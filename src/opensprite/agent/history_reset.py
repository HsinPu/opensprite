"""Conversation reset helpers across storage, documents, and search index."""

from __future__ import annotations

from typing import Callable

from ..search.base import SearchStore
from ..storage import StorageProvider
from ..utils.log import logger


class HistoryResetService:
    """Clears session history and related per-session derived state."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        search_store: SearchStore | None,
        clear_active_task: Callable[[str], None],
        clear_recent_summary: Callable[[str], None],
    ):
        self.storage = storage
        self.search_store = search_store
        self._clear_active_task = clear_active_task
        self._clear_recent_summary = clear_recent_summary

    async def reset(self, session_id: str | None = None) -> None:
        """Clear one session or all sessions from storage and derived indexes."""
        if session_id:
            await self._clear_one(session_id)
            return

        all_sessions = await self.storage.get_all_sessions()
        for current_session_id in all_sessions:
            await self._clear_one(current_session_id)

    async def _clear_one(self, session_id: str) -> None:
        await self.storage.clear_messages(session_id)
        self._clear_active_task(session_id)
        self._clear_recent_summary(session_id)
        if self.search_store is None:
            return
        try:
            await self.search_store.clear_session(session_id)
        except Exception as e:
            logger.warning("[{}] Failed to clear search index: {}", session_id, e)
