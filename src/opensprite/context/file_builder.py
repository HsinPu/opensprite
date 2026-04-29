"""
opensprite/context/file_builder.py - File-based ContextBuilder.

Assembles the system prompt from:
- bootstrap/*.md startup files
- workspace/chats/{session}/USER.md durable per-session profile (beside skills/ and subagent_prompts/)
- global + per-session skill metadata summaries (full skill content loads on demand)
- memory/<session>/MEMORY.md long-term memory
"""

import platform
import re
from pathlib import Path
from typing import Any

from .paths import (
    get_active_task_file,
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
from ..planning_mode import resolve_planning_mode
from ..documents.memory import MemoryStore
from ..documents.recent_summary import RecentSummaryStore
from ..documents.user_profile import create_user_profile_store
from ..skills import SkillsLoader
from ..subagent_prompts import get_all_subagents


class FileContextBuilder:
    """Context builder backed by bootstrap files, skills, and memory."""

    BOOTSTRAP_FILES = ["IDENTITY.md", "SOUL.md", "AGENTS.md", "TOOLS.md", "USER.md"]
    _RUNTIME_CONTEXT_TAG = RUNTIME_CONTEXT_TAG
    _WORKSPACE_TASK_WORD_KEYWORDS = (
        "agent",
        "api",
        "bug",
        "build",
        "cli",
        "code",
        "commit",
        "config",
        "debug",
        "diff",
        "error",
        "exception",
        "fail",
        "fix",
        "function",
        "git",
        "lint",
        "merge",
        "migration",
        "module",
        "package.json",
        "patch",
        "pytest",
        "refactor",
        "repo",
        "repository",
        "stack trace",
        "test",
        "traceback",
        "typescript",
    )
    _WORKSPACE_TASK_TEXT_MARKERS = (
        "package.json",
        "修復",
        "偵錯",
        "報錯",
        "專案",
        "建置",
        "測試",
        "程式",
        "程式碼",
        "編譯",
        "設定",
        "錯誤",
        "重構",
    )
    _WORKSPACE_TASK_WORD_PATTERN = re.compile(
        r"\b(?:" + "|".join(re.escape(keyword) for keyword in _WORKSPACE_TASK_WORD_KEYWORDS) + r")\b",
        re.IGNORECASE,
    )
    _WORKSPACE_PATH_PATTERN = re.compile(
        r"(?:^|\s)(?:[\w.-]+[\\/])+[\w.-]+|(?:^|\s)[\w.-]+\.(?:py|js|ts|tsx|jsx|vue|json|toml|yaml|yml|md|css|html|java|go|rs|sql)(?:\s|$)",
        re.IGNORECASE,
    )

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

    def _build_subagent_summary(self, session_id: str) -> str:
        """Describe the available delegate prompt types for the main agent."""
        session_ws = self.get_chat_workspace(session_id)
        subagents = get_all_subagents(self.app_home, session_workspace=session_ws)
        if not subagents:
            return ""

        subagent_lines = "\n".join(
            f"- `{name}`: {description}" for name, description in subagents.items()
        )
        return f"""# Available Subagents

Use `delegate` when a focused subproblem would benefit from a dedicated prompt.
Ids and descriptions below are **merged**: this session's `subagent_prompts/<id>.md` overrides `~/.opensprite/subagent_prompts/<id>.md` when both exist. Use `configure_subagent` for adds and edits under this session's `subagent_prompts/`.

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
        """Resolve the current session's isolated workspace."""
        return get_chat_workspace(chat_id, workspace_root=self.tool_workspace)

    def get_chat_skills_dir(self, chat_id: str = "default") -> Path:
        """Resolve the personal skills directory for the current session."""
        return get_chat_skills_dir(chat_id, workspace_root=self.tool_workspace)

    def get_user_profile_path(self, chat_id: str = "default") -> Path:
        """Resolve the USER.md path for the current user/session scope."""
        return get_user_profile_file(self.app_home, chat_id=chat_id, workspace_root=self.tool_workspace)

    def get_active_task_path(self, chat_id: str = "default") -> Path:
        """Resolve the ACTIVE_TASK.md path for the current session scope."""
        return get_active_task_file(self.app_home, chat_id=chat_id, workspace_root=self.tool_workspace)

    def get_workspace_agents_path(self, chat_id: str = "default") -> Path:
        """Resolve the current workspace's AGENTS.md instructions path."""
        return self.get_chat_workspace(chat_id) / "AGENTS.md"

    def _read_user_profile(self, session_id: str) -> str:
        """Load the current user/session profile text, creating it from the template when needed."""
        return create_user_profile_store(
            self.app_home,
            session_id,
            bootstrap_dir=self.bootstrap_dir,
            workspace_root=self.tool_workspace,
        ).read_text()

    def _read_active_task(self, session_id: str) -> str:
        """Load the current session's active task context when present."""
        from ..documents.active_task import create_active_task_store, build_active_task_execution_guidance

        store = create_active_task_store(
            self.app_home,
            session_id,
            workspace_root=self.tool_workspace,
        )
        task_context = store.get_context(session_id)
        if not task_context:
            return ""
        return f"{task_context}\n\n---\n\n{build_active_task_execution_guidance(store.read_managed_block())}"

    def _read_workspace_agents(self, session_id: str) -> str:
        """Load AGENTS.md from the active workspace when present."""
        agents_path = self.get_workspace_agents_path(session_id)
        if not agents_path.is_file():
            return ""
        content = agents_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        return f"# Workspace AGENTS.md\n\nLoaded from: `{agents_path.expanduser().resolve()}`\n\n{content}"

    @classmethod
    def _looks_like_workspace_task(cls, current_message: str) -> bool:
        """Heuristically detect code/project tasks without making every chat coding-first."""
        text = str(current_message or "").strip()
        if not text:
            return False
        lowered = text.lower()
        if "```" in text or cls._WORKSPACE_PATH_PATTERN.search(text):
            return True
        return bool(cls._WORKSPACE_TASK_WORD_PATTERN.search(text)) or any(
            marker in lowered for marker in cls._WORKSPACE_TASK_TEXT_MARKERS
        )

    @classmethod
    def _build_workspace_task_guidance(cls, current_message: str) -> str:
        """Return current-turn guidance only when the user appears to ask for workspace work."""
        if not cls._looks_like_workspace_task(current_message):
            return ""
        return """# Workspace Task Guidance

This request appears to be a workspace or project task. Use the active workspace autonomously: inspect relevant files and search results first, edit directly when the path forward is clear, run focused verification when feasible, then summarize the changes, verification result, and any remaining risk.
"""

    def build_system_prompt(self, session_id: str = "default") -> str:
        """Build the system prompt from bootstrap files, skills, and memory."""
        parts = [self._build_session_context(session_id)]

        bootstrap = load_bootstrap_files(self.bootstrap_dir)
        bootstrap["USER"] = self._read_user_profile(session_id)
        for key, content in bootstrap.items():
            if content:
                section = content.strip()
                if section.startswith("#"):
                    parts.append(section)
                else:
                    parts.append(f"## {key}\n\n{section}")

        workspace_agents = self._read_workspace_agents(session_id)
        if workspace_agents:
            parts.append(workspace_agents)

        active_task = self._read_active_task(session_id)
        if active_task:
            parts.append(active_task)

        # Skills follow the on-demand model from OpenCode docs: list available
        # skill metadata in the main prompt, then load a full SKILL.md only when
        # the model decides a skill is relevant via read_skill.
        skills_summary = self.skills_loader.build_skills_summary(
            personal_skills_dir=self.get_chat_skills_dir(session_id)
        )
        if skills_summary:
            parts.append(f"""# Available Skills

To use a skill, read its SKILL.md file using the read_skill tool.

{skills_summary}
""")

        subagent_summary = self._build_subagent_summary(session_id)
        if subagent_summary:
            parts.append(subagent_summary)

        mcp_tools_summary = self._build_mcp_tools_summary()
        if mcp_tools_summary:
            parts.append(mcp_tools_summary)

        memory = self.memory_store.read(session_id)
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        recent_summary = self.recent_summary_store.read(session_id)
        if recent_summary:
            parts.append(f"# Recent Summary\n\n{recent_summary}")

        return "\n\n---\n\n".join(parts)

    def _build_session_context(self, session_id: str) -> str:
        """Build the runtime session context block."""
        app_home_path = str(self.app_home.expanduser().resolve())
        bootstrap_path = str(self.bootstrap_dir.expanduser().resolve())
        workspace_path = str(self.get_chat_workspace(session_id).expanduser().resolve())
        user_profile_path = str(self.get_user_profile_path(session_id).expanduser().resolve())
        active_task_path = str(self.get_active_task_path(session_id).expanduser().resolve())
        memory_path = str(get_memory_file(self.memory_dir, session_id).expanduser().resolve())
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
        - Active task: {active_task_path}
        - Long-term memory: {memory_path}

## Active Workspace
{workspace_path}

## Workspace Operating Policy
Treat the active workspace as trusted working context. For workspace-local tasks, proceed without asking first when you need to read files, search files, edit files, apply patches, run focused tests, build, or verify results. Use the normal inspect -> edit -> verify -> summarize loop for code and project work.

Be conservative only for actions with external side effects or boundaries outside the active workspace, such as sending messages, scheduling jobs, using external MCP services, network operations, credential handling, or modifying OpenSprite runtime configuration.
"""

    @staticmethod
    def _build_runtime_context(channel: str | None, session_id: str | None) -> str:
        """Build an untrusted runtime metadata block."""
        return build_runtime_context(channel=channel, session_id=session_id)

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        current_images: list[str] | None = None,
        channel: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        session_id = session_id or "default"

        if current_images:
            content: list[Any] = [{"type": "text", "text": current_message}]
            for image in current_images:
                content.append({"type": "image_url", "image_url": {"url": image}})
            user_message = {"role": "user", "content": content}
        else:
            user_message = {"role": "user", "content": current_message}

        messages = [{"role": "system", "content": self.build_system_prompt(session_id)}]
        workspace_task_guidance = self._build_workspace_task_guidance(current_message)
        if workspace_task_guidance:
            messages.append({"role": "system", "content": workspace_task_guidance})
        planning_mode_guidance = resolve_planning_mode(current_message).overlay
        if planning_mode_guidance:
            messages.append({"role": "system", "content": planning_mode_guidance})
        messages.extend(history)
        messages.extend([
            {"role": "user", "content": self._build_runtime_context(channel, session_id)},
            user_message,
        ])
        return messages

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
