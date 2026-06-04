"""One-shot CLI channel adapter for local chat smoke tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..bus import RunEvent, SessionStatusEvent
from ..bus.message import AssistantMessage, MessageAdapter, UserMessage
from ..runs.lifecycle import TERMINAL_RUN_EVENTS
from .identity import build_session_id, normalize_identifier


@dataclass
class CliChatResult:
    """Result returned by a one-shot CLI chat turn."""

    response: AssistantMessage
    run_id: str | None = None
    run_status: str = ""
    run_events: list[RunEvent] = field(default_factory=list)
    statuses: list[SessionStatusEvent] = field(default_factory=list)
    error: str = ""

    @property
    def tool_call_count(self) -> int:
        return sum(1 for event in self.run_events if event.event_type == "tool_started")


class CliAdapter(MessageAdapter):
    """Small one-shot channel adapter used by `opensprite chat`."""

    def __init__(
        self,
        mq: Any,
        *,
        channel_instance_id: str = "cli",
        external_chat_id: str = "default",
        session_id: str | None = None,
        sender_id: str = "cli-user",
        sender_name: str = "OpenSprite CLI",
    ):
        self.mq = mq
        self.channel_type = "cli"
        self.channel_instance_id = normalize_identifier(channel_instance_id, fallback="cli")
        self.external_chat_id = str(external_chat_id or "default").strip() or "default"
        self.session_id = session_id or build_session_id(self.channel_instance_id, self.external_chat_id)
        self.sender_id = sender_id
        self.sender_name = sender_name
        self._response: AssistantMessage | None = None
        self._response_event = asyncio.Event()
        self._run_id: str | None = None
        self._run_status = ""
        self._run_events: list[RunEvent] = []
        self._statuses: list[SessionStatusEvent] = []
        self._error = ""

    async def to_user_message(self, raw_message: Any) -> UserMessage:
        payload = dict(raw_message) if isinstance(raw_message, dict) else {"text": str(raw_message or "")}
        return UserMessage(
            text=str(payload.get("text") or ""),
            channel=self.channel_instance_id,
            external_chat_id=str(payload.get("external_chat_id") or self.external_chat_id),
            session_id=str(payload.get("session_id") or self.session_id),
            sender_id=str(payload.get("sender_id") or self.sender_id),
            sender_name=str(payload.get("sender_name") or self.sender_name),
            metadata=dict(payload.get("metadata") or {}),
            raw=payload,
        )

    async def send(self, message: AssistantMessage) -> None:
        self._response = message
        self._response_event.set()

    async def _on_response(self, message: AssistantMessage, channel: str, external_chat_id: str | None) -> None:
        _ = channel, external_chat_id
        await self.send(message)

    async def _on_run_event(self, event: RunEvent) -> None:
        self._run_events.append(event)
        if event.run_id and self._run_id is None:
            self._run_id = event.run_id
        if event.event_type in TERMINAL_RUN_EVENTS:
            status = ""
            if not status and isinstance(event.payload, dict):
                status = str(event.payload.get("status") or "")
            self._run_status = status or event.event_type

    async def _on_session_status(self, event: SessionStatusEvent) -> None:
        self._statuses.append(event)

    async def _on_error(self, session_id: str, error: str) -> None:
        _ = session_id
        self._error = error
        self._response_event.set()

    def register(self) -> None:
        self.mq.register_response_handler(self.channel_instance_id, self._on_response)
        self.mq.register_run_event_handler(self.channel_instance_id, self._on_run_event)
        self.mq.register_session_status_handler(self.channel_instance_id, self._on_session_status)
        self.mq.register_error_handler(self.channel_instance_id, self._on_error)

    def unregister(self) -> None:
        self.mq.unregister_response_handler(self.channel_instance_id)
        self.mq.unregister_run_event_handler(self.channel_instance_id)
        self.mq.unregister_session_status_handler(self.channel_instance_id)
        self.mq.unregister_error_handler(self.channel_instance_id)

    async def run_once(self, text: str, *, timeout: float = 120.0, metadata: dict[str, Any] | None = None) -> CliChatResult:
        """Send one CLI message through the queue and wait for the assistant response."""
        self.register()
        try:
            user_message = await self.to_user_message(
                {
                    "text": text,
                    "external_chat_id": self.external_chat_id,
                    "session_id": self.session_id,
                    "sender_id": self.sender_id,
                    "sender_name": self.sender_name,
                    "metadata": {"source": "cli", **dict(metadata or {})},
                }
            )
            await self.mq.enqueue(user_message)
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
            if self._response is None:
                raise RuntimeError(self._error or "CLI chat did not receive an assistant response")
            return CliChatResult(
                response=self._response,
                run_id=self._run_id,
                run_status=self._run_status,
                run_events=list(self._run_events),
                statuses=list(self._statuses),
                error=self._error,
            )
        finally:
            self.unregister()
