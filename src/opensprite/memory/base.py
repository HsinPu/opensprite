"""Base interface for memory storage."""

from abc import ABC, abstractmethod
from pathlib import Path


class MemoryStorage(ABC):
    """
    Abstract base class for memory storage.
    
    Implement this to support different backends (file, database, etc.)
    """
    
    @abstractmethod
    def read(self, chat_id: str) -> str:
        """Read memory for a specific chat."""
        pass
    
    @abstractmethod
    def write(self, chat_id: str, content: str) -> None:
        """Write memory for a specific chat."""
        pass
    
    @abstractmethod
    def get_context(self, chat_id: str) -> str:
        """Get memory context for system prompt."""
        pass
