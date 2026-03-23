"""File-based memory storage implementation."""

from pathlib import Path

from ..context.paths import get_legacy_memory_file, get_memory_file
from .base import MemoryStorage


class FileMemoryStorage(MemoryStorage):
    """
    File-based memory stored in a safe per-chat directory under memory/.

    Accepts either the memory directory itself or an app-home style base path
    that contains a `memory/` subdirectory.
    """

    def __init__(self, memory_dir: Path):
        base_path = Path(memory_dir).expanduser()
        self.memory_base = base_path if base_path.name == "memory" else base_path / "memory"
        self.memory_base.mkdir(parents=True, exist_ok=True)

    def _get_memory_file(self, chat_id: str) -> Path:
        """Get memory file path for specific chat."""
        memory_file = get_memory_file(self.memory_base, chat_id)
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        return memory_file

    def read(self, chat_id: str) -> str:
        """Read memory for a specific chat."""
        memory_file = self._get_memory_file(chat_id)
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        legacy_memory_file = get_legacy_memory_file(self.memory_base, chat_id)
        if legacy_memory_file.exists():
            return legacy_memory_file.read_text(encoding="utf-8")
        if chat_id == "default":
            legacy_memory_file = self.memory_base / "MEMORY.md"
            if legacy_memory_file.exists():
                return legacy_memory_file.read_text(encoding="utf-8")
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
