"""Assistant response finalization helpers for AgentLoop turns."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..bus.message import AssistantMessage
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
    ):
        self.run_trace = run_trace
        self._save_message = save_message
        self._format_log_preview = format_log_preview

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

        await self._save_message(session_id, "assistant", response, metadata=assistant_metadata)
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
