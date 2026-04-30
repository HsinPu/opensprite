"""In-memory session status tracking for channel-agnostic queue work."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal


SessionStatusType = Literal[
    "idle",
    "queued",
    "thinking",
    "streaming",
    "tool_running",
    "waiting_permission",
    "waiting_user",
    "cancelling",
    "retry",
]


@dataclass
class SessionStatus:
    """Current transient status for one normalized session."""

    session_id: str
    status: SessionStatusType
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionStatusService:
    """Tracks non-idle session status in memory."""

    def __init__(self):
        self._statuses: dict[str, SessionStatus] = {}

    def get(self, session_id: str) -> SessionStatus:
        """Return the current status for one session, defaulting to idle."""
        existing = self._statuses.get(session_id)
        if existing is not None:
            return existing
        return SessionStatus(session_id=session_id, status="idle")

    def list(self) -> list[SessionStatus]:
        """Return all currently non-idle session statuses."""
        return sorted(self._statuses.values(), key=lambda item: (item.updated_at, item.session_id), reverse=True)

    def set(self, session_id: str, status: SessionStatusType, metadata: dict[str, Any] | None = None) -> SessionStatus:
        """Set session status; idle clears transient state."""
        item = SessionStatus(session_id=session_id, status=status, metadata=dict(metadata or {}))
        if status == "idle":
            self._statuses.pop(session_id, None)
            return item
        self._statuses[session_id] = item
        return item
