"""
opensprite/context/file_builder.py - File-based ContextBuilder.

Assembles the system prompt from:
- bootstrap/*.md startup files
- skill metadata summaries (full skill content loads on demand)
- memory/<chat>/MEMORY.md long-term memory
"""

import platform
from pathlib import Path
from typing import Any

from .paths import (
    get_app_home,
    get_bootstrap_dir,
    get_chat_workspace,
    get_memory_dir,
    get_memory_file,
    get_skills_dir,
    get_tool_workspace,
    load_bootstrap_files,
)
from .runtime import RUNTIME_CONTEXT_TAG, build_runtime_context
from ..documents.memory import MemoryStore
from ..skills import SkillsLoader


class FileContextBuilder:
    """Context builder backed by bootstrap files, skills, and memory."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "IDENTITY.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = RUNTIME_CONTEXT_TAG

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
        self.skills_loader = skills_loader or SkillsLoader(
            workspace=self.tool_workspace,
            default_skills_dir=default_skills_dir or get_skills_dir(self.app_home),
            custom_skills_dir=custom_skills_dir or self.tool_workspace / "skills",
        )

    def get_chat_workspace(self, chat_id: str = "default") -> Path:
        """Resolve the current chat's isolated workspace."""
        return get_chat_workspace(chat_id, workspace_root=self.tool_workspace)

    def build_system_prompt(self, chat_id: str = "default") -> str:
        """Build the system prompt from bootstrap files, skills, and memory."""
        parts = [self._get_identity(chat_id)]

        bootstrap = load_bootstrap_files(self.bootstrap_dir)
        for key, content in bootstrap.items():
            if content:
                parts.append(f"## {key}\n\n{content}")

        # Skills follow the on-demand model from OpenCode docs: list available
        # skill metadata in the main prompt, then load a full SKILL.md only when
        # the model decides a skill is relevant via read_skill.
        skills_summary = self.skills_loader.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Available Skills

To use a skill, read its SKILL.md file using the read_skill tool.

{skills_summary}
""")

        memory = self.memory_store.read(chat_id)
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, chat_id: str) -> str:
        """Get the core identity section."""
        app_home_path = str(self.app_home.expanduser().resolve())
        bootstrap_path = str(self.bootstrap_dir.expanduser().resolve())
        workspace_path = str(self.get_chat_workspace(chat_id).expanduser().resolve())
        memory_path = str(get_memory_file(self.memory_dir, chat_id).expanduser().resolve())
        system = platform.system()
        runtime = f"{system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# OpenSprite

You are OpenSprite, a helpful AI assistant.

## Runtime
{runtime}

## App Home
Your app home is at: {app_home_path}
- Bootstrap files: {bootstrap_path}
- Long-term memory: {memory_path}

## Workspace
Your file workspace is at: {workspace_path}

## Guidelines
- Be helpful and concise.
- When in doubt, ask for clarification.
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
