"""Session and storage HTTP API helpers for the web adapter."""

from __future__ import annotations

from typing import Any, Callable

from aiohttp import web

from ..runs.schema import serialize_diff_summary


async def handle_sessions(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    session_limit = adapter._coerce_limit(request.query.get("limit"), default=30, maximum=100)
    message_limit = adapter._coerce_limit(request.query.get("messages"), default=50, maximum=200)
    channel_filter = adapter._coerce_optional_text(request.query.get("channel"))
    session_ids = await storage.get_all_sessions()
    session_ids = [session_id for session_id in session_ids if ":subagent:" not in session_id]
    if channel_filter is None:
        session_prefix = f"{adapter.channel_instance_id}:"
        session_ids = [session_id for session_id in session_ids if session_id.startswith(session_prefix)]
    elif channel_filter.lower() != "all":
        session_prefix = f"{channel_filter}:"
        session_ids = [session_id for session_id in session_ids if session_id.startswith(session_prefix)]

    sessions = [
        await adapter._serialize_session_summary(storage, session_id, message_limit=message_limit)
        for session_id in session_ids
    ]
    sessions.sort(key=lambda item: (item["updated_at"], item["session_id"]), reverse=True)
    return web.json_response({"sessions": sessions[:session_limit], "channel": channel_filter or adapter.channel_instance_id})


async def handle_sessions_delete(
    adapter: Any,
    request: web.Request,
    *,
    visible_session_ids: Callable[[Any], Any],
    delete_conversation_sessions: Callable[[Any, Any, list[str]], Any],
) -> web.Response:
    storage = adapter._require_storage()
    session_id = adapter._coerce_optional_text(request.query.get("session_id") or request.match_info.get("session_id"))
    if session_id is not None:
        deleted = await delete_conversation_sessions(adapter, storage, [session_id])
        if deleted <= 0:
            raise web.HTTPNotFound(text="Session not found")
        return web.json_response({"ok": True, "session_id": session_id, "deleted": deleted})

    channel_filter = adapter._coerce_optional_text(request.query.get("channel"), default=adapter.channel_instance_id)
    session_ids = await visible_session_ids(storage)
    if channel_filter is not None and channel_filter.lower() != "all":
        prefix = f"{channel_filter}:"
        session_ids = [candidate for candidate in session_ids if candidate.startswith(prefix)]
    deleted = await delete_conversation_sessions(adapter, storage, session_ids)
    return web.json_response({"ok": True, "channel": channel_filter or "all", "deleted": deleted})


async def handle_session_status(adapter: Any, request: web.Request) -> web.Response:
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    if session_id is not None:
        return web.json_response({"status": adapter._serialize_session_status(session_id)})

    service = adapter._get_session_status_service()
    statuses = [] if service is None else [adapter._serialize_session_status(item.session_id) for item in service.list()]
    return web.json_response({"statuses": statuses})

async def serialize_session_summary(adapter: Any, storage: Any, session_id: str, *, message_limit: int) -> dict[str, Any]:
    messages = await storage.get_messages(session_id, limit=message_limit)
    display_messages = [message for message in messages if str(getattr(message, "role", "") or "") in {"user", "assistant"}]
    latest_runs = await storage.get_runs(session_id, limit=1)
    latest_traces = []
    for run in latest_runs:
        get_run_trace = getattr(storage, "get_run_trace", None)
        trace = await get_run_trace(session_id, run.run_id) if callable(get_run_trace) else None
        if trace is not None:
            latest_traces.append(trace)
    get_work_state = getattr(storage, "get_work_state", None)
    work_state = await get_work_state(session_id) if callable(get_work_state) else None
    external_chat_id = adapter._external_chat_id_from_session(session_id)
    fallback_title = external_chat_id or session_id
    return {
        "session_id": session_id,
        "channel": adapter._channel_from_session(session_id),
        "external_chat_id": external_chat_id,
        "title": adapter._session_title(display_messages, fallback_title),
        "updated_at": adapter._session_updated_at(messages, latest_runs),
        "status": adapter._serialize_session_status(session_id),
        "message_count": await storage.get_message_count(session_id),
        "messages": [adapter._serialize_message(message) for message in display_messages],
        "runs": [adapter._serialize_run(run) for run in latest_runs],
        "entries": serialize_session_entries(display_messages, latest_traces),
        "diff_summary": serialize_diff_summary(latest_traces[0]) if latest_traces else None,
        "work_state": adapter._serialize_work_state(work_state),
    }
