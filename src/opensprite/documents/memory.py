"""Per-chat long-term memory document store and consolidator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..context.paths import get_memory_file
from ..utils.log import logger
from .base import ConversationDocumentStore


class MemoryDocumentStore(ConversationDocumentStore):
    """File-based per-chat markdown memory stored under the memory directory."""

    def __init__(self, memory_dir: Path):
        base_path = Path(memory_dir).expanduser()
        self.memory_base = base_path if base_path.name == "memory" else base_path / "memory"
        self.memory_base.mkdir(parents=True, exist_ok=True)

    def _get_memory_file(self, chat_id: str) -> Path:
        memory_file = get_memory_file(self.memory_base, chat_id)
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        return memory_file

    def read(self, chat_id: str) -> str:
        memory_file = self._get_memory_file(chat_id)
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        if chat_id == "default":
            legacy_memory_file = self.memory_base / "MEMORY.md"
            if legacy_memory_file.exists():
                return legacy_memory_file.read_text(encoding="utf-8")
        return ""

    def write(self, chat_id: str, content: str) -> None:
        memory_file = self._get_memory_file(chat_id)
        memory_file.write_text(content, encoding="utf-8")

    def get_context(self, chat_id: str) -> str:
        memory = self.read(chat_id)
        if memory:
            return f"# Long-term Memory\n\n{memory}"
        return ""


FileMemoryStorage = MemoryDocumentStore
MemoryStore = MemoryDocumentStore


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save important information to long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_update": {
                        "type": "string",
                        "description": (
                            "Updated long-term memory as markdown. Include all existing facts plus new ones. "
                            "Return unchanged if nothing new."
                        ),
                    },
                },
                "required": ["memory_update"],
            },
        },
    }
]


async def consolidate(
    memory_store: MemoryStore,
    chat_id: str,
    messages: list[dict[str, Any]],
    provider,
    model: str,
) -> bool:
    """Consolidate old messages into per-chat memory via the active LLM."""
    if not messages:
        return True

    lines: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            content = message.get("content", "")
            role = message.get("role", "?").upper()
        else:
            content = getattr(message, "content", "")
            role = getattr(message, "role", "?").upper()

        if not content:
            continue
        lines.append(f"[{role}]: {content}")

    current_memory = memory_store.read(chat_id)
    prompt = f"""Process this conversation and call the save_memory tool with important information to remember.

Current memory:
{current_memory or "(empty)"}

Conversation:
{chr(10).join(lines[-20:])}  # Last 20 messages

Extract key facts, preferences, decisions, and important information. Update the memory accordingly."""

    try:
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a memory consolidation agent. Call the save_memory tool to update "
                        "long-term memory with important information from the conversation."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=_SAVE_MEMORY_TOOL,
            model=model,
        )

        if not response.tool_calls:
            logger.warning("Memory consolidation: LLM did not call save_memory")
            return False

        args = response.tool_calls[0].arguments
        if isinstance(args, str):
            args = json.loads(args)

        update = args.get("memory_update")
        if update and update != current_memory:
            memory_store.write(chat_id, update)
            logger.info("Memory consolidated for chat {}: {} chars", chat_id, len(update))

        return True
    except Exception as exc:
        import traceback

        logger.error(f"Memory consolidation failed: {exc}\n{traceback.format_exc()}")
        return False
