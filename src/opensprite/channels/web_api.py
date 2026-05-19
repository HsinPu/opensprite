"""HTTP API handlers for the web channel adapter."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

from aiohttp import web

from ..bus.session_commands import session_command_catalog
from ..config import Config
from ..evals.harness_live_scenarios import run_controlled_harness_scenarios
from ..evals.task_completion import run_live_task_completion_eval, run_task_completion_smoke
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
from ..tools.shell_runtime import CapturedOutputChunk, start_shell_process


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

    async def handle_background_processes(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        states = _coerce_states(request.query.get("states") or request.query.get("state"))
        limit = adapter._coerce_limit(request.query.get("limit"), default=20, maximum=100)
        processes = await storage.list_background_processes(
            owner_session_id=session_id,
            states=states,
            limit=limit,
        )
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

    async def handle_long_task_eval_status(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._get_storage()
        storage_available = storage is not None
        processes = []
        counts: dict[str, int] = {}
        if storage_available:
            processes = await storage.list_background_processes(limit=100)
            for process in processes:
                counts[process.state] = counts.get(process.state, 0) + 1

        return web.json_response(
            {
                "ok": True,
                "ready": storage_available,
                "storage_available": storage_available,
                "background_process_counts": counts,
                "recent_background_processes": len(processes),
                "recommended_metrics": _long_task_eval_metrics(),
                "recommended_scenarios": _long_task_eval_scenarios(),
            }
        )

    async def handle_long_task_eval_smoke(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._get_storage()
        checks: list[dict[str, Any]] = []

        storage_available = storage is not None
        checks.append(
            {
                "id": "storage_available",
                "label": "Run storage is available",
                "ok": storage_available,
                "detail": "Required for persisted run traces and background process lifecycle records.",
            }
        )

        background_process_api = False
        process_counts: dict[str, int] = {}
        if storage_available:
            processes = await storage.list_background_processes(limit=100)
            background_process_api = True
            for process in processes:
                process_counts[process.state] = process_counts.get(process.state, 0) + 1
        checks.append(
            {
                "id": "background_process_api",
                "label": "Background process records are queryable",
                "ok": background_process_api,
                "detail": f"Observed {sum(process_counts.values())} recent process record(s).",
            }
        )

        checks.append(
            {
                "id": "run_event_schema",
                "label": "Background process run events are registered",
                "ok": True,
                "detail": "Expected events: background_process.started, background_process.completed, background_process.lost.",
            }
        )

        ok = all(check["ok"] for check in checks)
        return web.json_response(
            {
                "ok": ok,
                "checks": checks,
                "background_process_counts": process_counts,
                "metrics": _long_task_eval_metrics(),
            }
        )

    async def handle_long_task_eval_controlled(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        storage = adapter._get_storage()
        manager = getattr(agent, "background_process_manager", None) if agent is not None else None
        if storage is None:
            raise web.HTTPServiceUnavailable(text="Run trace storage is not available")
        if manager is None:
            raise web.HTTPServiceUnavailable(text="Background process manager is not available")

        session_id = adapter._coerce_optional_text(
            request.query.get("session_id"),
            default="web:long-task-eval",
        )
        run_id = f"run_long_task_eval_{uuid4().hex}"
        created_at = time.time()
        await storage.create_run(
            session_id,
            run_id,
            status="running",
            metadata={"kind": "long_task_eval_controlled"},
            created_at=created_at,
        )

        command = _long_task_controlled_command()
        output_chunks: list[CapturedOutputChunk] = []
        process, read_tasks = await start_shell_process(command, cwd=None, output_chunks=output_chunks)
        background_session = manager.register_session(
            command=command,
            cwd=None,
            process=process,
            read_tasks=read_tasks,
            output_chunks=output_chunks,
            timeout_seconds=5.0,
            drain_timeout=5.0,
            exit_notifier=None,
            notify_on_exit=False,
            owner_session_id=session_id,
            owner_run_id=run_id,
            owner_channel=adapter._channel_from_session(session_id),
            owner_external_chat_id=adapter._external_chat_id_from_session(session_id),
        )
        await asyncio.sleep(0)

        if background_session.watch_task is not None:
            done, _ = await asyncio.wait({background_session.watch_task}, timeout=5.0)
            if not done:
                await manager.kill_session(background_session.session_id)

        stored_process = await storage.get_background_process(background_session.session_id)
        events = await storage.get_run_events(session_id, run_id)
        event_types = [event.event_type for event in events]
        await storage.update_run_status(
            session_id,
            run_id,
            "completed" if stored_process is not None and stored_process.exit_code == 0 else "failed",
            metadata={"kind": "long_task_eval_controlled", "event_types": event_types},
            finished_at=time.time(),
        )

        checks = [
            {
                "id": "process_record_created",
                "label": "Background process record was created",
                "ok": stored_process is not None,
                "detail": background_session.session_id,
            },
            {
                "id": "process_completed",
                "label": "Controlled process completed successfully",
                "ok": (
                    stored_process is not None
                    and stored_process.state == "exited"
                    and stored_process.exit_code == 0
                ),
                "detail": (
                    f"state={getattr(stored_process, 'state', None)} "
                    f"exit_code={getattr(stored_process, 'exit_code', None)}"
                ),
            },
            {
                "id": "started_event_recorded",
                "label": "Started event was recorded",
                "ok": "background_process.started" in event_types,
                "detail": ", ".join(event_types) or "none",
            },
            {
                "id": "completed_event_recorded",
                "label": "Completed event was recorded",
                "ok": "background_process.completed" in event_types,
                "detail": ", ".join(event_types) or "none",
            },
            {
                "id": "output_tail_captured",
                "label": "Output tail was captured",
                "ok": (
                    stored_process is not None
                    and "opensprite-long-task-controlled:done" in stored_process.output_tail
                ),
                "detail": (
                    getattr(stored_process, "output_tail", "")[-160:]
                    if stored_process is not None
                    else "missing process"
                ),
            },
        ]

        return web.json_response(
            {
                "ok": all(check["ok"] for check in checks),
                "session_id": session_id,
                "run_id": run_id,
                "process_session_id": background_session.session_id,
                "checks": checks,
                "event_types": event_types,
                "process": _serialize_background_process(stored_process) if stored_process is not None else None,
            }
        )

    async def handle_task_completion_eval_smoke(self, request: web.Request) -> web.Response:
        return web.json_response(run_task_completion_smoke())

    async def handle_task_completion_eval_run(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        agent = adapter._get_agent()
        storage = adapter._get_storage()
        if agent is None:
            raise web.HTTPServiceUnavailable(text="Agent is not available")
        if storage is None:
            raise web.HTTPServiceUnavailable(text="Run trace storage is not available")
        timeout_seconds = adapter._coerce_limit(request.query.get("timeout"), default=45, maximum=120)
        payload = await run_live_task_completion_eval(
            agent=agent,
            storage=storage,
            channel=adapter.channel_instance_id,
            timeout_seconds=float(timeout_seconds),
            model_info=_active_llm_model_info(adapter),
        )
        return web.json_response(payload)

    async def handle_harness_controlled_eval(self, request: web.Request) -> web.Response:
        return web.json_response(run_controlled_harness_scenarios())

    async def handle_task_completion_eval_history(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        limit = adapter._coerce_limit(request.query.get("limit"), default=20, maximum=100)
        history = await storage.list_eval_runs(kind="task_completion", limit=limit)
        return web.json_response({"ok": True, "history": [item.to_payload() for item in history]})

    async def handle_task_completion_eval_history_delete(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        eval_id = adapter._coerce_optional_text(request.match_info.get("eval_id"))
        if eval_id is None:
            raise web.HTTPBadRequest(text="eval_id is required")
        deleted = await storage.delete_eval_run(eval_id, kind="task_completion")
        if not deleted:
            raise web.HTTPNotFound(text="Eval run not found")
        return web.json_response({"ok": True, "eval_id": eval_id, "deleted": 1})

    async def handle_task_completion_eval_history_clear(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        deleted = await storage.clear_eval_runs(kind="task_completion")
        return web.json_response({"ok": True, "deleted": deleted})

    async def handle_sessions(self, request: web.Request) -> web.Response:
        adapter = self.adapter
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

    async def handle_sessions_delete(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        session_id = adapter._coerce_optional_text(
            request.query.get("session_id") or request.match_info.get("session_id")
        )
        if session_id is not None:
            deleted = await _delete_conversation_sessions(adapter, storage, [session_id])
            if deleted <= 0:
                raise web.HTTPNotFound(text="Session not found")
            return web.json_response({"ok": True, "session_id": session_id, "deleted": deleted})

        channel_filter = adapter._coerce_optional_text(request.query.get("channel"), default=adapter.channel_instance_id)
        session_ids = await _visible_session_ids(storage)
        if channel_filter is not None and channel_filter.lower() != "all":
            prefix = f"{channel_filter}:"
            session_ids = [candidate for candidate in session_ids if candidate.startswith(prefix)]
        deleted = await _delete_conversation_sessions(adapter, storage, session_ids)
        return web.json_response({"ok": True, "channel": channel_filter or "all", "deleted": deleted})

    async def handle_session_timeline(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if session_id is None:
            raise web.HTTPBadRequest(text="session_id is required")

        message_limit = adapter._coerce_limit(request.query.get("messages"), default=200, maximum=500)
        run_limit = adapter._coerce_limit(request.query.get("runs"), default=50, maximum=100)
        messages = await storage.get_messages(session_id, limit=message_limit)
        runs = await storage.get_runs(session_id, limit=run_limit)
        traces = []
        for run in runs:
            trace = await storage.get_run_trace(session_id, run.run_id)
            if trace is not None:
                traces.append(trace)

        return web.json_response(
            {
                "session_id": session_id,
                "messages": [adapter._serialize_message(message) for message in messages],
                "runs": [adapter._serialize_run(run) for run in runs],
                "entries": serialize_session_entries(messages, traces),
            }
        )

    async def handle_session_status(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        session_id = adapter._coerce_optional_text(request.query.get("session_id"))
        if session_id is not None:
            return web.json_response({"status": adapter._serialize_session_status(session_id)})

        service = adapter._get_session_status_service()
        statuses = [] if service is None else [adapter._serialize_session_status(item.session_id) for item in service.list()]
        return web.json_response({"statuses": statuses})

    async def handle_storage_status(self, request: web.Request) -> web.Response:
        adapter = self.adapter
        storage = adapter._require_storage()
        config = Config.load(adapter._get_config_path())
        session_ids = await storage.get_all_sessions()
        visible_session_ids = [session_id for session_id in session_ids if ":subagent:" not in session_id]
        message_count = 0
        run_count = 0
        for session_id in session_ids:
            message_count += await storage.get_message_count(session_id)
            run_count += len(await storage.get_runs(session_id))

        storage_path = getattr(storage, "db_path", None) or getattr(config.storage, "path", "")
        return web.json_response(
            {
                "storage": {
                    "type": config.storage.type,
                    "path": str(storage_path or ""),
                    "provider": type(storage).__name__,
                },
                "counts": {
                    "sessions": len(visible_session_ids),
                    "raw_sessions": len(session_ids),
                    "messages": message_count,
                    "runs": run_count,
                },
            }
        )

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

        session_id = adapter._coerce_optional_text(body.get("session_id") or request.query.get("session_id"))
        run_id = adapter._coerce_optional_text(body.get("run_id") or request.query.get("run_id"))
        emit_run_event = getattr(agent, "_emit_run_event", None) if agent is not None else None
        can_trace = callable(emit_run_event) and session_id is not None and run_id is not None
        if can_trace:
            await emit_run_event(
                session_id,
                run_id,
                "worktree_cleanup.started",
                {"sandbox_path": sandbox_path, "status": "running"},
                channel=adapter.channel_instance_id,
                external_chat_id=adapter._external_chat_id_from_session(session_id),
            )
        try:
            result = cleanup(sandbox_path)
        except Exception as exc:
            if can_trace:
                await emit_run_event(
                    session_id,
                    run_id,
                    "worktree_cleanup.failed",
                    {
                        "sandbox_path": sandbox_path,
                        "status": "failed",
                        "ok": False,
                        "reason": str(exc) or exc.__class__.__name__,
                    },
                    channel=adapter.channel_instance_id,
                    external_chat_id=adapter._external_chat_id_from_session(session_id),
                )
            raise
        if can_trace:
            ok = bool(result.get("ok"))
            await emit_run_event(
                session_id,
                run_id,
                "worktree_cleanup.completed" if ok else "worktree_cleanup.failed",
                {
                    "sandbox_path": result.get("sandbox_path") or sandbox_path,
                    "status": result.get("status"),
                    "ok": ok,
                    "reason": result.get("reason"),
                },
                channel=adapter.channel_instance_id,
                external_chat_id=adapter._external_chat_id_from_session(session_id),
            )
        return web.json_response({"ok": bool(result.get("ok")), "cleanup": adapter._json_safe(result)})


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


def _active_llm_model_info(adapter: Any) -> dict[str, Any]:
    agent = adapter._get_agent()
    explicit = getattr(agent, "eval_model_info", None) if agent is not None else None
    if isinstance(explicit, dict):
        return _model_info_payload(explicit)

    provider_id = ""
    provider_name = ""
    model = ""
    configured: bool | None = None
    context_window_tokens: int | None = None

    provider = getattr(agent, "provider", None) if agent is not None else None
    get_default_model = getattr(provider, "get_default_model", None)
    if callable(get_default_model):
        model = str(get_default_model() or "").strip()

    llm_config = getattr(agent, "llm_config", None) if agent is not None else None
    if llm_config is not None:
        provider_id = str(getattr(llm_config, "default", "") or "").strip()
        get_active = getattr(llm_config, "get_active", None)
        active = get_active() if callable(get_active) else None
        if active is not None:
            provider_name = str(getattr(active, "provider", "") or "").strip()
            model = model or str(getattr(active, "model", "") or "").strip()
            context_window_tokens = getattr(active, "context_window_tokens", None)
        configured = bool(getattr(agent, "llm_configured", False))

    if not model:
        try:
            config = Config.load(adapter._get_config_path())
            active = config.llm.get_active()
            provider_id = provider_id or str(config.llm.default or "").strip()
            provider_name = provider_name or str(getattr(active, "provider", "") or "").strip()
            model = str(getattr(active, "model", "") or "").strip()
            configured = bool(config.is_llm_configured)
            context_window_tokens = getattr(active, "context_window_tokens", None)
        except Exception:
            pass

    return _model_info_payload(
        {
            "provider_id": provider_id,
            "provider": provider_name or provider_id,
            "model": model,
            "configured": configured,
            "context_window_tokens": context_window_tokens,
        }
    )


def _model_info_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in ("provider_id", "provider", "model", "configured", "context_window_tokens")
        if key in value and value[key] not in (None, "")
    }


def _long_task_eval_metrics() -> list[dict[str, str]]:
    return [
        {"id": "expected_outcome_accuracy", "label": "Expected outcome accuracy"},
        {"id": "completion_rate", "label": "Completion rate"},
        {"id": "tool_call_error_rate", "label": "Tool call error rate"},
        {"id": "retry_recovery_rate", "label": "Retry recovery rate"},
        {"id": "summary_delivery_rate", "label": "Summary delivery rate"},
        {"id": "lost_process_rate", "label": "Lost process rate"},
        {"id": "ui_visibility_rate", "label": "Web/API visibility rate"},
    ]


def _long_task_eval_scenarios() -> list[dict[str, str]]:
    return [
        {"id": "short_success", "label": "Short successful background process"},
        {"id": "expected_failure", "label": "Expected failing background process"},
        {"id": "restart_recovery", "label": "Gateway restart marks stale work as lost"},
        {"id": "parallel_processes", "label": "Multiple concurrent background processes"},
        {"id": "agent_summary", "label": "Agent summary after process completion"},
    ]


def _long_task_controlled_command() -> str:
    code = (
        "import time; "
        "print('opensprite-long-task-controlled:start', flush=True); "
        "time.sleep(0.05); "
        "print('opensprite-long-task-controlled:done', flush=True)"
    )
    argv = [sys.executable, "-u", "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


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
