"""Session entry projections for human-readable timelines."""

from __future__ import annotations

from typing import Any

from .schema import RUN_SCHEMA_VERSION, serialize_run_artifacts, serialize_run_part
from ..utils.json_safe import json_safe_payload


def _text(value: Any) -> str:
    return str(value or "").strip()


def _entry_time(item: dict[str, Any]) -> float:
    try:
        return float(item.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def serialize_message_entry(message: Any, *, index: int | None = None) -> dict[str, Any]:
    """Project one stored chat message into the shared session-entry shape."""
    role = _text(getattr(message, "role", "assistant")) or "assistant"
    timestamp = float(getattr(message, "timestamp", 0) or 0)
    entry_id = f"message:{index}" if index is not None else None
    content = _text(getattr(message, "content", ""))
    metadata = json_safe_payload(dict(getattr(message, "metadata", {}) or {}))

    entry: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "entry_id": entry_id,
        "entry_type": role if role in {"user", "assistant", "system"} else "assistant",
        "role": role,
        "created_at": timestamp,
        "metadata": metadata,
    }
    if role == "assistant":
        entry["content"] = [
            {
                "type": "text",
                "text": content,
                "created_at": timestamp,
            }
        ]
    else:
        entry["text"] = content
    tool_name = getattr(message, "tool_name", None)
    if tool_name:
        entry["tool_name"] = tool_name
    return entry


def _content_type_from_artifact(artifact: dict[str, Any]) -> str:
    kind = _text(artifact.get("kind"))
    artifact_type = _text(artifact.get("artifact_type"))
    if kind == "file" or artifact_type == "file_change":
        return "file"
    if kind == "verification":
        return "verification"
    if kind == "permission":
        return "permission"
    if kind == "task" or artifact_type == "task_checklist":
        return "task"
    if kind == "llm" or artifact_type == "llm_step":
        return "llm_step"
    if kind == "tool" or artifact_type == "tool":
        return "tool"
    if artifact_type == "context_compaction":
        return "compaction"
    return kind or artifact_type or "system"


def serialize_run_trace_entries(trace: Any) -> list[dict[str, Any]]:
    """Project one run trace into assistant timeline entries."""
    run = getattr(trace, "run", None)
    if run is None:
        return []

    content_items: list[dict[str, Any]] = []
    for part in getattr(trace, "parts", None) or []:
        serialized = serialize_run_part(part)
        if serialized.get("part_type") == "assistant_message":
            content_items.append(
                {
                    "type": "text",
                    "text": serialized.get("content") or "",
                    "created_at": serialized.get("created_at"),
                    "part_id": serialized.get("part_id"),
                }
            )

    for artifact in serialize_run_artifacts(trace):
        content_items.append(
            {
                "type": _content_type_from_artifact(artifact),
                "status": artifact.get("status"),
                "title": artifact.get("title") or artifact.get("path") or artifact.get("tool_name"),
                "detail": artifact.get("detail") or artifact.get("path") or "",
                "artifact": artifact,
                "created_at": artifact.get("created_at"),
            }
        )

    content_items.sort(key=lambda item: (_entry_time(item), str(item.get("type") or "")))
    return [
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "entry_id": f"run:{getattr(run, 'run_id', None)}",
            "entry_type": "assistant",
            "role": "assistant",
            "run_id": getattr(run, "run_id", None),
            "session_id": getattr(run, "session_id", None),
            "status": getattr(run, "status", None),
            "created_at": getattr(run, "created_at", None),
            "updated_at": getattr(run, "updated_at", None),
            "content": content_items,
            "metadata": json_safe_payload(dict(getattr(run, "metadata", {}) or {})),
        }
    ]


def serialize_session_entries(messages: list[Any], traces: list[Any] | None = None) -> list[dict[str, Any]]:
    """Merge stored messages and run traces into a chronological session timeline."""
    entries = [serialize_message_entry(message, index=index + 1) for index, message in enumerate(messages or [])]
    for trace in traces or []:
        entries.extend(serialize_run_trace_entries(trace))
    entries.sort(key=lambda item: (_entry_time(item), str(item.get("entry_id") or "")))
    return entries
