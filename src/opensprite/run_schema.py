"""Shared run event and artifact envelope helpers."""

from __future__ import annotations

from typing import Any

from .utils.json_safe import json_safe_payload

RUN_SCHEMA_VERSION = 1


_EVENT_KINDS = {
    "run_started": "run",
    "run_finished": "run",
    "run_failed": "run",
    "run_cancelled": "run",
    "run_cancel_requested": "run",
    "llm_status": "llm",
    "task_intent.detected": "work",
    "work_plan.created": "work",
    "work_progress.updated": "work",
    "completion_gate.evaluated": "completion",
    "auto_continue.scheduled": "run",
    "auto_continue.completed": "run",
    "tool_started": "tool",
    "tool_result": "tool",
    "verification_started": "verification",
    "verification_result": "verification",
    "file_changed": "file",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _non_negative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _tool_artifact_id(tool_name: str, data: dict[str, Any]) -> str | None:
    call_id = _text(data.get("tool_call_id") or data.get("call_id"))
    if call_id:
        return f"tool:{call_id}"
    iteration = data.get("iteration")
    if tool_name and iteration is not None:
        return f"tool:{tool_name}:{iteration}"
    return None


def run_event_kind(event_type: str) -> str:
    """Return the stable high-level category for one run event type."""
    normalized = _text(event_type)
    if normalized in _EVENT_KINDS:
        return _EVENT_KINDS[normalized]
    if normalized.startswith("tool_"):
        return "tool"
    if normalized.startswith("verification_"):
        return "verification"
    if normalized.startswith("llm_"):
        return "llm"
    if normalized.startswith("work_") or normalized.startswith("task_"):
        return "work"
    if normalized.startswith("run_") or normalized.startswith("auto_continue."):
        return "run"
    return "other"


def run_event_status(event_type: str, payload: dict[str, Any] | None) -> str:
    """Return a normalized lifecycle state for one run event."""
    normalized = _text(event_type)
    data = payload or {}
    explicit = _text(data.get("status") or data.get("state"))
    if normalized == "run_started":
        return explicit or "running"
    if normalized == "run_finished":
        return explicit or "completed"
    if normalized == "run_failed":
        return explicit or "failed"
    if normalized == "run_cancelled":
        return explicit or "cancelled"
    if normalized == "run_cancel_requested":
        return explicit or "cancelling"
    if normalized.endswith("_started") or normalized == "llm_status" or normalized == "auto_continue.scheduled":
        return explicit or "running"
    if explicit:
        return explicit
    if data.get("ok") is False:
        return "failed" if run_event_kind(normalized) == "verification" else "error"
    return "completed"


def event_artifact(event_type: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a compact artifact projection for event types that represent artifacts."""
    data = json_safe_payload(payload)
    normalized = _text(event_type)
    status = run_event_status(normalized, data)

    if normalized == "file_changed":
        path = _text(data.get("path"))
        if not path:
            return None
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_type": "file_change",
            "kind": "file",
            "status": status,
            "path": path,
            "action": _text(data.get("action")),
            "tool_name": _text(data.get("tool_name")),
            "diff_len": _non_negative_int(data.get("diff_len")),
            "diff_preview": _text(data.get("diff_preview")),
        }

    if normalized in {"tool_started", "tool_result"}:
        tool_name = _text(data.get("tool_name"))
        if not tool_name:
            return None
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": _tool_artifact_id(tool_name, data),
            "artifact_type": "tool",
            "kind": "tool",
            "status": status,
            "phase": "started" if normalized == "tool_started" else "result",
            "tool_name": tool_name,
            "tool_call_id": data.get("tool_call_id"),
            "iteration": data.get("iteration"),
            "title": tool_name,
            "detail": _text(data.get("result_preview") or data.get("args_preview")),
        }

    if normalized in {"verification_started", "verification_result"}:
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_type": "verification",
            "kind": "verification",
            "status": status,
            "title": _text(data.get("verification_name") or data.get("action") or "verification"),
            "detail": _text(data.get("result_preview") or data.get("path")),
        }

    return None


def run_event_envelope(event_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Build the stable event envelope shared by live sockets and history APIs."""
    safe_payload = json_safe_payload(payload)
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "kind": run_event_kind(event_type),
        "status": run_event_status(event_type, safe_payload),
        "payload": safe_payload,
        "artifact": event_artifact(event_type, safe_payload),
    }


def serialize_run_event(
    event: Any,
    *,
    include_event_id: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a stored or live run event using the shared envelope."""
    event_type = str(getattr(event, "event_type", "") or "")
    envelope = run_event_envelope(event_type, dict(getattr(event, "payload", {}) or {}))
    serialized = {
        "schema_version": envelope["schema_version"],
        "run_id": getattr(event, "run_id", None),
        "session_id": getattr(event, "session_id", None),
        "event_type": event_type,
        "kind": envelope["kind"],
        "status": envelope["status"],
        "payload": envelope["payload"],
        "artifact": envelope["artifact"],
        "created_at": getattr(event, "created_at", None),
    }
    if include_event_id:
        serialized["event_id"] = getattr(event, "event_id", None)
    if extra:
        serialized.update(json_safe_payload(extra))
    return serialized


def run_part_kind(part_type: str) -> str:
    normalized = _text(part_type)
    if normalized == "assistant_message":
        return "text"
    if normalized in {"tool_call", "tool_result"}:
        return "tool"
    if normalized == "context_compaction":
        return "system"
    return "other"


def run_part_state(part_type: str, metadata: dict[str, Any] | None) -> str:
    explicit = _text((metadata or {}).get("state") or (metadata or {}).get("status"))
    if explicit:
        return explicit
    if part_type == "tool_call":
        return "running"
    if part_type == "tool_result" and (metadata or {}).get("ok") is False:
        return "error"
    return "completed"


def run_part_artifact(
    *,
    part_id: int | None,
    part_type: str,
    tool_name: str | None,
    content: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    safe_metadata = json_safe_payload(metadata)
    kind = run_part_kind(part_type)
    state = run_part_state(part_type, safe_metadata)
    title = _text(tool_name) or _text(part_type) or "part"
    detail = _text(safe_metadata.get("result_preview") or safe_metadata.get("args_preview"))
    if not detail and kind == "text":
        detail = str(content or "")[:240]
    artifact_id = f"part:{part_id}" if part_id is not None else None
    if kind == "tool":
        artifact_id = _tool_artifact_id(_text(tool_name), safe_metadata) or artifact_id
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_type": "tool" if kind == "tool" else part_type,
        "kind": kind,
        "status": state,
        "phase": part_type if kind == "tool" else None,
        "tool_name": _text(tool_name),
        "tool_call_id": safe_metadata.get("tool_call_id"),
        "iteration": safe_metadata.get("iteration"),
        "title": title,
        "detail": detail,
        "metadata": safe_metadata,
    }


def file_change_artifact(change: Any) -> dict[str, Any]:
    metadata = json_safe_payload(dict(getattr(change, "metadata", {}) or {}))
    diff = str(getattr(change, "diff", "") or "")
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "artifact_id": f"file_change:{getattr(change, 'change_id', None)}" if getattr(change, "change_id", None) is not None else None,
        "artifact_type": "file_change",
        "kind": "file",
        "status": "completed",
        "path": str(getattr(change, "path", "") or ""),
        "action": str(getattr(change, "action", "") or ""),
        "tool_name": str(getattr(change, "tool_name", "") or ""),
        "diff_len": _non_negative_int(metadata.get("diff_len") or len(diff)),
        "snapshots_available": {
            "before": getattr(change, "before_content", None) is not None,
            "after": getattr(change, "after_content", None) is not None,
        },
        "metadata": metadata,
    }
