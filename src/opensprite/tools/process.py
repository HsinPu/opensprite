"""Managed background process inspection tool."""

from __future__ import annotations

from typing import Any

from .base import Tool
from .process_runtime import BackgroundProcessManager, BackgroundSession
from .validation import NON_EMPTY_STRING_PATTERN


def _command_preview(command: str, *, max_chars: int = 80) -> str:
    normalized = " ".join(command.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."


def _format_session_summary(session: BackgroundSession) -> str:
    parts = [session.session_id, session.state, f"pid={session.pid}"]
    if session.state == "exited":
        parts.append(f"termination={session.termination_reason or 'exit'}")
        parts.append(f"exit_code={session.exit_code}")
    parts.append(_command_preview(session.command))
    return " | ".join(parts)


def _format_session_details(session: BackgroundSession) -> list[str]:
    details = [
        f"Session ID: {session.session_id}",
        f"Status: {session.state}",
        f"PID: {session.pid}",
        f"Command: {session.command}",
    ]
    if session.state == "exited":
        details.append(f"Termination: {session.termination_reason or 'exit'}")
        details.append(f"Exit code: {session.exit_code}")
        if session.error:
            details.append(f"Session error: {session.error}")
        if not session.output_drained:
            details.append(
                "Warning: output readers did not drain before the session finalized."
            )
    return details


class ProcessTool(Tool):
    """Inspect and control managed background exec sessions."""

    def __init__(self, manager: BackgroundProcessManager | None = None):
        self.manager = manager or BackgroundProcessManager()

    @property
    def name(self) -> str:
        return "process"

    @property
    def description(self) -> str:
        return (
            "Inspect managed background exec sessions, poll new output, or terminate a running session."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "poll", "kill"],
                    "description": "Required. list all sessions, poll one session for new output, or kill one session.",
                },
                "session_id": {
                    "type": "string",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                    "description": "Required for poll/kill. Background session id returned by exec.",
                },
            },
            "required": ["action"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        action = str(kwargs["action"]).strip().lower()
        session_id = str(kwargs.get("session_id", "")).strip()

        if action == "list":
            sessions = await self.manager.list_sessions()
            if not sessions:
                return "No background sessions."
            return "Background sessions:\n" + "\n".join(
                _format_session_summary(session) for session in sessions
            )

        if not session_id:
            return f"Error: process action '{action}' requires session_id."

        if action == "poll":
            polled = await self.manager.poll_session(session_id)
            if polled is None:
                return f"Error: background session '{session_id}' not found."
            session, new_output = polled
            lines = _format_session_details(session)
            lines.extend(["New output:", new_output])
            return "\n".join(lines)

        if action == "kill":
            session = await self.manager.kill_session(session_id)
            if session is None:
                return f"Error: background session '{session_id}' not found."
            lines = _format_session_details(session)
            lines.extend(["Output tail:", self.manager.render_output(session, max_chars=1200)])
            return "\n".join(lines)

        return f"Error: unsupported process action '{action}'."
