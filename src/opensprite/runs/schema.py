"""Shared run event and artifact envelope helpers."""

from __future__ import annotations

from typing import Any

from ..utils.json_safe import json_safe_payload

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
    "reasoning_delta": "llm",
    "task_intent.detected": "work",
    "work_plan.created": "work",
    "work_progress.updated": "work",
    "task_checklist.updated": "work",
    "curator.started": "work",
    "curator.job.started": "work",
    "curator.job.completed": "work",
    "curator.job.skipped": "work",
    "curator.failed": "work",
    "curator.completed": "work",
    "subagent.started": "work",
    "subagent.group.started": "work",
    "subagent.group.completed": "work",
    "subagent.group.failed": "work",
    "subagent.group.cancelled": "work",
    "subagent.completed": "work",
    "subagent.failed": "work",
    "subagent.cancelled": "work",
    "workflow.started": "work",
    "workflow.step.started": "work",
    "workflow.step.completed": "work",
    "workflow.step.failed": "work",
    "workflow.completed": "work",
    "workflow.failed": "work",
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
    "tool_input_delta": "tool",
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
    if normalized in {"curator.started", "curator.job.started"}:
        return explicit or "running"
    if normalized == "curator.failed":
        return explicit or "failed"
    if normalized == "curator.job.skipped":
        return explicit or "skipped"
    if normalized == "subagent.started":
        return explicit or "running"
    if normalized == "subagent.group.started":
        return explicit or "running"
    if normalized == "subagent.group.failed":
        return explicit or "failed"
    if normalized == "subagent.group.completed":
        return explicit or "completed"
    if normalized == "subagent.group.cancelled":
        return explicit or "cancelled"
    if normalized == "subagent.failed":
        return explicit or "failed"
    if normalized == "subagent.completed":
        return explicit or "completed"
    if normalized == "subagent.cancelled":
        return explicit or "cancelled"
    if normalized in {"workflow.started", "workflow.step.started"}:
        return explicit or "running"
    if normalized in {"workflow.completed", "workflow.step.completed"}:
        return explicit or "completed"
    if normalized in {"workflow.failed", "workflow.step.failed"}:
        return explicit or "failed"
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

    if normalized == "task_checklist.updated":
        todos = data.get("todos") if isinstance(data.get("todos"), list) else []
        completed = sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "completed")
        total = len(todos)
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": "task_checklist",
            "artifact_type": "task_checklist",
            "kind": "task",
            "status": status,
            "title": "Task checklist",
            "detail": f"{completed}/{total} completed" if total else "No task steps",
            "metadata": data,
        }

    if normalized in {"curator.started", "curator.failed", "curator.completed"}:
        detail = _text(data.get("summary") or data.get("error") or data.get("message"))
        if not detail and normalized == "curator.started":
            total_jobs = _non_negative_int(data.get("total_jobs"))
            detail = f"{total_jobs} job(s) queued" if total_jobs else "Background curator tasks started."
        if not detail:
            return None
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": "curator",
            "artifact_type": "curator",
            "kind": "work",
            "status": status,
            "title": "Curator",
            "detail": detail,
            "metadata": data,
        }

    if normalized in {"curator.job.started", "curator.job.completed", "curator.job.skipped"}:
        job = _text(data.get("job"))
        label = _text(data.get("label") or job or "curator job")
        detail = _text(data.get("summary") or data.get("message") or data.get("reason"))
        if normalized == "curator.job.completed" and not detail:
            detail = f"Updated {label}."
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": f"curator_job:{job or label}",
            "artifact_type": "curator_job",
            "kind": "work",
            "status": status,
            "title": f"Curator job: {label}",
            "detail": detail,
            "metadata": data,
        }

    if normalized in {"subagent.started", "subagent.completed", "subagent.failed", "subagent.cancelled"}:
        prompt_type = _text(data.get("prompt_type") or "subagent")
        task_id = _text(data.get("task_id"))
        child_run_id = _text(data.get("child_run_id"))
        detail = _text(data.get("summary") or data.get("error") or data.get("message"))
        if not detail and normalized == "subagent.started":
            detail = f"Task {task_id or child_run_id or prompt_type} started."
        if not detail and normalized == "subagent.completed":
            detail = f"Task {task_id or child_run_id or prompt_type} completed."
        if not detail and normalized == "subagent.failed":
            detail = f"Task {task_id or child_run_id or prompt_type} failed."
        if not detail and normalized == "subagent.cancelled":
            detail = f"Task {task_id or child_run_id or prompt_type} cancelled."
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": f"subagent:{task_id or child_run_id or prompt_type}",
            "artifact_type": "subagent_task",
            "kind": "work",
            "status": status,
            "title": f"Subagent: {prompt_type}",
            "detail": detail,
            "metadata": data,
        }

    if normalized in {
        "subagent.group.started",
        "subagent.group.completed",
        "subagent.group.failed",
        "subagent.group.cancelled",
    }:
        group_id = _text(data.get("group_id"))
        total_tasks = _non_negative_int(data.get("total_tasks"))
        detail = _text(data.get("summary") or data.get("error") or data.get("message"))
        if not detail and normalized == "subagent.group.started":
            detail = f"{total_tasks} parallel subagent task(s) queued." if total_tasks else "Parallel subagent tasks queued."
        if not detail and normalized == "subagent.group.completed":
            detail = f"Completed {total_tasks} parallel subagent task(s)." if total_tasks else "Parallel subagent group completed."
        if not detail and normalized == "subagent.group.failed":
            detail = "Parallel subagent group failed."
        if not detail and normalized == "subagent.group.cancelled":
            detail = "Parallel subagent group cancelled."
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": f"subagent_group:{group_id or 'parallel'}",
            "artifact_type": "subagent_group",
            "kind": "work",
            "status": status,
            "title": "Parallel subagents",
            "detail": detail,
            "metadata": data,
        }

    if normalized in {"workflow.started", "workflow.completed", "workflow.failed"}:
        workflow = _text(data.get("workflow") or "workflow")
        workflow_run_id = _text(data.get("workflow_run_id"))
        detail = _text(data.get("summary") or data.get("error") or data.get("message"))
        if not detail and normalized == "workflow.started":
            total_steps = _non_negative_int(data.get("total_steps"))
            detail = f"Started workflow with {total_steps} step(s)." if total_steps else "Workflow started."
        if not detail and normalized == "workflow.completed":
            detail = "Workflow completed."
        if not detail and normalized == "workflow.failed":
            detail = "Workflow failed."
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": f"workflow:{workflow_run_id or workflow}",
            "artifact_type": "workflow",
            "kind": "work",
            "status": status,
            "title": f"Workflow: {workflow}",
            "detail": detail,
            "metadata": data,
        }

    if normalized in {"workflow.step.started", "workflow.step.completed", "workflow.step.failed"}:
        workflow_run_id = _text(data.get("workflow_run_id"))
        step_id = _text(data.get("step_id") or data.get("label") or "step")
        label = _text(data.get("label") or step_id or "workflow step")
        detail = _text(data.get("summary") or data.get("error") or data.get("task_preview"))
        if not detail and normalized == "workflow.step.started":
            detail = f"Started {label}."
        if not detail and normalized == "workflow.step.completed":
            detail = f"Completed {label}."
        if not detail and normalized == "workflow.step.failed":
            detail = f"Failed {label}."
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "artifact_id": f"workflow_step:{workflow_run_id or 'workflow'}:{step_id or label}",
            "artifact_type": "workflow_step",
            "kind": "work",
            "status": status,
            "title": f"Workflow step: {label}",
            "detail": detail,
            "metadata": data,
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


