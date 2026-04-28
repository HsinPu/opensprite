"""Run trace persistence and event publishing helpers."""

from __future__ import annotations

import time
from typing import Any, Callable

from ..bus.events import RunEvent
from ..storage import StorageProvider
from ..utils.json_safe import json_safe_payload
from ..utils.log import logger


RUN_PART_CONTENT_MAX_CHARS = 20_000


def truncate_run_part_content(
    content: str,
    max_chars: int = RUN_PART_CONTENT_MAX_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Bound durable run-part content while preserving useful head/tail context."""
    text = str(content or "")
    original_len = len(text)
    if original_len <= max_chars:
        return text, {"content_truncated": False, "content_original_len": original_len}

    marker = f"\n... (run part content truncated, original {original_len} chars) ...\n"
    tail_chars = max(1000, max_chars // 4)
    head_chars = max(0, max_chars - tail_chars - len(marker))
    truncated = text[:head_chars].rstrip() + marker + text[-tail_chars:].lstrip()
    return truncated, {"content_truncated": True, "content_original_len": original_len}


class RunTraceRecorder:
    """Small service for durable run lifecycle, events, and ordered parts."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        message_bus_getter: Callable[[], Any | None],
    ):
        self.storage = storage
        self._message_bus_getter = message_bus_getter

    async def create_run(
        self,
        chat_id: str,
        run_id: str,
        *,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create a durable run record when the configured storage supports it."""
        creator = getattr(self.storage, "create_run", None)
        if not callable(creator):
            return
        try:
            await creator(chat_id, run_id, status=status, metadata=metadata)
        except Exception as e:
            logger.warning("[{}] run.create.failed | run_id={} error={}", chat_id, run_id, e)

    async def update_run_status(
        self,
        chat_id: str,
        run_id: str,
        status: str,
        *,
        metadata: dict[str, Any] | None = None,
        finished_at: float | None = None,
    ) -> None:
        """Update a durable run record when the configured storage supports it."""
        updater = getattr(self.storage, "update_run_status", None)
        if not callable(updater):
            return
        try:
            await updater(chat_id, run_id, status, metadata=metadata, finished_at=finished_at)
        except Exception as e:
            logger.warning("[{}] run.update.failed | run_id={} status={} error={}", chat_id, run_id, status, e)

    async def add_part(
        self,
        chat_id: str,
        run_id: str,
        part_type: str,
        *,
        content: str = "",
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist one ordered run artifact when the storage supports it."""
        add_part = getattr(self.storage, "add_run_part", None)
        if not callable(add_part):
            return
        try:
            stored_content, content_metadata = truncate_run_part_content(str(content or ""))
            safe_metadata = json_safe_payload(metadata)
            safe_metadata.update(content_metadata)
            await add_part(
                chat_id,
                run_id,
                part_type,
                content=stored_content,
                tool_name=tool_name,
                metadata=safe_metadata,
            )
        except Exception as e:
            logger.warning("[{}] run.part.persist.failed | run_id={} type={} error={}", chat_id, run_id, part_type, e)

    async def emit_event(
        self,
        chat_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Persist and publish one structured run event."""
        created_at = time.time()
        safe_payload = json_safe_payload(payload)
        add_event = getattr(self.storage, "add_run_event", None)
        if callable(add_event):
            try:
                await add_event(chat_id, run_id, event_type, payload=safe_payload, created_at=created_at)
            except Exception as e:
                logger.warning("[{}] run.event.persist.failed | run_id={} type={} error={}", chat_id, run_id, event_type, e)

        message_bus = self._message_bus_getter()
        if message_bus is None or not channel or external_chat_id is None:
            return
        try:
            await message_bus.publish_run_event(
                RunEvent(
                    channel=channel,
                    external_chat_id=str(external_chat_id),
                    session_id=chat_id,
                    run_id=run_id,
                    event_type=event_type,
                    payload=safe_payload,
                    created_at=created_at,
                )
            )
        except Exception as e:
            logger.warning("[{}] run.event.publish.failed | run_id={} type={} error={}", chat_id, run_id, event_type, e)

    async def start_turn_run(
        self,
        chat_id: str,
        run_id: str,
        *,
        channel: str | None,
        external_chat_id: str | None,
        sender_id: str | None,
        sender_name: str | None,
        text: str | None,
        images: list[str] | None,
        audios: list[str] | None,
        videos: list[str] | None,
    ) -> None:
        """Create a run and emit the initial user-turn run_started event."""
        run_metadata = {
            "channel": channel,
            "external_chat_id": external_chat_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
        }
        run_metadata = {key: value for key, value in run_metadata.items() if value is not None}
        await self.create_run(chat_id, run_id, status="running", metadata=run_metadata)
        await self.emit_event(
            chat_id,
            run_id,
            "run_started",
            {
                "status": "running",
                "text_len": len(text or ""),
                "images_count": len(images or []),
                "audios_count": len(audios or []),
                "videos_count": len(videos or []),
            },
            channel=channel,
            external_chat_id=external_chat_id,
        )

    async def record_assistant_message_part(
        self,
        chat_id: str,
        run_id: str,
        response: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist the assistant-visible response as an ordered run part."""
        await self.add_part(
            chat_id,
            run_id,
            "assistant_message",
            content=response,
            metadata=metadata,
        )

    async def record_context_compaction_parts(
        self,
        chat_id: str,
        run_id: str,
        compaction_events: list[Any],
    ) -> None:
        """Persist context compaction telemetry events as ordered run parts."""
        for compaction_event in compaction_events:
            compaction_metadata = vars(compaction_event)
            await self.add_part(
                chat_id,
                run_id,
                "context_compaction",
                content=(
                    f"{compaction_event.trigger}:"
                    f"{compaction_event.strategy}:"
                    f"{compaction_event.outcome}"
                ),
                metadata=compaction_metadata,
            )

    async def complete_run(
        self,
        chat_id: str,
        run_id: str,
        *,
        event_payload: dict[str, Any],
        status_metadata: dict[str, Any] | None = None,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Emit run_finished and mark the durable run completed."""
        finished_at = time.time()
        await self.emit_event(
            chat_id,
            run_id,
            "run_finished",
            event_payload,
            channel=channel,
            external_chat_id=external_chat_id,
        )
        await self.update_run_status(
            chat_id,
            run_id,
            "completed",
            metadata=status_metadata,
            finished_at=finished_at,
        )

    async def fail_run(
        self,
        chat_id: str,
        run_id: str,
        *,
        status: str,
        event_payload: dict[str, Any],
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Emit a terminal run event and mark the durable run with the supplied status."""
        finished_at = time.time()
        event_type = "run_cancelled" if status == "cancelled" else "run_failed"
        await self.emit_event(
            chat_id,
            run_id,
            event_type,
            event_payload,
            channel=channel,
            external_chat_id=external_chat_id,
        )
        await self.update_run_status(chat_id, run_id, status, finished_at=finished_at)
