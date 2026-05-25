"""Search tools for session history."""

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


class SearchHistoryTool(Tool):
    def __init__(self, store: SearchStore, get_session_id: Callable[[], str | None], default_limit: int):
        self.store = store
        self.get_session_id = get_session_id
        self.default_limit = default_limit

    def _current_session_id(self) -> str | None:
        return self.get_session_id()

    def _missing_chat_response(self) -> str:
        return "Error: current session_id is unavailable. Search tools require a session-scoped conversation."

    @property
    def name(self) -> str:
        return "search_history"

    @property
    def description(self) -> str:
        return (
            "Search saved conversation history for the current session only. Prefer this before asking the user "
            "to restate earlier chat details, and use it for prior decisions, commands, errors, task outcomes, "
            "or transcript-specific facts that should not be copied into MEMORY.md."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for in this session history", "pattern": NON_EMPTY_STRING_PATTERN},
                "limit": {"type": "integer", "description": "Maximum matches to return", "default": self.default_limit},
            },
            "required": ["query"],
        }

    async def _execute(self, query: str, limit: int | None = None, **kwargs: Any) -> str:
        query = query.strip()
        session_id = self._current_session_id()
        if not session_id:
            return self._missing_chat_response()

        hits = await self.store.search_history(session_id=session_id, query=query, limit=limit or self.default_limit)
        if not hits:
            return f"No history matches found for '{query}' in this session."

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
