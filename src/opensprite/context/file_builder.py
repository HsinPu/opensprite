"""
opensprite/context/file_builder.py - File-based ContextBuilder.

Assembles the system prompt from:
- bootstrap/*.md startup files
- workspace/chats/{session}/USER.md durable per-chat profile (beside skills/ and subagent_prompts/)
- global + per-chat skill metadata summaries (full skill content loads on demand)
- memory/<chat>/MEMORY.md long-term memory
"""

import platform
from pathlib import Path
from typing import Any

from .paths import (
    get_app_home,
    get_bootstrap_dir,
    get_chat_workspace,
    get_chat_skills_dir,
    get_memory_dir,
    get_memory_file,
    get_skills_dir,
    get_tool_workspace,
    get_user_profile_file,
    load_bootstrap_files,
)
from .runtime import RUNTIME_CONTEXT_TAG, build_runtime_context
from ..documents.memory import MemoryStore
from ..documents.recent_summary import RecentSummaryStore
from ..documents.user_profile import create_user_profile_store
from ..skills import SkillsLoader
from ..subagent_prompts import get_all_subagents


class FileContextBuilder:
    """Context builder backed by bootstrap files, skills, and memory."""

    BOOTSTRAP_FILES = ["SOUL.md", "IDENTITY.md", "AGENTS.md", "USER.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = RUNTIME_CONTEXT_TAG

    def _build_mcp_tools_summary(self) -> str:
        """Describe currently connected MCP tools for the main agent."""
        if not self._runtime_mcp_tools:
            return ""

        tool_lines = "\n".join(
            f"- `{name}`: {description}" for name, description in self._runtime_mcp_tools
        )
        return f"""# Available MCP Tools

These MCP tools are already connected and available through normal tool calling.

{tool_lines}
"""

    def _build_subagent_summary(self, chat_id: str) -> str:
        """Describe the available delegate prompt types for the main agent."""
        session_ws = self.get_chat_workspace(chat_id)
        subagents = get_all_subagents(self.app_home, session_workspace=session_ws)
        if not subagents:
            return ""

        subagent_lines = "\n".join(
            f"- `{name}`: {description}" for name, description in subagents.items()
        )
        return f"""# Available Subagents

Use `delegate` when a focused subproblem would benefit from a dedicated prompt.
Ids and descriptions below are **merged**: this chat's `subagent_prompts/<id>.md` overrides `~/.opensprite/subagent_prompts/<id>.md` when both exist. Use `configure_subagent` for adds and edits under this session's `subagent_prompts/`.

{subagent_lines}
"""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        app_home: Path | None = None,
        bootstrap_dir: Path | None = None,
        memory_dir: Path | None = None,
        tool_workspace: Path | None = None,
        skills_loader: SkillsLoader | None = None,
        default_skills_dir: Path | None = None,
        personal_skills_dir: Path | None = None,
        custom_skills_dir: Path | None = None,
    ):
        self.app_home = get_app_home(app_home)
        self.bootstrap_dir = Path(bootstrap_dir).expanduser() if bootstrap_dir else get_bootstrap_dir(self.app_home)
        self.memory_dir = Path(memory_dir).expanduser() if memory_dir else get_memory_dir(self.app_home)
        self.tool_workspace = (
            Path(tool_workspace).expanduser()
            if tool_workspace is not None
            else Path(workspace).expanduser() if workspace is not None else get_tool_workspace(self.app_home)
        )
        self.workspace = self.tool_workspace
        self.memory_store = MemoryStore(self.memory_dir)
        self.recent_summary_store = RecentSummaryStore(self.memory_dir)
        self._runtime_mcp_tools: list[tuple[str, str]] = []
        self.skills_loader = skills_loader or SkillsLoader(
            default_skills_dir=default_skills_dir or get_skills_dir(self.app_home),
            personal_skills_dir=personal_skills_dir,
            custom_skills_dir=custom_skills_dir,
        )

    def set_runtime_mcp_tools(self, tools: list[tuple[str, str]]) -> None:
        """Store the connected MCP tool summary for prompt generation."""
        self._runtime_mcp_tools = list(tools)

    def get_chat_workspace(self, chat_id: str = "default") -> Path:
        """Resolve the current chat's isolated workspace."""
        return get_chat_workspace(chat_id, workspace_root=self.tool_workspace)

    def get_chat_skills_dir(self, chat_id: str = "default") -> Path:
        """Resolve the personal skills directory for the current chat."""
        return get_chat_skills_dir(chat_id, workspace_root=self.tool_workspace)

    def get_user_profile_path(self, chat_id: str = "default") -> Path:
        """Resolve the USER.md path for the current user/session scope."""
        return get_user_profile_file(self.app_home, chat_id=chat_id, workspace_root=self.tool_workspace)

    def _read_user_profile(self, chat_id: str) -> str:
        """Load the current user/session profile text, creating it from the template when needed."""
        return create_user_profile_store(
            self.app_home,
            chat_id,
            bootstrap_dir=self.bootstrap_dir,
            workspace_root=self.tool_workspace,
        ).read_text()

    def build_system_prompt(self, chat_id: str = "default") -> str:
        """Build the system prompt from bootstrap files, skills, and memory."""
        parts = [self._build_session_context(chat_id)]

        bootstrap = load_bootstrap_files(self.bootstrap_dir)
        bootstrap["USER"] = self._read_user_profile(chat_id)
        for key, content in bootstrap.items():
            if content:
                section = content.strip()
                if section.startswith("#"):
                    parts.append(section)
                else:
                    parts.append(f"## {key}\n\n{section}")

        # Skills follow the on-demand model from OpenCode docs: list available
        # skill metadata in the main prompt, then load a full SKILL.md only when
        # the model decides a skill is relevant via read_skill.
        skills_summary = self.skills_loader.build_skills_summary(
            personal_skills_dir=self.get_chat_skills_dir(chat_id)
        )
        if skills_summary:
            parts.append(f"""# Available Skills

To use a skill, read its SKILL.md file using the read_skill tool.

{skills_summary}
""")

        subagent_summary = self._build_subagent_summary(chat_id)
        if subagent_summary:
            parts.append(subagent_summary)

        mcp_tools_summary = self._build_mcp_tools_summary()
        if mcp_tools_summary:
            parts.append(mcp_tools_summary)

        memory = self.memory_store.read(chat_id)
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        recent_summary = self.recent_summary_store.read(chat_id)
        if recent_summary:
            parts.append(f"# Recent Summary\n\n{recent_summary}")

        return "\n\n---\n\n".join(parts)

    def _build_session_context(self, chat_id: str) -> str:
        """Build the runtime session context block."""
        app_home_path = str(self.app_home.expanduser().resolve())
        bootstrap_path = str(self.bootstrap_dir.expanduser().resolve())
        workspace_path = str(self.get_chat_workspace(chat_id).expanduser().resolve())
        user_profile_path = str(self.get_user_profile_path(chat_id).expanduser().resolve())
        memory_path = str(get_memory_file(self.memory_dir, chat_id).expanduser().resolve())
        system = platform.system()
        runtime = f"{system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# Session Context

You are OpenSprite — operate as a **chief-of-staff / Jarvis-class** partner: omnicompetent within this workspace, proactive, economical with words, ruthless about correctness.

## Runtime
{runtime}

## App Paths
- App home: {app_home_path}
- Bootstrap files: {bootstrap_path}
- User profile: {user_profile_path}
- Long-term memory: {memory_path}

## Active Workspace
{workspace_path}
"""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build an untrusted runtime metadata block."""
        return build_runtime_context(channel=channel, chat_id=chat_id)

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        current_images: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        chat_id = chat_id or "default"

        if current_images:
            content: list[Any] = [{"type": "text", "text": current_message}]
            for image in current_images:
                content.append({"type": "image_url", "image_url": {"url": image}})
            user_message = {"role": "user", "content": content}
        else:
            user_message = {"role": "user", "content": current_message}

        return [
            {"role": "system", "content": self.build_system_prompt(chat_id)},
            *history,
            {"role": "user", "content": self._build_runtime_context(channel, chat_id)},
            user_message,
        ]

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": result,
            }
        )
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
        return messages
