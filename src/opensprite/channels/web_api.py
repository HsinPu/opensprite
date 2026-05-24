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

from . import web_api_control, web_api_evals, web_api_runs, web_api_sessions
from ..bus.session_commands import session_command_catalog
from ..config import Config
from ..evals.harness_live_scenarios import run_controlled_harness_scenarios
from ..evals.task_completion import run_live_task_completion_eval, run_task_completion_smoke
from ..agent.run_trace import RunTraceRecorder
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
        return await web_api_runs.handle_run_events(self.adapter, request)

    async def handle_runs(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_runs(self.adapter, request)

    async def handle_background_processes(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_background_processes(
            self.adapter,
            request,
            coerce_states=_coerce_states,
            serialize_background_process=_serialize_background_process,
        )

    async def handle_long_task_eval_status(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_long_task_eval_status(
            self.adapter,
            request,
            long_task_eval_metrics=_long_task_eval_metrics,
            long_task_eval_scenarios=_long_task_eval_scenarios,
        )

    async def handle_long_task_eval_smoke(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_long_task_eval_smoke(
            self.adapter,
            request,
            long_task_eval_metrics=_long_task_eval_metrics,
        )

    async def handle_long_task_eval_controlled(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_long_task_eval_controlled(
            self.adapter,
            request,
            long_task_controlled_command=_long_task_controlled_command,
            serialize_background_process=_serialize_background_process,
        )

    async def handle_task_completion_eval_smoke(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_task_completion_eval_smoke(request)

    async def handle_task_completion_eval_run(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_task_completion_eval_run(
            self.adapter,
            request,
            active_llm_model_info=_active_llm_model_info,
        )

    async def handle_harness_controlled_eval(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_harness_controlled_eval(
            self.adapter,
            request,
            persist_harness_controlled_eval_trace=_persist_harness_controlled_eval_trace,
        )

    async def handle_task_completion_eval_history(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_task_completion_eval_history(self.adapter, request)

    async def handle_task_completion_eval_history_delete(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_task_completion_eval_history_delete(self.adapter, request)

    async def handle_task_completion_eval_history_clear(self, request: web.Request) -> web.Response:
        return await web_api_evals.handle_task_completion_eval_history_clear(self.adapter, request)

    async def handle_sessions(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_sessions(self.adapter, request)

    async def handle_sessions_delete(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_sessions_delete(
            self.adapter,
            request,
            visible_session_ids=_visible_session_ids,
            delete_conversation_sessions=_delete_conversation_sessions,
        )

    async def handle_session_timeline(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_session_timeline(self.adapter, request)

    async def handle_session_status(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_session_status(self.adapter, request)

    async def handle_storage_status(self, request: web.Request) -> web.Response:
        return await web_api_sessions.handle_storage_status(self.adapter, request)

    async def handle_run_trace(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_trace(self.adapter, request)

    async def handle_run_summary(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_summary(self.adapter, request)

    async def handle_run_cancel(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_cancel(self.adapter, request)

    async def handle_run_file_change_revert(self, request: web.Request) -> web.Response:
        return await web_api_runs.handle_run_file_change_revert(self.adapter, request)

    async def handle_permissions(self, request: web.Request) -> web.Response:
        return await web_api_control.handle_permissions(self.adapter, request)

    async def handle_permission_approve(self, request: web.Request) -> web.Response:
        return await web_api_control.handle_permission_approve(self.adapter, request)

    async def handle_permission_deny(self, request: web.Request) -> web.Response:
        return await web_api_control.handle_permission_deny(self.adapter, request)

    async def handle_worktree_cleanup(self, request: web.Request) -> web.Response:
        return await web_api_control.handle_worktree_cleanup(self.adapter, request)


def _coerce_states(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    states = tuple(item.strip() for item in str(raw).split(",") if item.strip())
    return states or None


async def _persist_harness_controlled_eval_trace(adapter: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
    storage_getter = getattr(adapter, "_get_storage", None)
    storage = storage_getter() if callable(storage_getter) else None
    if storage is None:
        return None
    create_run = getattr(storage, "create_run", None)
    if not callable(create_run):
        return None
    session_id = "web:evaluations"
    run_id = f"harness-eval-{uuid4().hex[:12]}"
    recorder = RunTraceRecorder(storage=storage, message_bus_getter=lambda: None)
    await recorder.create_run(
        session_id,
        run_id,
        status="completed" if payload.get("ok") else "failed",
        metadata={"kind": payload.get("kind"), "source": "settings_eval"},
    )
    await recorder.emit_event(
        session_id,
        run_id,
        "harness_eval.completed" if payload.get("ok") else "harness_eval.failed",
        payload,
        channel=getattr(adapter, "channel_instance_id", None),
        external_chat_id=None,
    )
    await recorder.record_harness_eval_result_part(session_id, run_id, payload)
    await recorder.update_run_status(
        session_id,
        run_id,
        "completed" if payload.get("ok") else "failed",
        metadata={"kind": payload.get("kind"), "source": "settings_eval"},
        finished_at=time.time(),
    )
    return {"session_id": session_id, "run_id": run_id, "part_type": "harness_eval_result"}


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
