"""Permission request lifecycle orchestration for AgentLoop."""

from __future__ import annotations

from typing import Any, Callable

from ..tools.approval import DEFAULT_PERMISSION_DENIAL_REASON, PermissionRequest, PermissionRequestManager
from ..tools.permissions import PermissionApprovalResult, PermissionDecision
from .permission_events import PermissionEventRecorder


class AgentPermissionService:
    """Wraps ask-mode permission requests with current run context."""

    def __init__(
        self,
        *,
        requests: PermissionRequestManager,
        events: PermissionEventRecorder,
        current_session_id: Callable[[], str | None],
        current_run_id: Callable[[], str | None],
        current_channel: Callable[[], str | None],
        current_external_chat_id: Callable[[], str | None],
    ):
        self.requests = requests
        self.events = events
        self._current_session_id = current_session_id
        self._current_run_id = current_run_id
        self._current_channel = current_channel
        self._current_external_chat_id = current_external_chat_id

    def pending_requests(self) -> list[PermissionRequest]:
        """Return permission requests waiting for an external decision."""
        return self.requests.pending_requests()

    async def approve_request(self, request_id: str) -> PermissionRequest | None:
        """Approve one pending tool permission request."""
        return await self.requests.approve_once(request_id)

    async def deny_request(
        self,
        request_id: str,
        reason: str = DEFAULT_PERMISSION_DENIAL_REASON,
    ) -> PermissionRequest | None:
        """Deny one pending tool permission request."""
        return await self.requests.deny(request_id, reason=reason)

    async def handle_tool_permission_request(
        self,
        tool_name: str,
        params: Any,
        decision: PermissionDecision,
    ) -> PermissionApprovalResult:
        """Create an ask-mode approval request for the current run context."""
        return await self.requests.request(
            tool_name=tool_name,
            params=params,
            reason=decision.reason,
            risk_levels=decision.risk_levels,
            session_id=self._current_session_id(),
            run_id=self._current_run_id(),
            channel=self._current_channel(),
            external_chat_id=self._current_external_chat_id(),
        )

    async def emit_request_event(self, event_type: str, request: PermissionRequest) -> None:
        """Persist and publish permission approval lifecycle events for a run."""
        await self.events.emit(event_type, request)
