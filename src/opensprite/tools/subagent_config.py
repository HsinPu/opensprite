"""Tool for safely creating and updating subagent prompt markdown files in the session workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..context.paths import (
    SUBAGENT_PROMPTS_DIRNAME,
    get_app_home,
    get_subagent_prompts_dir,
    sync_subagent_prompts_from_package,
)
from ..subagent_prompts import load_all_metadata, read_prompt_document
from ..subagent_profiles import allowed_tool_profile_names, validate_tool_profile_name
from .base import Tool
from .skill_config import (
    _validate_body_for_write,
    _validate_description_for_write,
    _validate_skill_id,
)

AGENT_PROMPT_GUIDE_SKILL_NAME = "agent-creator-design"

WorkspaceResolver = Callable[[], Path]

_CONFIGURE_SUBAGENT_RULES_SUMMARY = (
    "Writes go only under the current session workspace: <workspace>/subagent_prompts/<subagent_id>.md with YAML frontmatter "
    "(name must match subagent_id, description required, optional tool_profile) and a markdown body used as the subagent system prompt. "
    "list/get reflect merged ids (session overrides ~/.opensprite/subagent_prompts for the same id). "
    f"Follow read_skill + skill_name '{AGENT_PROMPT_GUIDE_SKILL_NAME}' for structure (Role, Task, Constraints, Output). "
    "Use action=add only for a brand-new id (no file in session and no prompt under app home for that id). "
    "If an id already exists under ~/.opensprite/subagent_prompts/, use upsert to add or replace the session file. "
    "remove deletes only the session file, never app-home defaults."
)


def _build_subagent_prompt_md(
    subagent_id: str,
    description: str,
    body: str,
    *,
    tool_profile: str | None = None,
) -> str:
    desc = (description or "").strip().replace("\n", " ").replace("\r", "")
    body_text = (body or "").strip()
    profile_line = f"tool_profile: {tool_profile}\n" if tool_profile else ""
    return f"---\nname: {subagent_id}\ndescription: {desc}\n{profile_line}---\n\n{body_text}\n"


def _classify_subagent_session_write(
    subagent_id: str,
    *,
    app_home: Path | None,
    session_workspace: Path,
) -> str | None:
    """Return 'session' if session file exists, 'global' if app-home has this prompt, None if net-new."""
    session_path = Path(session_workspace).expanduser() / SUBAGENT_PROMPTS_DIRNAME / f"{subagent_id}.md"
    if session_path.is_file():
        return "session"
    _, global_text = read_prompt_document(subagent_id, app_home=app_home, session_workspace=None)
    if global_text.strip():
        return "global"
    return None


class ConfigureSubagentTool(Tool):
    """Read and update subagent prompts under the session workspace ``subagent_prompts/``."""

    name = "configure_subagent"
    description = (
        "Inspect, add, update, or remove subagent prompt definitions for this chat session (one markdown file per id "
        "under the session workspace subagent_prompts/). "
        "Use this when the user wants a new delegate target or to change prompts instead of editing files manually. "
        + _CONFIGURE_SUBAGENT_RULES_SUMMARY
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "add", "upsert", "remove"],
                "description": (
                    "list: merged subagent ids and descriptions; get: read merged prompt for one id; "
                    "add: create a session file only for a net-new id (none in session and none under app home); "
                    "upsert: create or replace the session file (overrides app home when both exist); "
                    "remove: delete the session file only (does not remove ~/.opensprite/subagent_prompts/)."
                ),
            },
            "subagent_id": {
                "type": "string",
                "description": (
                    "Subagent id: must match the markdown filename without .md and the frontmatter name field. "
                    "Same format as skill_name (lowercase ASCII, letter-first, hyphens between segments). "
                    "Required for get, add, upsert, and remove."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "YAML frontmatter description for add and upsert: same quality rules as configure_skill "
                    f"(read_skill '{AGENT_PROMPT_GUIDE_SKILL_NAME}' for narrative structure; tool enforces length and English word counts)."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Markdown body for add and upsert (system prompt after frontmatter). "
                    "Same minimum length rules as configure_skill body."
                ),
            },
            "tool_profile": {
                "type": "string",
                "enum": allowed_tool_profile_names(),
                "description": (
                    "Optional runtime tool capability profile for add and upsert. "
                    "read-only: read/search only; research: read plus web; implementation: read/write/exec; "
                    "testing: read/write/exec with writes limited to test paths. If omitted, runtime falls back to the built-in id profile or read-only for custom ids."
                ),
            },
            "user_confirmed": {
                "type": "boolean",
                "description": (
                    "For action=add only, when creating a brand-new subagent id (no session file and no app-home prompt): "
                    "must be true after the user explicitly agreed in chat. False or omitted is rejected for that case. "
                    "Ignored for list, get, upsert, and remove."
                ),
            },
        },
        "required": ["action"],
    }

    def __init__(self, *, app_home: Path | None = None, workspace_resolver: WorkspaceResolver | None = None):
        self._app_home = app_home
        self._workspace_resolver = workspace_resolver

    def _session_prompts_root(self, session_workspace: Path) -> Path:
        return (Path(session_workspace).expanduser().resolve() / SUBAGENT_PROMPTS_DIRNAME)

    def _session_prompt_path(self, session_workspace: Path, subagent_id: str) -> Path:
        return self._session_prompts_root(session_workspace) / f"{subagent_id}.md"

    async def _execute(self, action: str, **kwargs: Any) -> str:
        if self._workspace_resolver is None:
            return "Error: configure_subagent requires a session workspace; workspace_resolver is not configured."

        home = get_app_home(self._app_home)
        sync_subagent_prompts_from_package(home)

        session_ws = Path(self._workspace_resolver()).expanduser().resolve()
        session_root = self._session_prompts_root(session_ws)

        if action == "list":
            meta = load_all_metadata(app_home=self._app_home, session_workspace=session_ws)
            payload = {
                "subagent_prompts_dir": str(session_root),
                "app_home_subagent_prompts_dir": str(get_subagent_prompts_dir(home)),
                "subagents": meta,
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        subagent_id = str(kwargs.get("subagent_id", "") or "").strip()
        err = _validate_skill_id(subagent_id)
        if err:
            return (
                err.replace("skill_name", "subagent_id")
                .replace("Skill name", "Subagent id")
                .replace("skill name", "subagent id")
            )

        if action == "get":
            path, text = read_prompt_document(subagent_id, app_home=self._app_home, session_workspace=session_ws)
            if not text:
                return f"Error: no prompt found for subagent_id '{subagent_id}'"
            payload = {
                "subagent_id": subagent_id,
                "resolved_path": str(path) if path else "",
                "content": text,
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        session_path = self._session_prompt_path(session_ws, subagent_id)

        if action == "remove":
            if not session_path.is_file():
                return (
                    f"Error: no session-managed prompt at {session_path}. "
                    "remove only deletes files under this session's subagent_prompts/; "
                    "it does not remove defaults under ~/.opensprite/subagent_prompts/."
                )
            session_path.unlink()
            return f"Removed session subagent prompt '{subagent_id}' at {session_path}."

        if action in {"add", "upsert"}:
            description = kwargs.get("description")
            body = kwargs.get("body")
            tool_profile = kwargs.get("tool_profile")
            desc_err = _validate_description_for_write(description, action=action)
            if desc_err:
                return desc_err
            body_err = _validate_body_for_write(body, action=action)
            if body_err:
                return body_err
            if tool_profile is not None:
                profile_err = validate_tool_profile_name(tool_profile)
                if profile_err:
                    return profile_err
                tool_profile = str(tool_profile).strip()

            where = _classify_subagent_session_write(subagent_id, app_home=self._app_home, session_workspace=session_ws)
            if action == "add":
                if where == "session":
                    return (
                        f"Error: subagent '{subagent_id}' already exists in this session at {session_path}. "
                        "Use action=upsert to replace it, or remove it first."
                    )
                if where == "global":
                    return (
                        f"Error: subagent '{subagent_id}' already exists under ~/.opensprite/subagent_prompts/. "
                        "Use action=upsert to write a session override in subagent_prompts/."
                    )
                if where is None and kwargs.get("user_confirmed") is not True:
                    return (
                        "Error: action=add for a new subagent_id requires user_confirmed=true in the tool arguments "
                        "after the user explicitly agreed in the conversation. Ask first; do not set true without consent."
                    )

            existed_session = session_path.is_file()
            session_root.mkdir(parents=True, exist_ok=True)
            content = _build_subagent_prompt_md(
                subagent_id,
                str(description),
                str(body),
                tool_profile=tool_profile,
            )
            session_path.write_text(content, encoding="utf-8")
            guide = (
                f" Next: ensure read_skill '{AGENT_PROMPT_GUIDE_SKILL_NAME}' was applied for structure and metadata; "
                "delegate will pick up this id after the next tool description refresh."
            )
            if action == "add":
                return f"Added session subagent prompt '{subagent_id}' at {session_path}.{guide}"
            label = "Updated" if existed_session else "Added"
            return f"{label} session subagent prompt '{subagent_id}' at {session_path}.{guide}"

        return f"Error: unsupported action '{action}'"
