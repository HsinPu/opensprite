"""Search store abstractions for per-chat retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchHit:
    """Single search result."""

    id: str
    chat_id: str
    source_type: str
    content: str
    created_at: float
    score: float | None = None
    role: str | None = None
    tool_name: str | None = None
    title: str | None = None
    url: str | None = None
    query: str | None = None


class SearchStore(ABC):
    """Abstract search index used for per-chat retrieval."""

    @abstractmethod
    async def sync_from_storage(self, storage: "StorageProvider") -> None:
        """Backfill any missing records from persistent storage."""

    @abstractmethod
    async def index_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        created_at: float | None = None,
    ) -> None:
        """Index one conversation message for history search."""

    @abstractmethod
    async def index_tool_result(
        self,
        chat_id: str,
        tool_name: str,
        tool_args: dict,
        result: str,
        created_at: float | None = None,
    ) -> None:
        """Index structured tool results for knowledge search."""

    @abstractmethod
    async def search_history(self, chat_id: str, query: str, limit: int = 5) -> list[SearchHit]:
        """Search conversation history within a single chat."""

    @abstractmethod
    async def search_knowledge(
        self,
        chat_id: str,
        query: str,
        limit: int = 5,
        source_type: str | None = None,
    ) -> list[SearchHit]:
        """Search stored knowledge within a single chat."""

    @abstractmethod
    async def clear_chat(self, chat_id: str) -> None:
        """Remove all indexed data for a chat."""


from ..storage.base import StorageProvider
