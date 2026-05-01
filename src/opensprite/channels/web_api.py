"""HTTP API handlers for the web channel adapter."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..bus.session_commands import session_command_catalog
from ..run_schema import (
    serialize_file_change,
    serialize_run_artifacts,
    serialize_run_event_counts,
    serialize_run_events,
    serialize_run_part,
    serialize_run_summary,
    serialize_diff_summary,
)
from ..session_entries import serialize_run_trace_entries


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
        adapter = self.adapter
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

    async def handle_runs(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if session_id is None:
            raise web.HTTPBadRequest(text="session_id is required")

        runs = await storage.get_runs(session_id, limit=adapter._coerce_limit(request.query.get("limit")))
        return web.json_response(
            {
                "session_id": session_id,
                "runs": [adapter._serialize_run(run) for run in runs],
            }
        )

    async def handle_sessions(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        session_limit = adapter._coerce_limit(request.query.get("limit"), default=30, maximum=100)
        message_limit = adapter._coerce_limit(request.query.get("messages"), default=50, maximum=200)
        channel_filter = adapter._coerce_optional_text(request.query.get("channel"))
        session_ids = await storage.get_all_sessions()
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

    async def handle_session_status(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if session_id is not None:
            return web.json_response({"status": adapter._serialize_session_status(session_id)})

        service = adapter._get_session_status_service()
        statuses = [] if service is None else [adapter._serialize_session_status(item.session_id) for item in service.list()]
        return web.json_response({"statuses": statuses})

    async def handle_run_trace(self, request: web.Request) -> web.Response:
        adapter = self.adapter
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

    async def handle_run_summary(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        run_id = adapter._coerce_optional_text(request.match_info.get("run_id"))
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if run_id is None or session_id is None:
            raise web.HTTPBadRequest(text="Both run_id and session_id are required")

        trace = await storage.get_run_trace(session_id, run_id)
        if trace is None:
            raise web.HTTPNotFound(text="Run not found")

        return web.json_response(serialize_run_summary(trace))

    async def handle_run_cancel(self, request: web.Request) -> web.Response:
        adapter = self.adapter
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

    async def handle_run_file_change_revert(self, request: web.Request) -> web.Response:
        adapter = self.adapter
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

    async def handle_permissions(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        pending_requests = getattr(agent, "pending_permission_requests", None) if agent is not None else None
        if not callable(pending_requests):
            raise web.HTTPServiceUnavailable(text="Permission requests are not available")
        permissions = [adapter._serialize_permission_request(item) for item in pending_requests()]
        return web.json_response({"permissions": permissions})

    async def handle_permission_approve(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        approve = getattr(agent, "approve_permission_request", None) if agent is not None else None
        if not callable(approve):
            raise web.HTTPServiceUnavailable(text="Permission approvals are not available")

        request_id = adapter._coerce_optional_text(request.match_info.get("request_id"))
        if request_id is None:
            raise web.HTTPBadRequest(text="request_id is required")
        permission = await approve(request_id)
        if permission is None:
            raise web.HTTPNotFound(text="Permission request not found")
        return web.json_response({"ok": True, "permission": adapter._serialize_permission_request(permission)})

    async def handle_permission_deny(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        deny = getattr(agent, "deny_permission_request", None) if agent is not None else None
        if not callable(deny):
            raise web.HTTPServiceUnavailable(text="Permission denials are not available")

        request_id = adapter._coerce_optional_text(request.match_info.get("request_id"))
        if request_id is None:
            raise web.HTTPBadRequest(text="request_id is required")
        body = await adapter._read_json_body(request)
        reason = adapter._coerce_optional_text(body.get("reason"), default="user denied approval") or "user denied approval"
        permission = await deny(request_id, reason=reason)
        if permission is None:
            raise web.HTTPNotFound(text="Permission request not found")
        return web.json_response({"ok": True, "permission": adapter._serialize_permission_request(permission)})

    async def handle_worktree_cleanup(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        cleanup = getattr(agent, "cleanup_worktree_sandbox", None) if agent is not None else None
        if not callable(cleanup):
            raise web.HTTPServiceUnavailable(text="Worktree sandbox cleanup is not available")

        body = await adapter._read_json_body(request)
        sandbox_path = adapter._coerce_optional_text(body.get("sandbox_path"))
        if sandbox_path is None:
            raise web.HTTPBadRequest(text="sandbox_path is required")
        result = cleanup(sandbox_path)
        return web.json_response({"ok": bool(result.get("ok")), "cleanup": adapter._json_safe(result)})
