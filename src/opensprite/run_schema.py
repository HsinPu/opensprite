"""Shared run event and artifact envelope helpers."""

from __future__ import annotations

from typing import Any

from .utils.json_safe import json_safe_payload

RUN_SCHEMA_VERSION = 1
MAX_SERIALIZED_RUN_EVENTS = 80
MAX_SERIALIZED_TEXT_EVENTS = 24


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
    "permission_requested": "permission",
    "permission_granted": "permission",
    "permission_denied": "permission",
    "run_part_delta": "text",
    "message_part_delta": "text",
}


_TEXT_DELTA_EVENTS = {"run_part_delta", "message_part_delta"}


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
    if normalized.startswith("permission_"):
        return "permission"
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
    if normalized in {"run_part_delta", "message_part_delta"}:
        return explicit or "running"
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

    if normalized in {"permission_requested", "permission_granted", "permission_denied"}:
        request_id = _text(data.get("request_id"))
        tool_name = _text(data.get("tool_name"))
        if not request_id and not tool_name:
            return None
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": f"permission:{request_id}" if request_id else None,
            "artifact_type": "permission",
            "kind": "permission",
            "status": status,
            "title": tool_name or "permission",
            "detail": _text(data.get("reason") or data.get("resolution_reason") or data.get("args_preview")),
            "tool_name": tool_name,
            "request_id": request_id or None,
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


def _is_text_delta_event(event: Any) -> bool:
    return _text(getattr(event, "event_type", "")) in _TEXT_DELTA_EVENTS


def compact_run_events(
    events: list[Any],
    *,
    max_events: int = MAX_SERIALIZED_RUN_EVENTS,
    max_text_events: int = MAX_SERIALIZED_TEXT_EVENTS,
) -> list[Any]:
    """Keep recent text deltas without letting them evict lifecycle events."""
    kept: list[Any] = []
    text_count = 0
    other_count = 0
    for event in reversed(events):
        if _is_text_delta_event(event):
            if text_count >= max_text_events:
                continue
            text_count += 1
        else:
            if other_count >= max_events:
                continue
            other_count += 1
        kept.append(event)
    kept.reverse()
    return kept


def serialize_run_events(events: list[Any]) -> list[dict[str, Any]]:
    """Serialize bounded run events for trace APIs."""
    return [serialize_run_event(event) for event in compact_run_events(list(events or []))]


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


def serialize_run_part(part: Any) -> dict[str, Any]:
    """Serialize one durable run part using the shared artifact projection."""
    metadata = json_safe_payload(dict(getattr(part, "metadata", {}) or {}))
    part_type = str(getattr(part, "part_type", "") or "")
    tool_name = getattr(part, "tool_name", None)
    content = str(getattr(part, "content", "") or "")
    artifact = run_part_artifact(
        part_id=getattr(part, "part_id", None),
        part_type=part_type,
        tool_name=tool_name,
        content=content,
        metadata=metadata,
    )
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "part_id": getattr(part, "part_id", None),
        "run_id": getattr(part, "run_id", None),
        "session_id": getattr(part, "session_id", None),
        "part_type": part_type,
        "kind": run_part_kind(part_type),
        "state": run_part_state(part_type, metadata),
        "content": content,
        "tool_name": tool_name,
        "metadata": metadata,
        "artifact": artifact,
        "created_at": getattr(part, "created_at", None),
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


def serialize_file_change(change: Any) -> dict[str, Any]:
    """Serialize one durable file change using the shared artifact projection."""
    metadata = json_safe_payload(dict(getattr(change, "metadata", {}) or {}))
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "change_id": getattr(change, "change_id", None),
        "run_id": getattr(change, "run_id", None),
        "session_id": getattr(change, "session_id", None),
        "kind": "file",
        "state": "completed",
        "tool_name": getattr(change, "tool_name", None),
        "path": getattr(change, "path", None),
        "action": getattr(change, "action", None),
        "before_sha256": getattr(change, "before_sha256", None),
        "after_sha256": getattr(change, "after_sha256", None),
        "before_content": getattr(change, "before_content", None),
        "after_content": getattr(change, "after_content", None),
        "diff": getattr(change, "diff", None),
        "metadata": metadata,
        "artifact": file_change_artifact(change),
        "created_at": getattr(change, "created_at", None),
    }


