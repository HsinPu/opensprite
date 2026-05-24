"""Run-related HTTP API helpers for the web adapter."""

from __future__ import annotations

from typing import Any, Callable

from aiohttp import web

from ..runs.schema import (
    serialize_diff_summary,
    serialize_file_change,
    serialize_run_artifacts,
    serialize_run_event_counts,
    serialize_run_events,
    serialize_run_part,
    serialize_run_summary,
)
from ..runs.session_entries import serialize_run_trace_entries


async def handle_run_events(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()

    run_id = adapter._coerce_optional_text(request.match_info.get("run_id"))
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    if run_id is None or session_id is None:
        raise web.HTTPBadRequest(text="Both run_id and session_id are required")

    run = await storage.get_run(session_id, run_id)
    if run is None:
        raise web.HTTPNotFound(text="Run not found")

    events = await storage.get_run_events(session_id, run_id)
    serialized_events = serialize_run_events(events)
    return web.json_response(
        {
            "run_id": run_id,
            "session_id": session_id,
            "events": serialized_events,
            "event_counts": serialize_run_event_counts(events, serialized_events),
        }
    )


async def handle_runs(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    if session_id is None:
        raise web.HTTPBadRequest(text="session_id is required")

    runs = await storage.get_runs(session_id, limit=adapter._coerce_limit(request.query.get("limit")))
    return web.json_response({"session_id": session_id, "runs": [adapter._serialize_run(run) for run in runs]})


async def handle_run_trace(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    run_id = adapter._coerce_optional_text(request.match_info.get("run_id"))
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    if run_id is None or session_id is None:
        raise web.HTTPBadRequest(text="Both run_id and session_id are required")

    trace = await storage.get_run_trace(session_id, run_id)
    if trace is None:
        raise web.HTTPNotFound(text="Run not found")

    serialized_events = serialize_run_events(trace.events)
    return web.json_response(
        {
            "run": adapter._serialize_run(trace.run),
            "events": serialized_events,
            "event_counts": serialize_run_event_counts(trace.events, serialized_events),
            "parts": [serialize_run_part(part) for part in trace.parts],
            "file_changes": [serialize_file_change(change) for change in trace.file_changes],
            "diff_summary": serialize_diff_summary(trace),
            "artifacts": serialize_run_artifacts(trace),
            "entries": serialize_run_trace_entries(trace),
        }
    )


async def handle_run_summary(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    run_id = adapter._coerce_optional_text(request.match_info.get("run_id"))
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    if run_id is None or session_id is None:
        raise web.HTTPBadRequest(text="Both run_id and session_id are required")

    trace = await storage.get_run_trace(session_id, run_id)
    if trace is None:
        raise web.HTTPNotFound(text="Run not found")

    return web.json_response(serialize_run_summary(trace))


async def handle_run_cancel(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    agent = adapter._get_agent()
    if agent is None or not hasattr(agent, "request_run_cancel"):
        raise web.HTTPServiceUnavailable(text="Run cancellation is not available")

    run_id = adapter._coerce_optional_text(request.match_info.get("run_id"))
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    if run_id is None or session_id is None:
        raise web.HTTPBadRequest(text="Both run_id and session_id are required")

    run = await storage.get_run(session_id, run_id)
    if run is None:
        raise web.HTTPNotFound(text="Run not found")

    accepted = await agent.request_run_cancel(
        session_id,
        run_id,
        channel=adapter.channel_instance_id,
        external_chat_id=adapter._external_chat_id_from_session(session_id),
    )
    if not accepted:
        raise web.HTTPConflict(text="Run is not active")

    cancel_session = getattr(adapter.mq, "cancel_session", None)
    if callable(cancel_session):
        await cancel_session(session_id)

    return web.json_response({"ok": True, "session_id": session_id, "run_id": run_id, "status": "cancelling"})


async def handle_run_file_change_revert(adapter: Any, request: web.Request) -> web.Response:
    agent = adapter._get_agent()
    revert = getattr(agent, "revert_run_file_change", None) if agent is not None else None
    if not callable(revert):
        raise web.HTTPServiceUnavailable(text="Run file-change revert is not available")

    run_id = adapter._coerce_optional_text(request.match_info.get("run_id"))
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    if run_id is None or session_id is None:
        raise web.HTTPBadRequest(text="Both run_id and session_id are required")
    try:
        change_id = int(str(request.match_info.get("change_id") or ""))
    except ValueError as exc:
        raise web.HTTPBadRequest(text="change_id must be an integer") from exc

    body = await adapter._read_json_body(request)
    dry_run = bool(body.get("dry_run", True))
    result = await revert(session_id, run_id, change_id, dry_run=dry_run)
    status = str(result.get("status") or "")
    if status == "not_found":
        raise web.HTTPNotFound(text=str(result.get("reason") or "File change not found"))
    return web.json_response({"ok": bool(result.get("ok")), "revert": adapter._json_safe(result)})
