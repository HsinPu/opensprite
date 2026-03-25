"""Shared abstractions for document-backed context files."""

from abc import ABC, abstractmethod


class ConversationDocumentStore(ABC):
    """Abstract store for markdown documents derived from conversation history."""

    @abstractmethod
    def read(self, scope_id: str) -> str:
        """Read the document content for the given scope."""

    @abstractmethod
    def write(self, scope_id: str, content: str) -> None:
        """Write the document content for the given scope."""

    @abstractmethod
    def get_context(self, scope_id: str) -> str:
        """Return the document content formatted for prompt context."""


class IncrementalStateStore(ABC):
    """Persist per-scope progress for incremental consolidators."""

    @abstractmethod
    def get_processed_index(self, scope_id: str) -> int:
        """Return the last processed message index for a scope."""

    @abstractmethod
    def set_processed_index(self, scope_id: str, index: int) -> None:
        """Persist the last processed message index for a scope."""


class ConversationConsolidator(ABC):
    """Base protocol for background document updaters."""

    @abstractmethod
    async def maybe_update(self, scope_id: str) -> None:
        """Update the document for the given scope when thresholds are met."""
