"""Outbound notifications for managed background process sessions."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from ..bus.events import OutboundMessage
from ..tools.process_runtime import BackgroundSession
from ..tools.shell_runtime import format_captured_output


class BackgroundSessionNotificationService:
    """Formats and publishes background session completion notices."""

    def __init__(
        self,
        *,
        message_bus_getter: Callable[[], Any],
        save_message: Callable[..., Awaitable[None]],
    ):
        self._message_bus_getter = message_bus_getter
        self._save_message = save_message

    @staticmethod
    def format_exit_message(session: BackgroundSession) -> str:
        """Render a concise outbound notice when a managed background session exits."""
        output_tail = format_captured_output(
            session.output_chunks,
            max_chars=1200,
        )
        runtime_seconds = max(
            0.0,
            (session.finished_at or time.monotonic()) - session.started_at,
        )
        return "\n".join(
            [
                "Background session finished.",
                f"Session ID: {session.session_id}",
                f"Termination: {session.termination_reason or 'exit'}",
                f"Exit code: {session.exit_code}",
                f"Runtime: {runtime_seconds:.2f}s",
                "Output tail:",
                output_tail,
            ]
        )

    def make_exit_notifier(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str | None,
    ) -> Callable[[BackgroundSession], Awaitable[None]] | None:
        """Build an outbound notifier for managed background session completion."""
        bus = self._message_bus_getter()
        if not bus or not channel or external_chat_id is None or session_id is None:
            return None

        ch = channel
        tid = str(external_chat_id)
        sid = session_id

        async def _notify(session: BackgroundSession) -> None:
            content = self.format_exit_message(session)
            metadata = {
                "channel": ch,
                "external_chat_id": tid,
                "kind": "background_session_exit",
                "session_id": session.session_id,
                "termination_reason": session.termination_reason or "exit",
                "exit_code": session.exit_code,
            }
            await self._save_message(sid, "assistant", content, metadata=metadata)
            await bus.publish_outbound(
                OutboundMessage(
                    channel=ch,
                    external_chat_id=tid,
                    session_id=sid,
                    content=content,
                    metadata=metadata,
                )
            )

        return _notify
