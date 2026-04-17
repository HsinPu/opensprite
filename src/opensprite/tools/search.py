"""Search tools for history and stored knowledge."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from ..search.base import SearchHit, SearchStore
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


def _truncate(text: str, limit: int = 240) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _format_time(created_at: float) -> str:
    if not created_at:
        return "unknown"
    return datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M")


class _BaseSearchTool(Tool):
    def __init__(self, store: SearchStore, get_chat_id: Callable[[], str | None], default_limit: int):
        self.store = store
        self.get_chat_id = get_chat_id
        self.default_limit = default_limit

    def _current_chat_id(self) -> str | None:
        return self.get_chat_id()

    def _missing_chat_response(self) -> str:
        return "Error: current chat_id is unavailable. Search tools require a chat-scoped conversation."


class SearchHistoryTool(_BaseSearchTool):
    @property
    def name(self) -> str:
        return "search_history"

    @property
    def description(self) -> str:
        return "Search saved conversation history for the current chat only."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for in this chat history", "pattern": NON_EMPTY_STRING_PATTERN},
                "limit": {"type": "integer", "description": "Maximum matches to return", "default": self.default_limit},
            },
            "required": ["query"],
        }

    async def _execute(self, query: str, limit: int | None = None, **kwargs: Any) -> str:
        query = query.strip()
        chat_id = self._current_chat_id()
        if not chat_id:
            return self._missing_chat_response()

        hits = await self.store.search_history(chat_id=chat_id, query=query, limit=limit or self.default_limit)
        if not hits:
            return f"No history matches found for '{query}' in this chat."

        return self._format_hits(query, hits)

    def _format_hits(self, query: str, hits: list[SearchHit]) -> str:
        lines = [f"History matches for: {query}"]
        for index, hit in enumerate(hits, 1):
            label = hit.role or "message"
            if hit.tool_name:
                label = f"{label}:{hit.tool_name}"
            lines.append(f"{index}. [{label}] {_format_time(hit.created_at)}")
            lines.append(f"   {_truncate(hit.content)}")
        return "\n".join(lines)


class SearchKnowledgeTool(_BaseSearchTool):
    @property
    def name(self) -> str:
        return "search_knowledge"

    @property
    def description(self) -> str:
        return "Search stored web search and web fetch results for the current chat only. Prefer this before repeating web_search or web_fetch on topics already researched in this chat."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for in saved web knowledge", "pattern": NON_EMPTY_STRING_PATTERN},
                "limit": {"type": "integer", "description": "Maximum matches to return", "default": self.default_limit},
                "source_type": {
                    "type": "string",
                    "description": "Optional source filter",
                    "enum": ["web_search", "web_fetch"],
                },
                "provider": {"type": "string", "description": "Optional provider filter"},
                "extractor": {"type": "string", "description": "Optional extractor filter"},
                "status": {"type": "integer", "description": "Optional HTTP status filter"},
                "content_type": {"type": "string", "description": "Optional content type filter"},
                "truncated": {"type": "boolean", "description": "Optional truncation filter"},
            },
            "required": ["query"],
        }

    async def _execute(
        self,
        query: str,
        limit: int | None = None,
        source_type: str | None = None,
        provider: str | None = None,
        extractor: str | None = None,
        status: int | None = None,
        content_type: str | None = None,
        truncated: bool | None = None,
        **kwargs: Any,
    ) -> str:
        query = query.strip()
        chat_id = self._current_chat_id()
        if not chat_id:
            return self._missing_chat_response()

        hits = await self.store.search_knowledge(
            chat_id=chat_id,
            query=query,
            limit=limit or self.default_limit,
            source_type=source_type,
            provider=provider,
            extractor=extractor,
            status=status,
            content_type=content_type,
            truncated=truncated,
        )
        if not hits:
            scope = source_type or provider or extractor or content_type or "web knowledge"
            return f"No {scope} matches found for '{query}' in this chat."

        return self._format_hits(query, hits)

    def _format_hits(self, query: str, hits: list[SearchHit]) -> str:
        lines = [f"Knowledge matches for: {query}"]
        for index, hit in enumerate(hits, 1):
            title = hit.title or hit.source_type
            lines.append(f"{index}. [{hit.source_type}] {title}")
            if hit.url:
                lines.append(f"   {hit.url}")
            if hit.query:
                lines.append(f"   query: {hit.query}")
            metadata = []
            if hit.provider:
                metadata.append(f"provider={hit.provider}")
            if hit.extractor:
                metadata.append(f"extractor={hit.extractor}")
            if hit.status is not None:
                metadata.append(f"status={hit.status}")
            if hit.content_type:
                metadata.append(f"content_type={hit.content_type}")
            if hit.truncated is not None:
                metadata.append(f"truncated={'yes' if hit.truncated else 'no'}")
            if metadata:
                lines.append(f"   {' | '.join(metadata)}")
            if hit.summary:
                lines.append(f"   summary: {_truncate(hit.summary, limit=120)}")
            lines.append(f"   {_truncate(hit.content)}")
        return "\n".join(lines)