def serialize_run_artifacts(trace: Any) -> list[dict[str, Any]]:
    """Project run events, parts, and file changes into merged artifacts."""
    artifacts_by_key: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []

    def upsert_artifact(item: dict[str, Any]) -> None:
        key = str(item.get("artifact_id") or f"{item.get('source')}:{item.get('source_id')}")
        existing = artifacts_by_key.get(key)
        if existing is None:
            artifacts_by_key[key] = item
            return
        sources = list(existing.get("sources") or [existing.get("source")])
        source = item.get("source")
        if source and source not in sources:
            sources.append(source)
        artifacts_by_key[key] = {**existing, **item, "sources": [entry for entry in sources if entry]}

    for event in getattr(trace, "events", None) or []:
        serialized = serialize_run_event(event)
        artifact = serialized.get("artifact")
        if not isinstance(artifact, dict):
            continue
        candidates.append(
            {
                **artifact,
                "source": "event",
                "source_id": serialized.get("event_id"),
                "event_type": serialized.get("event_type"),
                "created_at": serialized.get("created_at"),
            }
        )
    for part in getattr(trace, "parts", None) or []:
        serialized = serialize_run_part(part)
        artifact = serialized.get("artifact")
        if not isinstance(artifact, dict):
            continue
        candidates.append(
            {
                **artifact,
                "source": "part",
                "source_id": serialized.get("part_id"),
                "part_type": serialized.get("part_type"),
                "created_at": serialized.get("created_at"),
            }
        )
    for change in getattr(trace, "file_changes", None) or []:
        serialized = serialize_file_change(change)
        artifact = serialized.get("artifact")
        if not isinstance(artifact, dict):
            continue
        candidates.append(
            {
                **artifact,
                "source": "file_change",
                "source_id": serialized.get("change_id"),
                "created_at": serialized.get("created_at"),
            }
        )
    candidates.sort(
        key=lambda item: (
            float(item.get("created_at") or 0),
            str(item.get("artifact_id") or item.get("source_id") or ""),
        )
    )
    for candidate in candidates:
        upsert_artifact(candidate)
    artifacts = list(artifacts_by_key.values())
    artifacts.sort(
        key=lambda item: (
            float(item.get("created_at") or 0),
            str(item.get("artifact_id") or item.get("source_id") or ""),
        )
    )
    return artifacts


