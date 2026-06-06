"""Conversation history load/save helpers for AgentLoop."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from ..llms import ChatMessage
from ..runs.events import SEARCH_INDEX_MESSAGE_FAILED_EVENT
from ..search.base import SearchStore
from ..storage import StorageProvider, StoredMessage
from ..utils.log import logger


def _reasoning_details_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]] | None:
    details = metadata.get("llm_reasoning_details")
    return details if isinstance(details, list) else None


class MessageHistoryService:
    """Loads session history and persists messages with optional search indexing."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        search_store: SearchStore | None,
        max_history_getter: Callable[[], int],
        emit_index_failure: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self.storage = storage
        self.search_store = search_store
        self._max_history_getter = max_history_getter
        self._emit_index_failure = emit_index_failure

    async def load_history(self, session_id: str) -> list[ChatMessage]:
        """Load conversation history as ChatMessage objects for LLM consumption."""
        stored_messages = await self.storage.get_messages(
            session_id,
            limit=self._max_history_getter(),
        )

        chat_messages = []
        for message in stored_messages:
            if isinstance(message, dict):
                metadata = message.get("metadata", {}) if isinstance(message.get("metadata", {}), dict) else {}
                chat_messages.append(ChatMessage(
                    role=message.get("role", "?"),
                    content=message.get("content", ""),
                    reasoning_details=_reasoning_details_from_metadata(metadata),
                ))
            else:
                metadata = message.metadata if isinstance(message.metadata, dict) else {}
                chat_messages.append(ChatMessage(
                    role=message.role,
                    content=message.content,
                    reasoning_details=_reasoning_details_from_metadata(metadata),
                ))

        return chat_messages

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save one message to storage and index it when search is configured."""
        created_at = time.time()
        await self.storage.add_message(
            session_id,
            StoredMessage(
                role=role,
                content=content,
                timestamp=created_at,
                tool_name=tool_name,
                metadata=dict(metadata or {}),
            ),
        )
        if self.search_store is None:
            return

        try:
            await self.search_store.index_message(
                session_id=session_id,
                role=role,
                content=content,
                tool_name=tool_name,
                created_at=created_at,
            )
        except Exception as e:
            logger.warning("[{}] Failed to index message for search: {}", session_id, e)
            if self._emit_index_failure is not None:
                await self._emit_index_failure(
                    session_id,
                    SEARCH_INDEX_MESSAGE_FAILED_EVENT,
                    {
                        "role": role,
                        "tool_name": tool_name,
                        "content_len": len(str(content or "")),
                        "error": str(e),
                    },
                )


class HistoryResetService:
    """Clears session history and related per-session derived state."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        search_store: SearchStore | None,
        clear_session_artifacts: Callable[[str], Awaitable[None]],
    ):
        self.storage = storage
        self.search_store = search_store
        self._clear_session_artifacts = clear_session_artifacts

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
        await self._clear_session_artifacts(session_id)
        if self.search_store is None:
            return
        try:
            await self.search_store.clear_session(session_id)
        except Exception as e:
            logger.warning("[{}] Failed to clear search index: {}", session_id, e)
