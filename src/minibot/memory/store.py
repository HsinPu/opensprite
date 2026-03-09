"""File-based memory storage implementation."""

from pathlib import Path

from minibot.memory.base import MemoryStorage


class FileMemoryStorage(MemoryStorage):
    """
    File-based memory stored in memory/{chat_id}/MEMORY.md.
    """

    def __init__(self, workspace: Path):
        self.memory_base = workspace / "memory"

    def _get_memory_file(self, chat_id: str) -> Path:
        """Get memory file path for specific chat."""
        chat_dir = self.memory_base / chat_id
        chat_dir.mkdir(parents=True, exist_ok=True)
        return chat_dir / "MEMORY.md"

    def read(self, chat_id: str) -> str:
        """Read memory for a specific chat."""
        memory_file = self._get_memory_file(chat_id)
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return ""

    def write(self, chat_id: str, content: str) -> None:
        """Write memory for a specific chat."""
        memory_file = self._get_memory_file(chat_id)
        memory_file.write_text(content, encoding="utf-8")

    def get_context(self, chat_id: str) -> str:
        """Get memory context for system prompt."""
        memory = self.read(chat_id)
        if memory:
            return f"# Long-term Memory\n\n{memory}"
        return ""


# Alias for backward compatibility
MemoryStore = FileMemoryStorage
