"""Run telemetry helpers for tool permission approval events."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from ..tools.approval import PermissionRequest
from ..utils import json_safe_value


class PermissionEventRecorder:
    """Formats and emits permission request lifecycle events."""

    def __init__(
        self,
        *,
        emit_run_event: Callable[..., Awaitable[None]],
        format_log_preview: Callable[..., str],
    ):
        self._emit_run_event = emit_run_event
        self._format_log_preview = format_log_preview

    async def emit(self, event_type: str, request: PermissionRequest) -> None:
        """Persist and publish one permission approval lifecycle event for a run."""
        if not request.session_id or not request.run_id:
            return
        try:
            params_preview = json.dumps(
                json_safe_value(request.params),
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:
            params_preview = str(request.params)
        payload: dict[str, Any] = {
            "request_id": request.request_id,
            "tool_name": request.tool_name,
            "reason": request.reason,
            "status": request.status,
            "action_type": request.action_type,
            "risk_level": request.risk_level,
            "risk_levels": request.risk_levels,
            "resource": request.resource,
            "preview": request.preview,
            "recommended_decision": request.recommended_decision,
            "args_preview": self._format_log_preview(params_preview, max_chars=240),
            "created_at": request.created_at,
            "expires_at": request.expires_at,
        }
        if request.resolved_at is not None:
            payload.update(
                {
                    "resolved_at": request.resolved_at,
                    "resolution_reason": request.resolution_reason,
                    "timed_out": request.timed_out,
                }
            )
        await self._emit_run_event(
            request.session_id,
            request.run_id,
            event_type,
            payload,
            channel=request.channel,
            external_chat_id=request.external_chat_id,
        )
