"""HTTP API handlers for the web channel adapter."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from . import web_api_control, web_api_runs, web_api_sessions
from ..bus.session_commands import session_command_catalog
from ..runs.schema import (
    serialize_file_change,
    serialize_run_artifacts,
    serialize_run_event_counts,
    serialize_run_events,
    serialize_run_part,
    serialize_run_summary,
    serialize_diff_summary,
)
from ..runs.session_entries import serialize_run_trace_entries, serialize_session_entries


class WebApiHandlers:
    """Focused HTTP handlers that delegate adapter-specific behavior."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    async def handle_command_catalog(self, request: web.Request) -> web.Response:
        return web.json_response(session_command_catalog())

    async def handle_curator_status(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        get_status = getattr(agent, "get_curator_status", None) if agent is not None else None
        if not callable(get_status):
            raise web.HTTPServiceUnavailable(text="Curator status is not available")

        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if session_id is None:
            raise web.HTTPBadRequest(text="session_id is required")
        status = await get_status(session_id)
        if status is None:
            raise web.HTTPServiceUnavailable(text="Curator status is not available")
        return web.json_response({"ok": True, "session_id": session_id, "status": adapter._json_safe(status)})

    async def handle_curator_history(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        get_history = getattr(agent, "get_curator_history", None) if agent is not None else None
        if not callable(get_history):
            raise web.HTTPServiceUnavailable(text="Curator history is not available")

        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if session_id is None:
            raise web.HTTPBadRequest(text="session_id is required")
        limit = adapter._coerce_limit(request.query.get("limit"), default=5, maximum=20)
        history = await get_history(session_id, limit=limit)
        if history is None:
            raise web.HTTPServiceUnavailable(text="Curator history is not available")
        return web.json_response({"ok": True, "session_id": session_id, "history": adapter._json_safe(history)})

    async def handle_curator_action(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if session_id is None:
            raise web.HTTPBadRequest(text="session_id is required")

        action = adapter._coerce_optional_text(request.match_info.get("action"), default="") or ""
        if action == "run":
            method = getattr(agent, "run_curator_now", None) if agent is not None else None
            if not callable(method):
                raise web.HTTPServiceUnavailable(text="Curator run is not available")
            channel = adapter._channel_from_session(session_id)
            if channel == "unknown":
                raise web.HTTPBadRequest(text="session_id must include a channel prefix")
            scope = adapter._coerce_optional_text(request.query.get("scope"))
            try:
                status = await method(
                    session_id,
                    scope=scope,
                    channel=channel,
                    external_chat_id=adapter._external_chat_id_from_session(session_id),
                )
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc)) from exc
        elif action == "pause":
            method = getattr(agent, "pause_curator", None) if agent is not None else None
            if not callable(method):
                raise web.HTTPServiceUnavailable(text="Curator pause is not available")
            status = await method(session_id)
        elif action == "resume":
            method = getattr(agent, "resume_curator", None) if agent is not None else None
            if not callable(method):
                raise web.HTTPServiceUnavailable(text="Curator resume is not available")
            status = await method(session_id)
        else:
            raise web.HTTPNotFound(text="Unknown curator action")

        if status is None:
            raise web.HTTPServiceUnavailable(text="Curator is not available")
        return web.json_response(
            {
                "ok": True,
                "session_id": session_id,
                "action": action,
                "status": adapter._json_safe(status),
            }
        )

    async def handle_run_events(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_events(self.adapter, request)

    async def handle_runs(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_runs(self.adapter, request)

    async def handle_background_processes(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        states = _coerce_states(request.query.get("states") or request.query.get("state"))
        limit = adapter._coerce_limit(request.query.get("limit"), default=20, maximum=100)
        processes = await storage.list_background_processes(owner_session_id=session_id, states=states, limit=limit)
        counts: dict[str, int] = {}
        for process in processes:
            counts[process.state] = counts.get(process.state, 0) + 1
        return web.json_response(
            {
                "session_id": session_id,
                "states": list(states or []),
                "counts": counts,
                "processes": [_serialize_background_process(process) for process in processes],
            }
        )

    async def handle_sessions(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_sessions(self.adapter, request)

    async def handle_sessions_delete(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_sessions_delete(
            self.adapter,
            request,
            visible_session_ids=_visible_session_ids,
            delete_conversation_sessions=_delete_conversation_sessions,
        )

    async def handle_session_status(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_session_status(self.adapter, request)

    async def handle_run_trace(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_trace(self.adapter, request)

    async def handle_run_summary(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_summary(self.adapter, request)

    async def handle_run_cancel(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_cancel(self.adapter, request)

    async def handle_run_file_change_revert(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_file_change_revert(self.adapter, request)

    async def handle_worktree_cleanup(self, request: web.Request) -> web.Response:
        return await web_api_control.handle_worktree_cleanup(self.adapter, request)


def _coerce_states(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    states = tuple(item.strip() for item in str(raw).split(",") if item.strip())
    return states or None


async def _visible_session_ids(storage: Any) -> list[str]:
    return [session_id for session_id in await storage.get_all_sessions() if ":subagent:" not in session_id]


async def _delete_conversation_sessions(adapter: Any, storage: Any, root_session_ids: list[str]) -> int:
    all_session_ids = await storage.get_all_sessions()
    existing = set(all_session_ids)
    targets: list[str] = []
    for root_session_id in root_session_ids:
        if root_session_id not in existing:
            continue
        targets.append(root_session_id)
        child_prefix = f"{root_session_id}:subagent:"
        targets.extend(session_id for session_id in all_session_ids if session_id.startswith(child_prefix))

    unique_targets = list(dict.fromkeys(targets))
    for session_id in unique_targets:
        await _delete_one_conversation_session(adapter, storage, session_id)
    return len(unique_targets)


async def _delete_one_conversation_session(adapter: Any, storage: Any, session_id: str) -> None:
    cancel_session = getattr(getattr(adapter, "mq", None), "cancel_session", None)
    if callable(cancel_session):
        try:
            await cancel_session(session_id)
        except Exception:
            pass

    agent = adapter._get_agent()
    reset_history = getattr(agent, "reset_history", None) if agent is not None else None
    if callable(reset_history):
        await reset_history(session_id)
    else:
        await storage.clear_messages(session_id)

    status_service = adapter._get_session_status_service()
    if status_service is not None:
        status_service.set(session_id, "idle")


def _serialize_background_process(process: Any) -> dict[str, Any]:
    return {
        "process_session_id": process.process_session_id,
        "owner_session_id": process.owner_session_id,
        "owner_run_id": process.owner_run_id,
        "owner_channel": process.owner_channel,
        "owner_external_chat_id": process.owner_external_chat_id,
        "pid": process.pid,
        "command": process.command,
        "cwd": process.cwd,
        "state": process.state,
        "termination_reason": process.termination_reason,
        "exit_code": process.exit_code,
        "notify_mode": process.notify_mode,
        "output_tail": process.output_tail,
        "output_path": process.output_path,
        "metadata": dict(process.metadata or {}),
        "started_at": process.started_at,
        "updated_at": process.updated_at,
        "finished_at": process.finished_at,
    }