def serialize_run_event_counts(events: list[Any], serialized_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe trace event retention so clients can show compacted payloads honestly."""
    original = list(events or [])
    text_total = sum(1 for event in original if _is_text_delta_event(event))
    text_returned = sum(
        1
        for event in serialized_events
        if _text(event.get("event_type")) in _TEXT_DELTA_EVENTS or event.get("kind") == "text"
    )
    return {
        "total": len(original),
        "returned": len(serialized_events),
        "compacted": max(0, len(original) - len(serialized_events)),
        "text_total": text_total,
        "text_returned": text_returned,
        "max_events": MAX_SERIALIZED_RUN_EVENTS,
        "max_text_events": MAX_SERIALIZED_TEXT_EVENTS,
    }


def run_part_kind(part_type: str) -> str:
    normalized = _text(part_type)
    if normalized == "assistant_message":
        return "text"
    if normalized in {"tool_call", "tool_result"}:
        return "tool"
    if normalized == "context_compaction":
        return "system"
    if normalized == "task_checklist":
        return "task"
    if normalized == "llm_step":
        return "llm"
    if normalized == "worktree_sandbox":
        return "work"
    return "other"


def serialize_work_state_todos(state: Any) -> list[dict[str, Any]]:
    """Project StoredWorkState steps into a stable session task checklist."""
    if state is None:
        return []

    todos: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(content: Any, status: str, *, priority: str = "medium") -> None:
        text = _text(content)
        if not text or text == "not set" or text in seen:
            return
        seen.add(text)
        todos.append(
            {
                "id": f"task:{len(todos) + 1}",
                "content": text,
                "status": status,
                "priority": priority,
                "updated_at": getattr(state, "updated_at", None),
            }
        )

    for step in getattr(state, "completed_steps", ()) or ():
        add(step, "completed")
    add(getattr(state, "current_step", ""), "in_progress", priority="high")
    add(getattr(state, "next_step", ""), "pending")
    for step in getattr(state, "pending_steps", ()) or ():
        add(step, "pending")
    for step in getattr(state, "steps", ()) or ():
        add(step, "pending")
    for blocker in getattr(state, "blockers", ()) or ():
        add(blocker, "cancelled", priority="high")

    return todos


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
    if part_type == "task_checklist":
        todos = safe_metadata.get("todos") if isinstance(safe_metadata.get("todos"), list) else []
        completed = sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "completed")
        total = len(todos)
        detail = f"{completed}/{total} completed" if total else "No task steps"
    if part_type == "llm_step":
        title = _text(safe_metadata.get("model")) or "LLM step"
        detail = f"attempt {safe_metadata.get('attempt')} · {safe_metadata.get('estimated_input_tokens')} input tokens"
    if part_type == "worktree_sandbox":
        title = "Worktree sandbox"
        detail = _text(safe_metadata.get("status") or safe_metadata.get("reason"))
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


def _count_diff_lines(diff: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in str(diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def serialize_diff_summary(trace: Any) -> dict[str, Any]:
    """Summarize file mutations for user-facing run/session cards."""
    file_changes = list(getattr(trace, "file_changes", None) or [])
    paths: list[str] = []
    action_counts: dict[str, int] = {}
    additions = 0
    deletions = 0
    for change in file_changes:
        path = str(getattr(change, "path", "") or "").strip()
        if path and path not in paths:
            paths.append(path)
        action = str(getattr(change, "action", "") or "unknown").strip() or "unknown"
        action_counts[action] = action_counts.get(action, 0) + 1
        added, deleted = _count_diff_lines(str(getattr(change, "diff", "") or ""))
        additions += added
        deletions += deleted

    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "changed_files": len(paths),
        "change_count": len(file_changes),
        "additions": additions,
        "deletions": deletions,
        "paths": paths,
        "actions": action_counts,
    }


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


def _summarize_review(completion: dict[str, Any]) -> dict[str, Any]:
    required = bool(completion.get("review_required"))
    attempted = bool(completion.get("review_attempted"))
    passed = bool(completion.get("review_passed"))
    status = "not_required"
    if required:
        if passed:
            status = "passed"
        elif attempted:
            status = "failed"
        else:
            status = "not_attempted"
    prompt_types = [
        str(item).strip()
        for item in (completion.get("review_prompt_types") if isinstance(completion.get("review_prompt_types"), list) else [])
        if str(item).strip()
    ]
    return {
        "required": required,
        "attempted": attempted,
        "passed": passed,
        "status": status,
        "summary": _text(completion.get("review_summary")),
        "prompt_types": prompt_types,
        "finding_count": _non_negative_int(completion.get("review_finding_count")),
    }


def _summarize_parallel_delegation(events: list[Any]) -> dict[str, Any]:
    group_events: dict[str, dict[str, Any]] = {}
    ordered_group_ids: list[str] = []
    for event in events:
        event_type = str(getattr(event, "event_type", "") or "")
        if event_type not in {
            "subagent.group.started",
            "subagent.group.completed",
            "subagent.group.failed",
            "subagent.group.cancelled",
        }:
            continue
        payload = dict(getattr(event, "payload", {}) or {})
        group_id = _text(payload.get("group_id"))
        if not group_id:
            continue
        created_at = getattr(event, "created_at", None)
        if group_id not in group_events:
            ordered_group_ids.append(group_id)
        group_events[group_id] = {
            "event_type": event_type,
            "created_at": created_at,
            "payload": payload,
        }

    groups: list[dict[str, Any]] = []
    for group_id in ordered_group_ids:
        entry = group_events.get(group_id)
        if not entry:
            continue
        payload = entry["payload"]
        tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
        groups.append(
            {
                "group_id": group_id,
                "status": _text(payload.get("status") or run_event_status(entry["event_type"], payload)),
                "total_tasks": _non_negative_int(payload.get("total_tasks")),
                "max_parallel": _non_negative_int(payload.get("max_parallel")),
                "completed_count": _non_negative_int(payload.get("completed_count")),
                "failed_count": _non_negative_int(payload.get("failed_count")),
                "cancelled_count": _non_negative_int(payload.get("cancelled_count")),
                "summary": _text(payload.get("summary") or payload.get("error") or payload.get("message")),
                "tasks": [json_safe_payload(item) for item in tasks if isinstance(item, dict)],
                "created_at": entry["created_at"],
            }
        )

    return {
        "group_count": len(groups),
        "task_count": sum(int(group.get("total_tasks") or 0) for group in groups),
        "groups": groups,
    }


def _summarize_structured_subagents(events: list[Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    by_prompt_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total_sections = 0
    total_items = 0
    total_findings = 0
    total_questions = 0
    total_residual_risks = 0

    for event in events:
        if str(getattr(event, "event_type", "") or "") != "subagent.completed":
            continue
        payload = dict(getattr(event, "payload", {}) or {})
        structured_output = payload.get("structured_output")
        if not isinstance(structured_output, dict):
            continue
        prompt_type = _text(payload.get("prompt_type") or structured_output.get("prompt_type") or "subagent")
        status = _text(structured_output.get("status") or "inconclusive") or "inconclusive"
        section_count = _non_negative_int(structured_output.get("section_count"))
        item_count = _non_negative_int(structured_output.get("item_count"))
        finding_count = _non_negative_int(structured_output.get("finding_count"))
        question_count = _non_negative_int(structured_output.get("question_count"))
        residual_risk_count = _non_negative_int(structured_output.get("residual_risk_count"))

        by_prompt_type[prompt_type] = by_prompt_type.get(prompt_type, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        total_sections += section_count
        total_items += item_count
        total_findings += finding_count
        total_questions += question_count
        total_residual_risks += residual_risk_count
        results.append(
            {
                "task_id": _text(payload.get("task_id")) or None,
                "prompt_type": prompt_type,
                "status": status,
                "summary": _text(structured_output.get("summary") or payload.get("summary")),
                "section_count": section_count,
                "item_count": item_count,
                "finding_count": finding_count,
                "question_count": question_count,
                "residual_risk_count": residual_risk_count,
                "created_at": getattr(event, "created_at", None),
            }
        )

    return {
        "total": len(results),
        "by_prompt_type": by_prompt_type,
        "by_status": by_status,
        "total_sections": total_sections,
        "total_items": total_items,
        "total_findings": total_findings,
        "total_questions": total_questions,
        "total_residual_risks": total_residual_risks,
        "results": results,
    }


def _summarize_workflows(events: list[Any]) -> dict[str, Any]:
    workflow_events: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for event in events:
        event_type = str(getattr(event, "event_type", "") or "")
        if event_type not in {"workflow.completed", "workflow.failed"}:
            continue
        payload = dict(getattr(event, "payload", {}) or {})
        workflow_run_id = _text(payload.get("workflow_run_id"))
        if not workflow_run_id:
            continue
        if workflow_run_id not in workflow_events:
            ordered_ids.append(workflow_run_id)
        workflow_events[workflow_run_id] = {
            "event_type": event_type,
            "created_at": getattr(event, "created_at", None),
            "payload": payload,
        }

    results: list[dict[str, Any]] = []
    by_workflow: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for workflow_run_id in ordered_ids:
        entry = workflow_events.get(workflow_run_id)
        if not entry:
            continue
        payload = entry["payload"]
        workflow_id = _text(payload.get("workflow") or "workflow") or "workflow"
        status = _text(payload.get("status") or run_event_status(entry["event_type"], payload)) or "unknown"
        by_workflow[workflow_id] = by_workflow.get(workflow_id, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        results.append(
            {
                "workflow_run_id": workflow_run_id,
                "workflow": workflow_id,
                "status": status,
                "task_preview": _text(payload.get("task_preview")),
                "total_steps": _non_negative_int(payload.get("total_steps")),
                "completed_steps": _non_negative_int(payload.get("completed_steps")),
                "failed_steps": _non_negative_int(payload.get("failed_steps")),
                "summary": _workflow_result_summary(payload, status=status),
                "created_at": entry["created_at"],
            }
        )

    return {
        "total": len(results),
        "by_workflow": by_workflow,
        "by_status": by_status,
        "results": results,
    }


def _workflow_result_summary(payload: dict[str, Any], *, status: str) -> str:
    summary = _text(payload.get("summary") or payload.get("error") or payload.get("message"))
    step_label = _text(payload.get("next_step_label") or payload.get("next_step_id"))
    error = _text(payload.get("error"))
    if status == "cancelled":
        if step_label and summary:
            return f"Resume with the {step_label} step. {summary}"
        if step_label:
            return f"Resume with the {step_label} step."
        return summary
    if status == "failed":
        if step_label and error:
            return f"Resolve the {step_label} step failure: {error}"
        if step_label:
            return f"Resolve the {step_label} step failure."
    return summary


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
    review = _summarize_review(completion)
    parallel_delegation = _summarize_parallel_delegation(events)
    structured_subagents = _summarize_structured_subagents(events)
    workflows = _summarize_workflows(events)
    had_tool_error = _metadata_bool(run_metadata, "had_tool_error")
    warnings: list[str] = []
    if had_tool_error:
        warnings.append("tool_error")
    if verification["attempted"] and not verification["passed"]:
        warnings.append("verification_not_passed")
    if review["required"] and not review["passed"]:
        warnings.append("review_not_passed")
    if any(str(group.get("status") or "") in {"failed", "error"} for group in parallel_delegation.get("groups", [])):
        warnings.append("parallel_delegation_failed")
    if any(str(group.get("status") or "") in {"cancelled", "cancelling"} for group in parallel_delegation.get("groups", [])):
        warnings.append("parallel_delegation_cancelled")
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
        "diff_summary": serialize_diff_summary(trace),
        "verification": verification,
        "review": review,
        "parallel_delegation": parallel_delegation,
        "structured_subagents": structured_subagents,
        "workflows": workflows,
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
