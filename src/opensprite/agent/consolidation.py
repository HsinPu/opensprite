"""Conversation maintenance helpers for memory and profile updates."""

from __future__ import annotations

from typing import Any

from ..documents.memory import MemoryStore, consolidate
from ..documents.user_profile import UserProfileConsolidator
from ..llms import LLMProvider
from ..storage import StorageProvider, StoredMessage
from ..utils.log import logger


class MemoryConsolidationService:
    """Coordinate incremental long-term memory consolidation."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        memory_store: MemoryStore,
        provider: LLMProvider,
        threshold: int,
    ):
        self.storage = storage
        self.memory_store = memory_store
        self.provider = provider
        self.threshold = threshold

    @staticmethod
    def _to_message_dicts(messages: list[StoredMessage | dict[str, Any]]) -> list[dict[str, str]]:
        """Normalize stored messages for the memory consolidation prompt."""
        normalized: list[dict[str, str]] = []
        for message in messages:
            if isinstance(message, dict):
                normalized.append({
                    "role": message.get("role", "?"),
                    "content": message.get("content", ""),
                })
                continue

            normalized.append({
                "role": message.role,
                "content": message.content,
            })
        return normalized

    async def maybe_consolidate(self, chat_id: str) -> None:
        """Consolidate pending chat history into long-term memory when needed."""
        messages = await self.storage.get_messages(chat_id, limit=1000)
        message_count = len(messages)
        last_consolidated = await self.storage.get_consolidated_index(chat_id)
        unconsolidated = message_count - last_consolidated
        if unconsolidated < self.threshold:
            return

        logger.info(f"[{chat_id}] memory.consolidate | pending={unconsolidated}")
        try:
            success = await consolidate(
                memory_store=self.memory_store,
                chat_id=chat_id,
                messages=self._to_message_dicts(messages[last_consolidated:]),
                provider=self.provider,
                model=self.provider.get_default_model(),
            )
            if success:
                await self.storage.set_consolidated_index(chat_id, message_count)
                logger.info(f"[{chat_id}] memory.consolidated | total_messages={message_count}")
        except Exception as exc:
            logger.error(f"[{chat_id}] memory.consolidate.error | error={exc}")


class UserProfileUpdateService:
    """Wrap optional USER.md profile updates behind a stable interface."""

    def __init__(self, consolidator: UserProfileConsolidator | None = None):
        self.consolidator = consolidator

    async def maybe_update(self, chat_id: str) -> None:
        """Refresh the global USER.md managed block when enough new history exists."""
        if self.consolidator is None:
            return

        try:
            await self.consolidator.maybe_update(chat_id)
        except Exception as exc:
            logger.error(f"[{chat_id}] profile.update.error | error={exc}")