def _latest_event_payload(events: list[Any], event_type: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if getattr(event, "event_type", None) == event_type:
            return dict(getattr(event, "payload", {}) or {})
    return None


def _latest_work_progress(events: list[Any]) -> dict[str, Any] | None:
    for event in reversed(events):
        payload = dict(getattr(event, "payload", {}) or {})
        event_type = getattr(event, "event_type", None)
        if event_type == "work_progress.updated":
            return payload
        if event_type == "run_finished" and isinstance(payload.get("work_progress"), dict):
            return dict(payload["work_progress"])
    return None


def _metadata_bool(metadata: dict[str, Any], key: str) -> bool:
    return metadata.get(key) is True or metadata.get(key) == "true" or metadata.get(key) == 1


def _summarize_tools(parts: list[Any], events: list[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for part in parts:
        if getattr(part, "part_type", None) != "tool_call" or not getattr(part, "tool_name", None):
            continue
        tool_name = str(getattr(part, "tool_name"))
        counts[tool_name] = counts.get(tool_name, 0) + 1

    if not counts:
        for event in events:
            if getattr(event, "event_type", None) != "tool_started":
                continue
            tool_name = str((getattr(event, "payload", {}) or {}).get("tool_name") or "").strip()
            if not tool_name:
                continue
            counts[tool_name] = counts.get(tool_name, 0) + 1

    return [{"name": name, "count": count} for name, count in counts.items()]


def _summarize_file_changes(file_changes: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "change_id": getattr(change, "change_id", None),
            "path": getattr(change, "path", None),
            "action": getattr(change, "action", None),
            "tool_name": getattr(change, "tool_name", None),
            "diff_len": int((getattr(change, "metadata", {}) or {}).get("diff_len") or len(getattr(change, "diff", "") or "")),
            "diff": getattr(change, "diff", None) or "",
            "snapshots_available": {
                "before": getattr(change, "before_content", None) is not None,
                "after": getattr(change, "after_content", None) is not None,
            },
        }
        for change in file_changes
    ]


def _summarize_verification(run_metadata: dict[str, Any], events: list[Any]) -> dict[str, Any]:
    latest = _latest_event_payload(events, "verification_result")
    attempted = _metadata_bool(run_metadata, "verification_attempted") or latest is not None
    passed = _metadata_bool(run_metadata, "verification_passed")
    if latest is not None:
        passed = latest.get("ok") is not False and str(latest.get("verification_status") or "").lower() not in {"failed", "error"}

    status = "not_attempted"
    name = None
    summary = ""
    if attempted:
        status = "passed" if passed else "failed"
    if latest is not None:
        status = str(latest.get("verification_status") or status)
        name = latest.get("verification_name")
        summary = str(latest.get("result_preview") or "")

    return {
        "attempted": attempted,
        "passed": passed,
        "status": status,
        "name": name,
        "summary": summary,
    }


def serialize_run_summary(trace: Any) -> dict[str, Any]:
    """Serialize the compact run summary used by Web inspector cards."""
    run = trace.run
    events = list(getattr(trace, "events", None) or [])
    parts = list(getattr(trace, "parts", None) or [])
    file_changes = list(getattr(trace, "file_changes", None) or [])
    run_metadata = dict(getattr(run, "metadata", {}) or {})
    task_intent = _latest_event_payload(events, "task_intent.detected") or {}
    completion = _latest_event_payload(events, "completion_gate.evaluated") or {}
    work_progress = _latest_work_progress(events) or {}
    verification = _summarize_verification(run_metadata, events)
    had_tool_error = _metadata_bool(run_metadata, "had_tool_error")
    warnings: list[str] = []
    if had_tool_error:
        warnings.append("tool_error")
    if verification["attempted"] and not verification["passed"]:
        warnings.append("verification_not_passed")
    if getattr(run, "status", None) in {"failed", "cancelled"}:
        warnings.append(run.status)

    duration_seconds = None
    if getattr(run, "finished_at", None) is not None:
        duration_seconds = max(0.0, float(run.finished_at) - float(run.created_at))

    objective = str(task_intent.get("objective") or run_metadata.get("objective") or "").strip()
    artifacts = serialize_run_artifacts(trace)
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": getattr(run, "run_id", None),
        "session_id": getattr(run, "session_id", None),
        "status": getattr(run, "status", None),
        "objective": objective or None,
        "created_at": getattr(run, "created_at", None),
        "updated_at": getattr(run, "updated_at", None),
        "finished_at": getattr(run, "finished_at", None),
        "duration_seconds": duration_seconds,
        "tools": _summarize_tools(parts, events),
        "file_changes": _summarize_file_changes(file_changes),
        "verification": verification,
        "artifact_counts": {
            "total": len(artifacts),
            "tool": sum(1 for artifact in artifacts if artifact.get("kind") == "tool"),
            "file": sum(1 for artifact in artifacts if artifact.get("kind") == "file"),
            "verification": sum(1 for artifact in artifacts if artifact.get("kind") == "verification"),
        },
        "completion": json_safe_payload(completion),
        "next_action": work_progress.get("next_action"),
        "warnings": warnings,
        "counts": {
            "events": len(events),
            "parts": len(parts),
            "tool_calls": sum(1 for part in parts if getattr(part, "part_type", None) == "tool_call"),
            "file_changes": len(file_changes),
        },
    }
