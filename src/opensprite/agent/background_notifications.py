"""Outbound notifications for managed background process sessions."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from ..bus.events import InboundMessage
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
    def format_summary_request(session: BackgroundSession) -> str:
        """Render the internal request asking the agent to summarize a completed process."""
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
                "A managed background process has finished. Summarize the result for the user.",
                f"Session ID: {session.session_id}",
                f"Command: {session.command}",
                f"Termination: {session.termination_reason or 'exit'}",
                f"Exit code: {session.exit_code}",
                f"Runtime: {runtime_seconds:.2f}s",
                "Keep the reply concise. Mention whether it succeeded, failed, or was stopped. Include only the most relevant output details.",
                "Output tail:",
                output_tail,
            ]
        )

    @staticmethod
    def format_exit_message(session: BackgroundSession) -> str:
        """Backward-compatible alias for the agent summary request text."""
        return BackgroundSessionNotificationService.format_summary_request(session)

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
            content = self.format_summary_request(session)
            metadata = {
                "channel": ch,
                "external_chat_id": tid,
                "kind": "background_session_summary_request",
                "session_id": session.session_id,
                "termination_reason": session.termination_reason or "exit",
                "exit_code": session.exit_code,
                "_bypass_commands": True,
            }
            await bus.publish_inbound(
                InboundMessage(
                    channel=ch,
                    sender_id="system:background",
                    sender_name="background process",
                    external_chat_id=tid,
                    session_id=sid,
                    content=content,
                    metadata=metadata,
                )
            )

        return _notify
