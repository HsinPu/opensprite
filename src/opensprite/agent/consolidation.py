"""Conversation maintenance helpers for memory and profile updates."""

from __future__ import annotations

from typing import Any

from ..config.schema import MemoryLlmConfig
from ..documents.memory import MemoryStore, consolidate
from ..documents.user_profile import UserProfileConsolidator
from ..llms import LLMProvider
from ..storage import StorageProvider, StoredMessage
from ..utils import count_messages_tokens
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
        token_threshold: int = 0,
        memory_llm: MemoryLlmConfig | None = None,
    ):
        self.storage = storage
        self.memory_store = memory_store
        self.provider = provider
        self.threshold = threshold
        self.token_threshold = token_threshold
        self.memory_llm = memory_llm or MemoryLlmConfig()

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
        messages = await self.storage.get_messages(chat_id)
        message_count = len(messages)
        last_consolidated = await self.storage.get_consolidated_index(chat_id)
        pending_messages = self._to_message_dicts(messages[last_consolidated:])
        unconsolidated = len(pending_messages)
        pending_tokens = count_messages_tokens(pending_messages, model=self.provider.get_default_model()) if pending_messages else 0

        should_consolidate_by_count = self.threshold > 0 and unconsolidated >= self.threshold
        should_consolidate_by_tokens = self.token_threshold > 0 and pending_tokens >= self.token_threshold
        if not should_consolidate_by_count and not should_consolidate_by_tokens:
            return

        logger.info(
            f"[{chat_id}] memory.consolidate | pending_messages={unconsolidated} pending_tokens={pending_tokens} "
            f"threshold={self.threshold} token_threshold={self.token_threshold}"
        )
        try:
            success = await consolidate(
                memory_store=self.memory_store,
                chat_id=chat_id,
                messages=pending_messages,
                provider=self.provider,
                model=self.provider.get_default_model(),
                memory_llm=self.memory_llm,
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
        """Refresh this chat's USER.md managed block when enough new history exists."""
        if self.consolidator is None:
            return

        try:
            await self.consolidator.maybe_update(chat_id)
        except Exception as exc:
            logger.error(f"[{chat_id}] profile.update.error | error={exc}")


class RecentSummaryUpdateService:
    """Wrap optional RECENT_SUMMARY.md updates behind a stable interface."""

    def __init__(self, consolidator: Any | None = None):
        self.consolidator = consolidator

    async def maybe_update(self, chat_id: str) -> None:
        if self.consolidator is None:
            return

        try:
            await self.consolidator.maybe_update(chat_id)
        except Exception as exc:
            logger.error(f"[{chat_id}] recent_summary.update.error | error={exc}")
