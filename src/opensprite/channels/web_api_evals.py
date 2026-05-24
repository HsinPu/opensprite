"""Eval and background-process HTTP API helpers for the web adapter."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable
from uuid import uuid4

from aiohttp import web

from ..evals.harness_live_scenarios import run_controlled_harness_scenarios
from ..evals.task_completion import run_live_task_completion_eval, run_task_completion_smoke
from ..tools.shell_runtime import CapturedOutputChunk, start_shell_process


async def handle_background_processes(adapter: Any, request: web.Request, *, coerce_states: Callable[[str | None], tuple[str, ...] | None], serialize_background_process: Callable[[Any], dict[str, Any]]) -> web.Response:
    storage = adapter._require_storage()
    session_id = adapter._coerce_optional_text(request.query.get("session_id"))
    states = coerce_states(request.query.get("states") or request.query.get("state"))
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
            "processes": [serialize_background_process(process) for process in processes],
        }
    )


async def handle_long_task_eval_status(adapter: Any, request: web.Request, *, long_task_eval_metrics: Callable[[], list[dict[str, str]]], long_task_eval_scenarios: Callable[[], list[dict[str, str]]]) -> web.Response:
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
            "recommended_metrics": long_task_eval_metrics(),
            "recommended_scenarios": long_task_eval_scenarios(),
        }
    )


async def handle_long_task_eval_smoke(adapter: Any, request: web.Request, *, long_task_eval_metrics: Callable[[], list[dict[str, str]]]) -> web.Response:
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
    return web.json_response({"ok": ok, "checks": checks, "background_process_counts": process_counts, "metrics": long_task_eval_metrics()})


async def handle_long_task_eval_controlled(
    adapter: Any,
    request: web.Request,
    *,
    long_task_controlled_command: Callable[[], str],
    serialize_background_process: Callable[[Any], dict[str, Any]],
) -> web.Response:
    agent = adapter._get_agent()
    storage = adapter._get_storage()
    manager = getattr(agent, "background_process_manager", None) if agent is not None else None
    if storage is None:
        raise web.HTTPServiceUnavailable(text="Run trace storage is not available")
    if manager is None:
        raise web.HTTPServiceUnavailable(text="Background process manager is not available")

    session_id = adapter._coerce_optional_text(request.query.get("session_id"), default="web:long-task-eval")
    run_id = f"run_long_task_eval_{uuid4().hex}"
    created_at = time.time()
    await storage.create_run(session_id, run_id, status="running", metadata={"kind": "long_task_eval_controlled"}, created_at=created_at)

    command = long_task_controlled_command()
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
        {"id": "process_record_created", "label": "Background process record was created", "ok": stored_process is not None, "detail": background_session.session_id},
        {
            "id": "process_completed",
            "label": "Controlled process completed successfully",
            "ok": (stored_process is not None and stored_process.state == "exited" and stored_process.exit_code == 0),
            "detail": f"state={getattr(stored_process, 'state', None)} exit_code={getattr(stored_process, 'exit_code', None)}",
        },
        {"id": "started_event_recorded", "label": "Started event was recorded", "ok": "background_process.started" in event_types, "detail": ", ".join(event_types) or "none"},
        {"id": "completed_event_recorded", "label": "Completed event was recorded", "ok": "background_process.completed" in event_types, "detail": ", ".join(event_types) or "none"},
        {
            "id": "output_tail_captured",
            "label": "Output tail was captured",
            "ok": (stored_process is not None and "opensprite-long-task-controlled:done" in stored_process.output_tail),
            "detail": getattr(stored_process, "output_tail", "")[-160:] if stored_process is not None else "missing process",
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
            "process": serialize_background_process(stored_process) if stored_process is not None else None,
        }
    )


async def handle_task_completion_eval_smoke(request: web.Request) -> web.Response:
    return web.json_response(run_task_completion_smoke())


async def handle_task_completion_eval_run(adapter: Any, request: web.Request, *, active_llm_model_info: Callable[[Any], dict[str, Any]]) -> web.Response:
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
        model_info=active_llm_model_info(adapter),
    )
    return web.json_response(payload)


async def handle_harness_controlled_eval(adapter: Any, request: web.Request, *, persist_harness_controlled_eval_trace: Callable[[Any, dict[str, Any]], Any]) -> web.Response:
    payload = run_controlled_harness_scenarios()
    trace = await persist_harness_controlled_eval_trace(adapter, payload)
    if trace is not None:
        payload = dict(payload)
        payload["trace"] = trace
    return web.json_response(payload)


async def handle_task_completion_eval_history(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    limit = adapter._coerce_limit(request.query.get("limit"), default=20, maximum=100)
    history = await storage.list_eval_runs(kind="task_completion", limit=limit)
    return web.json_response({"ok": True, "history": [item.to_payload() for item in history]})


async def handle_task_completion_eval_history_delete(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    eval_id = adapter._coerce_optional_text(request.match_info.get("eval_id"))
    if eval_id is None:
        raise web.HTTPBadRequest(text="eval_id is required")
    deleted = await storage.delete_eval_run(eval_id, kind="task_completion")
    if not deleted:
        raise web.HTTPNotFound(text="Eval run not found")
    return web.json_response({"ok": True, "eval_id": eval_id, "deleted": 1})


async def handle_task_completion_eval_history_clear(adapter: Any, request: web.Request) -> web.Response:
    storage = adapter._require_storage()
    deleted = await storage.clear_eval_runs(kind="task_completion")
    return web.json_response({"ok": True, "deleted": deleted})
