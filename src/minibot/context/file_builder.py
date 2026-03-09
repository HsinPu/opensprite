"""
minibot/context/file_builder.py - 檔案式 ContextBuilder 實作

從 workspace 檔案組裝 system prompt：
- IDENTITY.md（身份設定）
- AGENTS.md、SOUL.md、USER.md、TOOLS.md（啟動檔案）
- memory/{chat_id}/MEMORY.md（長期記憶）
"""

import platform
from datetime import datetime
from pathlib import Path
from typing import Any

from minibot.context.workspace import load_bootstrap_files
from minibot.memory import MemoryStore


class FileContextBuilder:
    """
    Context builder that reads from workspace files.
    
    Assembles system prompt from:
    - Identity (IDENTITY.md)
    - Bootstrap files (AGENTS.md, SOUL.md, USER.md, TOOLS.md)
    - Memory (memory/{chat_id}/MEMORY.md)
    """
    
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "IDENTITY.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_store = MemoryStore(workspace)
    
    def build_system_prompt(self, chat_id: str = "default") -> str:
        """Build system prompt from workspace files."""
        parts = [self._get_identity()]
        
        # Load bootstrap files
        bootstrap = load_bootstrap_files(self.workspace)
        for key, content in bootstrap.items():
            if content:
                parts.append(f"## {key}\n\n{content}")
        
        # Load per-chat memory
        memory = self.memory_store.read(chat_id)
        if memory:
            parts.append(f"# Memory\n\n{memory}")
        
        return "\n\n---\n\n".join(parts)
    
    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{system} {platform.machine()}, Python {platform.python_version()}"
        
        return f"""# mini-bot 🤖

You are mini-bot, a helpful AI assistant.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)

## Guidelines
- Be helpful and concise.
- When in doubt, ask for clarification.
"""
    
    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build untrusted runtime metadata block."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = platform.time() if hasattr(platform, 'time') else "Local"
        lines = [f"Current Time: {now}"]
        
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        
        return FileContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build complete message list for LLM call."""
        chat_id = chat_id or "default"
        return [
            {"role": "system", "content": self.build_system_prompt(chat_id)},
            *history,
            {"role": "user", "content": self._build_runtime_context(channel, chat_id)},
            {"role": "user", "content": current_message},
        ]
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict[str, Any]]:
        """Add tool result to message list."""
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Add assistant message to message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        messages.append(msg)
        return messages
