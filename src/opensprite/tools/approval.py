"""Runtime approval requests for ask-mode tool permissions."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..utils.log import logger
from .permissions import PermissionApprovalResult, ToolPermissionPolicy


def _text(value: Any) -> str:
    return str(value or "").strip()


def _param_value(params: Any, *names: str) -> str:
    if not isinstance(params, dict):
        return ""
    for name in names:
        value = params.get(name)
        if value is not None:
            return _text(value)
    return ""


def _preview_params(params: Any, *, max_chars: int = 240) -> str:
    if isinstance(params, dict):
        for key in ("path", "file_path", "command", "cmd", "query", "url", "name"):
            value = _param_value(params, key)
            if value:
                return value[:max_chars]
    return _text(params)[:max_chars]


def classify_permission_request(tool_name: str, params: Any) -> dict[str, Any]:
    """Classify one tool approval request for safer user-facing decisions."""
    risks = sorted(ToolPermissionPolicy.risk_levels_for_tool(tool_name))
    command = _param_value(params, "command", "cmd")
    lowered_command = command.lower()
    action_type = "external_message"
    if "read" in risks and not any(risk in risks for risk in ("write", "execute", "external_side_effect")):
        action_type = "read"
    elif "write" in risks:
        action_type = "edit"
    elif "execute" in risks:
        action_type = "shell"
    elif tool_name == "cron":
        action_type = "schedule"
    if any(token in lowered_command for token in ("git commit", "git add")):
        action_type = "commit"
    if "git push" in lowered_command:
        action_type = "push"
    if any(token in lowered_command for token in ("rm -rf", "del /", "rmdir", "git reset --hard", "drop table")):
        action_type = "destructive"

    if action_type in {"destructive", "push"}:
        risk_level = "high"
    elif action_type in {"edit", "shell", "commit", "external_message", "schedule"}:
        risk_level = "medium"
    else:
        risk_level = "low"

    resource = _param_value(params, "path", "file_path", "url", "command", "cmd", "query", "name")
    return {
        "action_type": action_type,
        "risk_level": risk_level,
        "risk_levels": risks,
        "resource": resource,
        "preview": _preview_params(params),
        "recommended_decision": "deny" if action_type == "destructive" else "approve",
    }


@dataclass
class PermissionRequest:
    """One pending decision for a tool call that requires user approval."""

    request_id: str
    tool_name: str
    params: Any
    reason: str
    created_at: float
    expires_at: float
    session_id: str | None = None
    run_id: str | None = None
    channel: str | None = None
    external_chat_id: str | None = None
    action_type: str = "external_message"
    risk_level: str = "medium"
    risk_levels: list[str] = field(default_factory=list)
    resource: str = ""
    preview: str = ""
    recommended_decision: str = "approve"
    status: str = "pending"
    resolved_at: float | None = None
    resolution_reason: str = ""
    timed_out: bool = False
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)


PermissionRequestEventHandler = Callable[[str, PermissionRequest], Awaitable[None]]


class PermissionRequestManager:
    """Track pending permission requests and resolve them from external UI actions."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 300.0,
        on_event: PermissionRequestEventHandler | None = None,
    ):
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self._on_event = on_event
        self._requests: dict[str, PermissionRequest] = {}
        self._lock = asyncio.Lock()

    def pending_requests(self) -> list[PermissionRequest]:
        """Return currently pending requests from oldest to newest."""
        return sorted(
            (request for request in self._requests.values() if request.status == "pending"),
            key=lambda request: request.created_at,
        )

    async def request(
        self,
        *,
        tool_name: str,
        params: Any,
        reason: str,
        session_id: str | None = None,
        run_id: str | None = None,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> PermissionApprovalResult:
        """Create a pending request and wait until it is approved, denied, or timed out."""
        created_at = time.time()
        classification = classify_permission_request(tool_name, params)
        request = PermissionRequest(
            request_id=f"perm_{uuid4().hex}",
            tool_name=tool_name,
            params=params,
            reason=reason,
            session_id=session_id,
            run_id=run_id,
            channel=channel,
            external_chat_id=external_chat_id,
            created_at=created_at,
            expires_at=created_at + self.timeout_seconds,
            **classification,
        )
        async with self._lock:
            self._requests[request.request_id] = request

        await self._emit("permission_requested", request)
        try:
            try:
                await asyncio.wait_for(request._event.wait(), timeout=self.timeout_seconds)
            except TimeoutError:
                await self._resolve(
                    request.request_id,
                    status="denied",
                    reason="permission request timed out",
                    timed_out=True,
                    event_type="permission_denied",
                )

            return PermissionApprovalResult(
                approved=request.status == "approved",
                request_id=request.request_id,
                reason=request.resolution_reason or request.reason,
                status=request.status,
            )
        finally:
            async with self._lock:
                self._requests.pop(request.request_id, None)

    async def approve_once(self, request_id: str) -> PermissionRequest | None:
        """Approve one pending request and allow that single tool call to continue."""
        return await self._resolve(
            request_id,
            status="approved",
            reason="approved once",
            timed_out=False,
            event_type="permission_granted",
        )

    async def deny(self, request_id: str, reason: str = "user denied approval") -> PermissionRequest | None:
        """Deny one pending request."""
        return await self._resolve(
            request_id,
            status="denied",
            reason=reason,
            timed_out=False,
            event_type="permission_denied",
        )

    async def _resolve(
        self,
        request_id: str,
        *,
        status: str,
        reason: str,
        timed_out: bool,
        event_type: str,
    ) -> PermissionRequest | None:
        async with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.status != "pending":
                return None
            request.status = status
            request.resolved_at = time.time()
            request.resolution_reason = reason
            request.timed_out = timed_out
            request._event.set()

        await self._emit(event_type, request)
        return request

    async def _emit(self, event_type: str, request: PermissionRequest) -> None:
        if self._on_event is None:
            return
        try:
            await self._on_event(event_type, request)
        except Exception as exc:
            logger.warning(
                "permission.event.failed | request_id={} type={} error={}",
                request.request_id,
                event_type,
                exc,
            )
