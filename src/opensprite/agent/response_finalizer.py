"""Assistant response finalization helpers for AgentLoop turns."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from ..bus.message import AssistantMessage
from ..config import LogConfig
from ..utils.log import logger
from .run_trace import RunTraceRecorder


class AgentResponseFinalizer:
    """Persists assistant replies, completes runs, and builds outbound messages."""

    def __init__(
        self,
        *,
        run_trace: RunTraceRecorder,
        save_message: Callable[..., Awaitable[None]],
        format_log_preview: Callable[..., str],
        log_config: LogConfig | None = None,
    ):
        self.run_trace = run_trace
        self._save_message = save_message
        self._format_log_preview = format_log_preview
        self.log_config = log_config or LogConfig()

    @staticmethod
    def _reasoning_text_size(value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, dict):
            return sum(AgentResponseFinalizer._reasoning_text_size(item) for item in value.values())
        if isinstance(value, list):
            return sum(AgentResponseFinalizer._reasoning_text_size(item) for item in value)
        return 0

    @staticmethod
    def _reasoning_type_summary(details: list[Any]) -> str:
        counts: dict[str, int] = {}
        for item in details:
            item_type = item.get("type") if isinstance(item, dict) else type(item).__name__
            key = str(item_type or "unknown")
            counts[key] = counts.get(key, 0) + 1
        return ", ".join(f"{key}:{counts[key]}" for key in sorted(counts)) or "none"

    def _log_reasoning_details(self, session_id: str, metadata: dict[str, Any]) -> None:
        details = metadata.get("llm_reasoning_details")
        if not isinstance(details, list) or not details:
            return

        logger.info(
            "[{}] LLM reasoning summary | details={} chars={} types={}",
            session_id,
            len(details),
            self._reasoning_text_size(details),
            self._reasoning_type_summary(details),
        )
        if not self.log_config.log_reasoning_details:
            return

        logger.info(
            "[{}] LLM reasoning details | {}",
            session_id,
            json.dumps(details, ensure_ascii=False, default=str),
        )

    def _log_outbound(
        self,
        session_id: str,
        response: str,
        *,
        prefix: str = "",
    ) -> None:
        logger.info(
            f"[{session_id}] outbound | {prefix}text={self._format_log_preview(response, max_chars=200)}"
        )

    async def finalize(
        self,
        *,
        session_id: str,
        run_id: str,
        response: str,
        channel: str | None,
        external_chat_id: str | None,
        assistant_metadata: dict[str, Any],
        run_part_metadata: dict[str, Any],
        run_event_payload: dict[str, Any],
        persisted_assistant_metadata: dict[str, Any] | None = None,
        status_metadata: dict[str, Any] | None = None,
        images: list[str] | None = None,
        voices: list[str] | None = None,
        audios: list[str] | None = None,
        videos: list[str] | None = None,
        log_prefix: str = "",
        log_before_record: bool = False,
        after_save: Callable[[], Awaitable[None]] | None = None,
    ) -> AssistantMessage:
        """Finalize a visible assistant response for one user turn."""
        if log_before_record:
            self._log_outbound(session_id, response, prefix=log_prefix)

        await self.run_trace.record_assistant_message_part(
            session_id,
            run_id,
            response,
            metadata=run_part_metadata,
        )

        if not log_before_record:
            self._log_outbound(session_id, response, prefix=log_prefix)

        persisted_metadata = persisted_assistant_metadata if persisted_assistant_metadata is not None else assistant_metadata
        self._log_reasoning_details(session_id, persisted_metadata)

        await self._save_message(
            session_id,
            "assistant",
            response,
            metadata=persisted_metadata,
        )
        if after_save is not None:
            await after_save()

        await self.run_trace.complete_run(
            session_id,
            run_id,
            event_payload=run_event_payload,
            status_metadata=status_metadata,
            channel=channel,
            external_chat_id=external_chat_id,
        )

        return AssistantMessage(
            text=response,
            channel=channel or "unknown",
            external_chat_id=external_chat_id,
            session_id=session_id,
            images=images,
            voices=voices,
            audios=audios,
            videos=videos,
            metadata=assistant_metadata,
        )
