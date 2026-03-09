"""Memory system for persistent agent memory (per-chat long-term)."""

import json
from pathlib import Path
from typing import Any

from minibot.utils.log import logger


# Tool definition for saving memory
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
                        "description": "Updated long-term memory as markdown. Include all existing facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["memory_update"],
            },
        },
    }
]


class MemoryStore:
    """
    Per-chat long-term memory stored in memory/{chat_id}/MEMORY.md.
    """

    def __init__(self, workspace: Path):
        self.memory_base = workspace / "memory"

    def _get_memory_file(self, chat_id: str) -> Path:
        """Get memory file path for specific chat."""
        chat_dir = self.memory_base / chat_id
        chat_dir.mkdir(parents=True, exist_ok=True)
        return chat_dir / "MEMORY.md"

    def read(self, chat_id: str) -> str:
        """Read long-term memory for a specific chat."""
        memory_file = self._get_memory_file(chat_id)
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return ""

    def write(self, chat_id: str, content: str) -> None:
        """Write long-term memory for a specific chat."""
        memory_file = self._get_memory_file(chat_id)
        memory_file.write_text(content, encoding="utf-8")

    def get_context(self, chat_id: str) -> str:
        """Get memory context for system prompt."""
        memory = self.read(chat_id)
        if memory:
            return f"# Long-term Memory\n\n{memory}"
        return ""

    async def consolidate(
        self,
        chat_id: str,
        messages: list[dict],
        provider: "LLMProvider",
        model: str,
    ) -> bool:
        """
        Consolidate old messages into memory via LLM.
        
        Args:
            chat_id: Chat ID for per-chat memory
            messages: List of conversation messages to process
            provider: LLM provider
            model: Model to use
            
        Returns:
            True on success, False on failure
        """
        if not messages:
            return True

        # Build prompt with messages
        lines = []
        for m in messages:
            if not m.get("content"):
                continue
            role = m.get("role", "?").upper()
            content = m.get("content", "")
            lines.append(f"[{role}]: {content}")

        current_memory = self.read(chat_id)
        prompt = f"""Process this conversation and call the save_memory tool with important information to remember.

Current memory:
{current_memory or "(empty)"}

Conversation:
{chr(10).join(lines[-20:])}  # Last 20 messages

Extract key facts, preferences, decisions, and important information. Update the memory accordingly."""

        try:
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent. Call the save_memory tool to update long-term memory with important information from the conversation."},
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

            if update := args.get("memory_update"):
                if update != current_memory:
                    self.write(chat_id, update)
                    logger.info("Memory consolidated for chat {}: {} chars", chat_id, len(update))

            return True
        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")
            return False
