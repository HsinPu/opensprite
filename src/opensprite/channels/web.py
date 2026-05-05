"""
opensprite/channels/web.py - WebSocket chat adapter

Expose a lightweight WebSocket endpoint that feeds browser messages into
MessageQueue and routes assistant replies back to the same web session.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import WSMsgType, web

from .identity import build_session_id, normalize_identifier
from ..bus.events import RunEvent, SessionStatusEvent
from ..bus.message import AssistantMessage, MessageAdapter, UserMessage
from ..config import Config, MessagesConfig
from ..config.channel_settings import (
    ChannelSettingsError,
    ChannelSettingsNotFound,
    ChannelSettingsService,
    ChannelSettingsValidationError,
)
from ..config.mcp_settings import (
    MCPSettingsError,
    MCPSettingsNotFound,
    MCPSettingsService,
    MCPSettingsValidationError,
)
from ..config.media_settings import MediaSettingsService
from ..config.provider_settings import (
    ProviderSettingsConflict,
    ProviderSettingsError,
    ProviderSettingsNotFound,
    ProviderSettingsService,
    ProviderSettingsValidationError,
)
from ..config.schedule_settings import (
    ScheduleSettingsError,
    ScheduleSettingsNotFound,
    ScheduleSettingsService,
    ScheduleSettingsValidationError,
)
from ..cron import CronJob, CronSchedule
from ..cron.presentation import format_cron_timestamp, format_cron_timing
from ..runs.schema import serialize_diff_summary, serialize_run_event, serialize_work_state_todos
from ..runs.session_entries import serialize_session_entries
from ..tools.approval import classify_permission_request
from ..utils.log import logger
from .web_api import WebApiHandlers


class WebAdapter(MessageAdapter):
    """WebSocket adapter for browser-based chat clients."""

    DEFAULT_CONFIG = {
        "host": "127.0.0.1",
        "port": 8765,
        "path": "/ws",
        "health_path": "/healthz",
        "max_message_size": 1024 * 1024,
        "frontend_auto_build": True,
        "frontend_auto_install": True,
        "frontend_build_timeout": 120,
        "frontend_install_timeout": 300,
    }

    def __init__(self, mq=None, config: dict[str, Any] | None = None):
        self.mq = mq
        self.messages = getattr(mq, "messages", None) or MessagesConfig()
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.channel_type = "web"
        self.channel_instance_id = normalize_identifier(str(self.config.get("id") or "web"), fallback="web")
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._shutdown_event = asyncio.Event()
        self._started_event = asyncio.Event()
        self._session_connections: dict[str, web.WebSocketResponse] = {}
        self._socket_sessions: dict[web.WebSocketResponse, set[str]] = {}
        self._api = WebApiHandlers(self)
        self._maybe_build_frontend()
        self._frontend_dir = self._resolve_frontend_dir()

    def _get_host(self) -> str:
        return str(self.config.get("host", self.DEFAULT_CONFIG["host"]))

    def _get_port(self) -> int:
        return int(self.config.get("port", self.DEFAULT_CONFIG["port"]))

    def _get_max_message_size(self) -> int:
        return int(self.config.get("max_message_size", self.DEFAULT_CONFIG["max_message_size"]))

    def _get_frontend_build_timeout(self) -> int:
        return int(self.config.get("frontend_build_timeout", self.DEFAULT_CONFIG["frontend_build_timeout"]))

    def _get_frontend_install_timeout(self) -> int:
        return int(self.config.get("frontend_install_timeout", self.DEFAULT_CONFIG["frontend_install_timeout"]))

    def _get_path(self, key: str) -> str:
        raw = str(self.config.get(key, self.DEFAULT_CONFIG[key]) or self.DEFAULT_CONFIG[key]).strip() or "/"
        return raw if raw.startswith("/") else f"/{raw}"

    @staticmethod
    def _is_frontend_source_dir(path: Path) -> bool:
        return (path / "package.json").is_file()

    def _resolve_frontend_source_dir(self) -> Path | None:
        configured = str(self.config.get("frontend_source_dir", "") or "").strip()
        configured_static = str(self.config.get("static_dir", "") or "").strip()
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured).expanduser())
        if configured_static:
            candidates.append(Path(configured_static).expanduser())

        if not configured_static:
            module_path = Path(__file__).resolve()
            candidates.extend(
                [
                    module_path.parents[3] / "apps" / "web",
                    Path.cwd() / "apps" / "web",
                ]
            )

        for candidate in candidates:
            resolved = candidate.expanduser().resolve(strict=False)
            if self._is_frontend_source_dir(resolved):
                return resolved
        return None

    @staticmethod
    def _trim_process_output(value: str | None, limit: int = 2000) -> str:
        if not value:
            return ""
        stripped = value.strip()
        if len(stripped) <= limit:
            return stripped
        return f"...{stripped[-limit:]}"

    def _resolve_npm_executable(self) -> str | None:
        preferred = "npm.cmd" if os.name == "nt" else "npm"
        return shutil.which(preferred) or shutil.which("npm")

    def _is_frontend_auto_build_enabled(self) -> bool:
        value = self.config.get("frontend_auto_build", self.DEFAULT_CONFIG["frontend_auto_build"])
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    def _is_frontend_auto_install_enabled(self) -> bool:
        value = self.config.get("frontend_auto_install", self.DEFAULT_CONFIG["frontend_auto_install"])
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    def _frontend_dependencies_ready(self, source_dir: Path) -> bool:
        bin_dir = source_dir / "node_modules" / ".bin"
        return (bin_dir / "vite").is_file() or (bin_dir / "vite.cmd").is_file()

    def _run_frontend_command(self, source_dir: Path, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=source_dir,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )

    def _maybe_install_frontend_dependencies(self, source_dir: Path, npm: str) -> bool:
        if self._frontend_dependencies_ready(source_dir):
            return True
        if not self._is_frontend_auto_install_enabled():
            logger.warning("Skipping web frontend dependency install because frontend_auto_install is disabled")
            return False

        install_command = [npm, "ci"] if (source_dir / "package-lock.json").is_file() else [npm, "install"]
        logger.info("Installing web frontend dependencies before build: {}", source_dir)
        try:
            result = self._run_frontend_command(source_dir, install_command, self._get_frontend_install_timeout())
        except subprocess.TimeoutExpired:
            logger.warning("Web frontend dependency install timed out after {} seconds", self._get_frontend_install_timeout())
            return False
        except OSError as exc:
            logger.warning("Web frontend dependency install could not start: {}", exc)
            return False

        if result.returncode != 0:
            logger.warning(
                "Web frontend dependency install failed with exit code {} | stdout={} | stderr={}",
                result.returncode,
                self._trim_process_output(result.stdout),
                self._trim_process_output(result.stderr),
            )
            return False

        return True

    def _maybe_build_frontend(self) -> None:
        if not self._is_frontend_auto_build_enabled():
            return

        source_dir = self._resolve_frontend_source_dir()
        if source_dir is None:
            return

        npm = self._resolve_npm_executable()
        if npm is None:
            logger.warning("Skipping web frontend build because npm was not found")
            return

        if not self._maybe_install_frontend_dependencies(source_dir, npm):
            return

        logger.info("Building web frontend before gateway startup: {}", source_dir)
        try:
            result = self._run_frontend_command(source_dir, [npm, "run", "build"], self._get_frontend_build_timeout())
        except subprocess.TimeoutExpired:
            logger.warning("Web frontend build timed out after {} seconds", self._get_frontend_build_timeout())
            return
        except OSError as exc:
            logger.warning("Web frontend build could not start: {}", exc)
            return

        if result.returncode != 0:
            logger.warning(
                "Web frontend build failed with exit code {} | stdout={} | stderr={}",
                result.returncode,
                self._trim_process_output(result.stdout),
                self._trim_process_output(result.stderr),
            )
            return

        logger.info("Web frontend build completed")

    def _resolve_frontend_dir(self) -> Path | None:
        configured = str(self.config.get("static_dir", "") or "").strip()
        candidates: list[Path] = []
        if configured:
            resolved = Path(configured).expanduser().resolve(strict=False)
            candidates.append(resolved / "dist" if self._is_frontend_source_dir(resolved) else resolved)

        module_path = Path(__file__).resolve()
        candidates.extend(
            [
                module_path.parents[3] / "apps" / "web" / "dist",
                Path.cwd() / "apps" / "web" / "dist",
            ]
        )

        for candidate in candidates:
            resolved = candidate.expanduser().resolve(strict=False)
            if (resolved / "index.html").is_file():
                return resolved
        return None

    def _resolve_frontend_asset(self, asset_path: str) -> Path:
        if self._frontend_dir is None:
            raise web.HTTPNotFound()

        target = (self._frontend_dir / asset_path).resolve(strict=False)
        if not target.is_relative_to(self._frontend_dir) or not target.is_file():
            raise web.HTTPNotFound()
        return target

    def _build_session_id(self, external_chat_id: str | None) -> str:
        normalized_external_chat_id = self._coerce_optional_text(external_chat_id, default="default") or "default"
        return build_session_id(self.channel_instance_id, normalized_external_chat_id)

    @property
    def bound_port(self) -> int | None:
        if self.site is None:
            return None
        server = getattr(self.site, "_server", None)
        sockets = getattr(server, "sockets", None) or []
        if not sockets:
            return None
        return int(sockets[0].getsockname()[1])

    async def wait_until_started(self, timeout: float = 5.0) -> None:
        """Wait until the HTTP server starts listening."""
        await asyncio.wait_for(self._started_event.wait(), timeout=timeout)

    def _bind_session(self, session_id: str, ws: web.WebSocketResponse) -> None:
        self._session_connections[session_id] = ws
        self._socket_sessions.setdefault(ws, set()).add(session_id)

    def _unbind_socket(self, ws: web.WebSocketResponse) -> None:
        for session_id in self._socket_sessions.pop(ws, set()):
            if self._session_connections.get(session_id) is ws:
                self._session_connections.pop(session_id, None)

    @staticmethod
    def _coerce_metadata(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return json.loads(json.dumps(value, default=str))

    def _get_storage(self) -> Any | None:
        return getattr(getattr(self.mq, "agent", None), "storage", None)

    def _get_agent(self) -> Any | None:
        return getattr(self.mq, "agent", None)

    def _get_session_status_service(self) -> Any | None:
        return getattr(self.mq, "session_status", None)

    def _get_config_path(self) -> Path:
        agent = self._get_agent()
        raw_path = getattr(agent, "config_path", None) if agent is not None else None
        if raw_path is not None:
            return Path(raw_path).expanduser().resolve()
        config = Config.load(None)
        return Path(config.source_path or Path.home() / ".opensprite" / "opensprite.json").resolve()

    def _get_provider_settings(self) -> ProviderSettingsService:
        return ProviderSettingsService(self._get_config_path())

    def _get_channel_settings(self) -> ChannelSettingsService:
        return ChannelSettingsService(self._get_config_path())

    def _get_schedule_settings(self) -> ScheduleSettingsService:
        return ScheduleSettingsService(self._get_config_path())

    def _get_mcp_settings(self) -> MCPSettingsService:
        return MCPSettingsService(self._get_config_path())

    def _get_media_settings(self) -> MediaSettingsService:
        return MediaSettingsService(self._get_config_path())

    def _reload_agent_llm_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted LLM settings to the running agent when possible."""
        if not force and not payload.get("restart_required"):
            return payload

        updated = dict(payload)
        agent = self._get_agent()
        reload_llm = getattr(agent, "reload_llm_from_config", None) if agent is not None else None
        if not callable(reload_llm):
            updated["runtime_reloaded"] = False
            return updated

        try:
            runtime = reload_llm(Config.load(self._get_config_path()))
        except Exception as exc:
            logger.warning("LLM runtime reload failed after settings change: {}", exc)
            updated["runtime_reloaded"] = False
            updated["reload_error"] = str(exc)
            return updated

        updated["restart_required"] = False
        updated["runtime_reloaded"] = True
        updated["runtime"] = self._json_safe(runtime)
        return updated

    async def _reload_channels_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted channel settings to running adapters when possible."""
        if not force and not payload.get("restart_required"):
            return payload

        manager = getattr(self.mq, "channel_manager", None)
        apply_channels = getattr(manager, "apply", None)
        if not callable(apply_channels):
            return payload

        updated = dict(payload)
        try:
            runtime = await apply_channels(Config.load(self._get_config_path()).channels, include_fixed=False)
        except Exception as exc:
            logger.warning("Channel runtime reload failed after settings change: {}", exc)
            updated["runtime_reloaded"] = False
            updated["reload_error"] = str(exc)
            return updated

        runtime_ok = bool(runtime.get("ok"))
        updated["restart_required"] = not runtime_ok
        updated["runtime_reloaded"] = runtime_ok
        updated["runtime"] = self._json_safe(runtime)
        return updated

    def _reload_schedule_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted scheduling settings to the running agent when possible."""
        if not force and not payload.get("restart_required"):
            return payload

        updated = dict(payload)
        agent = self._get_agent()
        if agent is None:
            updated["runtime_reloaded"] = False
            return updated

        try:
            config = Config.load(self._get_config_path())
        except Exception as exc:
            logger.warning("Schedule runtime reload failed after settings change: {}", exc)
            updated["runtime_reloaded"] = False
            updated["reload_error"] = str(exc)
            return updated

        agent.tools_config = config.tools
        cron_tool = getattr(getattr(agent, "tools", None), "get", lambda _name: None)("cron")
        set_default_timezone = getattr(cron_tool, "set_default_timezone", None)
        tool_updated = False
        if callable(set_default_timezone):
            set_default_timezone(config.tools.cron.default_timezone)
            tool_updated = True

        updated["restart_required"] = False
        updated["runtime_reloaded"] = True
        updated["runtime"] = {
            "default_timezone": config.tools.cron.default_timezone,
            "tool_updated": tool_updated,
        }
        return updated

    def _reload_media_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted media settings to the running agent when possible."""
        if not force and not payload.get("restart_required"):
            return payload

        updated = dict(payload)
        agent = self._get_agent()
        reload_media = getattr(agent, "reload_media_from_config", None) if agent is not None else None
        if not callable(reload_media):
            updated["runtime_reloaded"] = False
            return updated

        try:
            runtime = reload_media(Config.load(self._get_config_path()))
        except Exception as exc:
            logger.warning("Media runtime reload failed after settings change: {}", exc)
            updated["runtime_reloaded"] = False
            updated["reload_error"] = str(exc)
            return updated

        updated["restart_required"] = False
        updated["runtime_reloaded"] = True
        updated["runtime"] = self._json_safe(runtime)
        return updated

    async def _reload_mcp_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted MCP settings to the running agent when possible."""
        if not force and not payload.get("restart_required"):
            return self._with_mcp_runtime(payload)

        updated = dict(payload)
        agent = self._get_agent()
        reload_mcp = getattr(agent, "reload_mcp_from_config", None) if agent is not None else None
        if not callable(reload_mcp):
            updated["runtime_reloaded"] = False
            return self._with_mcp_runtime(updated)

        try:
            reload_message = await reload_mcp()
        except Exception as exc:
            logger.warning("MCP runtime reload failed after settings change: {}", exc)
            updated["runtime_reloaded"] = False
            updated["reload_error"] = str(exc)
            return self._with_mcp_runtime(updated)

        updated["restart_required"] = False
        updated["runtime_reloaded"] = True
        updated["reload_message"] = reload_message
        return self._with_mcp_runtime(updated)

    @staticmethod
    async def _read_json_body(request: web.Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(text="Request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Request body must be a JSON object")
        return payload

    @staticmethod
    def _raise_provider_settings_error(exc: ProviderSettingsError) -> None:
        if isinstance(exc, ProviderSettingsValidationError):
            raise web.HTTPBadRequest(text=str(exc)) from exc
        if isinstance(exc, ProviderSettingsNotFound):
            raise web.HTTPNotFound(text=str(exc)) from exc
        if isinstance(exc, ProviderSettingsConflict):
            raise web.HTTPConflict(text=str(exc)) from exc
        raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    @staticmethod
    def _raise_channel_settings_error(exc: ChannelSettingsError) -> None:
        if isinstance(exc, ChannelSettingsValidationError):
            raise web.HTTPBadRequest(text=str(exc)) from exc
        if isinstance(exc, ChannelSettingsNotFound):
            raise web.HTTPNotFound(text=str(exc)) from exc
        raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    @staticmethod
    def _raise_schedule_settings_error(exc: ScheduleSettingsError) -> None:
        if isinstance(exc, ScheduleSettingsValidationError):
            raise web.HTTPBadRequest(text=str(exc)) from exc
        if isinstance(exc, ScheduleSettingsNotFound):
            raise web.HTTPNotFound(text=str(exc)) from exc
        raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    @staticmethod
    def _raise_mcp_settings_error(exc: MCPSettingsError) -> None:
        if isinstance(exc, MCPSettingsValidationError):
            raise web.HTTPBadRequest(text=str(exc)) from exc
        if isinstance(exc, MCPSettingsNotFound):
            raise web.HTTPNotFound(text=str(exc)) from exc
        raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    def _mcp_runtime_payload(self) -> dict[str, Any]:
        agent = self._get_agent()
        lifecycle = getattr(agent, "mcp_lifecycle", None) if agent is not None else None
        if lifecycle is None:
            return {
                "connected": False,
                "connecting": False,
                "connect_failures": 0,
                "retry_after": 0.0,
                "tool_names": [],
            }
        return {
            "connected": bool(getattr(lifecycle, "connected", False)),
            "connecting": bool(getattr(lifecycle, "connecting", False)),
            "connect_failures": int(getattr(lifecycle, "connect_failures", 0) or 0),
            "retry_after": float(getattr(lifecycle, "retry_after", 0.0) or 0.0),
            "tool_names": sorted(getattr(lifecycle, "tool_names", set()) or []),
        }

    def _with_mcp_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        updated = dict(payload)
        updated["runtime"] = self._mcp_runtime_payload()
        return updated

    def _cron_default_timezone(self) -> str:
        try:
            return Config.load(self._get_config_path()).tools.cron.default_timezone or "UTC"
        except Exception:
            return "UTC"

    def _require_cron_manager(self) -> Any:
        agent = self._get_agent()
        cron_manager = getattr(agent, "cron_manager", None) if agent is not None else None
        if cron_manager is None:
            raise web.HTTPServiceUnavailable(text="Cron manager is not available")
        return cron_manager

    async def _get_cron_service(self, session_id: str):
        return await self._require_cron_manager().get_or_create_service(session_id)

    @staticmethod
    def _require_session_id(value: Any) -> str:
        session_id = str(value or "").strip()
        if not session_id:
            raise web.HTTPBadRequest(text="session_id is required")
        return session_id

    @staticmethod
    def _split_session_for_cron(session_id: str) -> tuple[str, str]:
        if ":" in session_id:
            channel, external_chat_id = session_id.split(":", 1)
            return channel or "default", external_chat_id or "default"
        return "default", session_id or "default"

    def _build_cron_schedule_from_payload(self, body: dict[str, Any]) -> tuple[CronSchedule, bool]:
        mode = str(body.get("kind") or body.get("mode") or "").strip().lower()
        default_timezone = self._cron_default_timezone()

        if mode == "every":
            try:
                every_seconds = int(body.get("every_seconds") or 0)
            except (TypeError, ValueError) as exc:
                raise web.HTTPBadRequest(text="every_seconds must be an integer") from exc
            if every_seconds <= 0:
                raise web.HTTPBadRequest(text="every_seconds must be greater than zero")
            return CronSchedule(kind="every", every_ms=every_seconds * 1000), False

        if mode == "cron":
            expr = str(body.get("cron_expr") or body.get("expr") or "").strip()
            if not expr:
                raise web.HTTPBadRequest(text="cron_expr is required")
            tz = str(body.get("tz") or body.get("timezone") or default_timezone).strip() or default_timezone
            return CronSchedule(kind="cron", expr=expr, tz=tz), False

        if mode == "at":
            raw_at = str(body.get("at") or "").strip()
            if not raw_at:
                raise web.HTTPBadRequest(text="at is required")
            try:
                dt = datetime.fromisoformat(raw_at)
            except ValueError as exc:
                raise web.HTTPBadRequest(text="at must use ISO format like 2026-04-10T09:00:00") from exc
            if dt.tzinfo is None:
                from zoneinfo import ZoneInfo

                dt = dt.replace(tzinfo=ZoneInfo(default_timezone))
            return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000)), True

        raise web.HTTPBadRequest(text="kind must be one of every, cron, or at")

    def _serialize_cron_job(self, job: CronJob, *, default_timezone: str, session_id: str | None = None) -> dict[str, Any]:
        next_run_display = None
        if job.state.next_run_at_ms:
            next_run_display = format_cron_timestamp(job.state.next_run_at_ms, job.schedule.tz or default_timezone)
        return {
            "id": job.id,
            "session_id": session_id,
            "name": job.name,
            "enabled": job.enabled,
            "schedule": {
                "kind": job.schedule.kind,
                "at_ms": job.schedule.at_ms,
                "every_ms": job.schedule.every_ms,
                "expr": job.schedule.expr,
                "tz": job.schedule.tz,
                "display": format_cron_timing(job.schedule, default_timezone),
            },
            "payload": {
                "message": job.payload.message,
                "deliver": job.payload.deliver,
                "channel": job.payload.channel,
                "external_chat_id": job.payload.external_chat_id,
            },
            "state": {
                "next_run_at_ms": job.state.next_run_at_ms,
                "next_run_display": next_run_display,
                "last_run_at_ms": job.state.last_run_at_ms,
                "last_status": job.state.last_status,
                "last_error": job.state.last_error,
            },
            "created_at_ms": job.created_at_ms,
            "updated_at_ms": job.updated_at_ms,
            "delete_after_run": job.delete_after_run,
        }

    def _serialize_run(self, run: Any) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "status": run.status,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "finished_at": run.finished_at,
            "metadata": self._json_safe(dict(run.metadata or {})),
        }

    def _serialize_message(self, message: Any) -> dict[str, Any]:
        metadata = getattr(message, "metadata", {})
        return {
            "role": str(getattr(message, "role", "assistant") or "assistant"),
            "content": str(getattr(message, "content", "") or ""),
            "tool_name": getattr(message, "tool_name", None),
            "metadata": self._json_safe(dict(metadata or {})),
            "created_at": float(getattr(message, "timestamp", 0) or 0),
        }

    def _serialize_work_state(self, state: Any) -> dict[str, Any] | None:
        if state is None:
            return None
        return {
            "session_id": state.session_id,
            "objective": state.objective,
            "kind": state.kind,
            "status": state.status,
            "steps": list(state.steps or ()),
            "constraints": list(state.constraints or ()),
            "done_criteria": list(state.done_criteria or ()),
            "long_running": bool(state.long_running),
            "coding_task": bool(state.coding_task),
            "expects_code_change": bool(state.expects_code_change),
            "expects_verification": bool(state.expects_verification),
            "current_step": state.current_step,
            "next_step": state.next_step,
            "completed_steps": list(state.completed_steps or ()),
            "pending_steps": list(state.pending_steps or ()),
            "blockers": list(state.blockers or ()),
            "verification_targets": list(state.verification_targets or ()),
            "resume_hint": state.resume_hint,
            "last_progress_signals": list(state.last_progress_signals or ()),
            "file_change_count": int(state.file_change_count or 0),
            "touched_paths": list(state.touched_paths or ()),
            "verification_attempted": bool(state.verification_attempted),
            "verification_passed": bool(state.verification_passed),
            "last_next_action": state.last_next_action,
            "delegated_tasks": [
                {
                    "task_id": task.task_id,
                    "prompt_type": task.prompt_type,
                    "status": task.status,
                    "selected": bool(task.selected),
                    "summary": task.summary,
                    "error": task.error,
                    "child_session_id": task.child_session_id,
                    "last_child_run_id": task.last_child_run_id,
                    "metadata": self._json_safe(dict(task.metadata or {})),
                    "created_at": float(task.created_at or 0),
                    "updated_at": float(task.updated_at or 0),
                }
                for task in list(state.delegated_tasks or ())
            ],
            "active_delegate_task_id": state.active_delegate_task_id,
            "active_delegate_prompt_type": state.active_delegate_prompt_type,
            **(
                {"follow_up_workflow": str(state.metadata.get("follow_up_workflow") or "").strip()}
                if str(state.metadata.get("follow_up_workflow") or "").strip()
                else {}
            ),
            **(
                {"follow_up_step_id": str(state.metadata.get("follow_up_step_id") or "").strip()}
                if str(state.metadata.get("follow_up_step_id") or "").strip()
                else {}
            ),
            **(
                {"follow_up_step_label": str(state.metadata.get("follow_up_step_label") or "").strip()}
                if str(state.metadata.get("follow_up_step_label") or "").strip()
                else {}
            ),
            **(
                {"follow_up_prompt_type": str(state.metadata.get("follow_up_prompt_type") or "").strip()}
                if str(state.metadata.get("follow_up_prompt_type") or "").strip()
                else {}
            ),
            **(
                {"verification_action": str(state.metadata.get("verification_action") or "").strip()}
                if str(state.metadata.get("verification_action") or "").strip()
                else {}
            ),
            **(
                {"verification_path": str(state.metadata.get("verification_path") or "").strip()}
                if str(state.metadata.get("verification_path") or "").strip()
                else {}
            ),
            **(
                {"verification_pytest_args": self._json_safe(list(state.metadata.get("verification_pytest_args") or []))}
                if isinstance(state.metadata.get("verification_pytest_args"), list) and state.metadata.get("verification_pytest_args")
                else {}
            ),
            **(
                {"active_task_detail": str(state.metadata.get("active_task_detail") or "").strip()}
                if str(state.metadata.get("active_task_detail") or "").strip()
                else {}
            ),
            "metadata": self._json_safe(dict(state.metadata or {})),
            "todos": serialize_work_state_todos(state),
            "created_at": float(state.created_at or 0),
            "updated_at": float(state.updated_at or 0),
        }

    @staticmethod
    def _session_title(messages: list[Any], fallback: str) -> str:
        for message in messages:
            role = str(getattr(message, "role", "") or "")
            content = " ".join(str(getattr(message, "content", "") or "").split())
            if role == "user" and content:
                return f"{content[:30]}..." if len(content) > 30 else content
        return fallback

    @staticmethod
    def _session_updated_at(messages: list[Any], runs: list[Any]) -> float:
        timestamps = [float(getattr(message, "timestamp", 0) or 0) for message in messages]
        timestamps.extend(float(getattr(run, "updated_at", 0) or 0) for run in runs)
        return max(timestamps, default=0.0)

    async def _serialize_session_summary(self, storage: Any, session_id: str, *, message_limit: int) -> dict[str, Any]:
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
        external_chat_id = self._external_chat_id_from_session(session_id)
        fallback_title = external_chat_id or session_id
        return {
            "session_id": session_id,
            "channel": self._channel_from_session(session_id),
            "external_chat_id": external_chat_id,
            "title": self._session_title(display_messages, fallback_title),
            "updated_at": self._session_updated_at(messages, latest_runs),
            "status": self._serialize_session_status(session_id),
            "message_count": await storage.get_message_count(session_id),
            "messages": [self._serialize_message(message) for message in display_messages],
            "runs": [self._serialize_run(run) for run in latest_runs],
            "entries": serialize_session_entries(display_messages, latest_traces),
            "diff_summary": serialize_diff_summary(latest_traces[0]) if latest_traces else None,
            "work_state": self._serialize_work_state(work_state),
        }

    def _serialize_session_status(self, session_id: str) -> dict[str, Any]:
        service = self._get_session_status_service()
        if service is None:
            return {"session_id": session_id, "status": "idle", "metadata": {}}
        item = service.get(session_id)
        return {
            "session_id": item.session_id,
            "status": item.status,
            "updated_at": item.updated_at,
            "metadata": self._json_safe(dict(item.metadata or {})),
        }

    def _serialize_permission_request(self, request: Any) -> dict[str, Any]:
        classification = classify_permission_request(getattr(request, "tool_name", ""), getattr(request, "params", {}))
        return {
            "request_id": request.request_id,
            "tool_name": request.tool_name,
            "params": self._json_safe(request.params),
            "reason": request.reason,
            "status": request.status,
            "action_type": getattr(request, "action_type", None) or classification["action_type"],
            "risk_level": getattr(request, "risk_level", None) or classification["risk_level"],
            "risk_levels": list(getattr(request, "risk_levels", None) or classification["risk_levels"]),
            "resource": getattr(request, "resource", None) or classification["resource"],
            "preview": getattr(request, "preview", None) or classification["preview"],
            "recommended_decision": getattr(request, "recommended_decision", None) or classification["recommended_decision"],
            "session_id": request.session_id,
            "run_id": request.run_id,
            "channel": request.channel,
            "external_chat_id": request.external_chat_id,
            "created_at": request.created_at,
            "expires_at": request.expires_at,
            "resolved_at": request.resolved_at,
            "resolution_reason": request.resolution_reason,
            "timed_out": request.timed_out,
        }

    def _require_storage(self) -> Any:
        storage = self._get_storage()
        if storage is None:
            raise web.HTTPServiceUnavailable(text="Run trace storage is not available")
        return storage

    @staticmethod
    def _coerce_limit(value: str | None, *, default: int = 20, maximum: int = 100) -> int:
        if value is None or not value.strip():
            return default
        try:
            limit = int(value)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="limit must be an integer") from exc
        if limit < 1:
            raise web.HTTPBadRequest(text="limit must be greater than zero")
        return min(limit, maximum)

    @staticmethod
    def _external_chat_id_from_session(session_id: str) -> str | None:
        parts = str(session_id or "").split(":", 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
        compact = str(session_id or "").strip()
        return compact or None

    @staticmethod
    def _channel_from_session(session_id: str) -> str:
        parts = str(session_id or "").split(":", 1)
        return parts[0].strip() if len(parts) == 2 and parts[0].strip() else "unknown"

    @staticmethod
    def _coerce_media_list(value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        items = [str(item) for item in value if isinstance(item, str) and item.strip()]
        return items or None

    @staticmethod
    def _coerce_optional_text(value: Any, *, default: str | None = None) -> str | None:
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    def _parse_incoming_payload(self, raw_text: str) -> dict[str, Any]:
        stripped = raw_text.strip()
        if not stripped:
            raise ValueError("Message text cannot be empty")

        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON payload: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON payload must be an object")
            return payload

        return {"text": raw_text}

    async def to_user_message(self, raw_message: Any) -> UserMessage:
        payload = dict(raw_message) if isinstance(raw_message, dict) else {}
        external_chat_id = self._coerce_optional_text(payload.get("external_chat_id"))
        session_id = self._coerce_optional_text(payload.get("session_id"))
        if session_id is None:
            session_id = self._build_session_id(external_chat_id)

        return UserMessage(
            text=self._coerce_optional_text(payload.get("text"), default="") or "",
            channel=self.channel_instance_id,
            external_chat_id=external_chat_id,
            session_id=session_id,
            sender_id=self._coerce_optional_text(payload.get("sender_id"), default="web-user"),
            sender_name=self._coerce_optional_text(payload.get("sender_name")),
            images=self._coerce_media_list(payload.get("images")),
            audios=self._coerce_media_list(payload.get("audios")),
            videos=self._coerce_media_list(payload.get("videos")),
            metadata={
                "channel_type": self.channel_type,
                "channel_instance_id": self.channel_instance_id,
                **self._coerce_metadata(payload.get("metadata")),
            },
            raw=payload,
        )

    async def send(self, message: AssistantMessage) -> None:
        session_id = message.session_id or self._build_session_id(message.external_chat_id)
        ws = self._session_connections.get(session_id)
        if ws is None or ws.closed:
            logger.warning("Web reply dropped because no active socket is bound to session {}", session_id)
            return

        await ws.send_json(
            {
                "type": "message",
                "channel": self.channel_instance_id,
                "channel_type": self.channel_type,
                "external_chat_id": message.external_chat_id,
                "session_id": session_id,
                "text": message.text,
                "metadata": dict(message.metadata or {}),
            }
        )

    async def send_run_event(self, event: RunEvent) -> None:
        """Send one structured run event to the browser session socket."""
        ws = self._session_connections.get(event.session_id)
        if ws is None or ws.closed:
            return

        await ws.send_json(
            serialize_run_event(
                event,
                include_event_id=False,
                extra={
                    "type": "run_event",
                    "channel": self.channel_instance_id,
                    "channel_type": self.channel_type,
                    "external_chat_id": event.external_chat_id,
                },
            )
        )

    async def send_session_status(self, event: SessionStatusEvent) -> None:
        """Send one session status update to interested browser sockets."""
        payload = {
            "type": "session_status",
            "channel": self._channel_from_session(event.session_id),
            "session_id": event.session_id,
            "status": event.status,
            "updated_at": event.updated_at,
            "metadata": self._json_safe(dict(event.metadata or {})),
        }
        sent: set[web.WebSocketResponse] = set()
        session_ws = self._session_connections.get(event.session_id)
        if session_ws is not None and not session_ws.closed:
            await session_ws.send_json(payload)
            sent.add(session_ws)

        # The Web UI can inspect external-channel sessions through history, so
        # broadcast non-web status changes to connected browser inspectors too.
        for ws in list(self._socket_sessions.keys()):
            if ws in sent or ws.closed:
                continue
            await ws.send_json(payload)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "channel": self.channel_instance_id, "channel_type": self.channel_type})

    async def _handle_settings_providers(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_provider_settings().list_providers()
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        return web.json_response(payload)

    async def _handle_settings_channels(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_channel_settings().list_channels()
        except ChannelSettingsError as exc:
            self._raise_channel_settings_error(exc)
        payload = await self._reload_channels_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_channel_create(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        channel_type = self._coerce_optional_text(body.get("type"))
        if channel_type is None:
            raise web.HTTPBadRequest(text="type is required")
        try:
            payload = self._get_channel_settings().create_channel(
                channel_type,
                name=self._coerce_optional_text(body.get("name")),
                token=self._coerce_optional_text(body.get("token")),
            )
        except ChannelSettingsError as exc:
            self._raise_channel_settings_error(exc)
        payload = await self._reload_channels_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_channel_update(self, request: web.Request) -> web.Response:
        channel_id = self._coerce_optional_text(request.match_info.get("channel_id"))
        if channel_id is None:
            raise web.HTTPBadRequest(text="channel_id is required")
        body = await self._read_json_body(request)
        try:
            payload = self._get_channel_settings().update_channel(
                channel_id,
                enabled=body.get("enabled") if "enabled" in body else None,
                settings=body.get("settings", {}),
            )
        except ChannelSettingsError as exc:
            self._raise_channel_settings_error(exc)
        payload = await self._reload_channels_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_channel_connect(self, request: web.Request) -> web.Response:
        channel_id = self._coerce_optional_text(request.match_info.get("channel_id"))
        if channel_id is None:
            raise web.HTTPBadRequest(text="channel_id is required")
        body = await self._read_json_body(request)
        try:
            payload = self._get_channel_settings().connect_channel(
                channel_id,
                token=self._coerce_optional_text(body.get("token")),
                name=self._coerce_optional_text(body.get("name")),
            )
        except ChannelSettingsError as exc:
            self._raise_channel_settings_error(exc)
        payload = await self._reload_channels_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_channel_disconnect(self, request: web.Request) -> web.Response:
        channel_id = self._coerce_optional_text(request.match_info.get("channel_id"))
        if channel_id is None:
            raise web.HTTPBadRequest(text="channel_id is required")
        try:
            payload = self._get_channel_settings().disconnect_channel(channel_id)
        except ChannelSettingsError as exc:
            self._raise_channel_settings_error(exc)
        payload = await self._reload_channels_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_provider_connect(self, request: web.Request) -> web.Response:
        provider_id = self._coerce_optional_text(request.match_info.get("provider_id"))
        if provider_id is None:
            raise web.HTTPBadRequest(text="provider_id is required")
        body = await self._read_json_body(request)
        try:
            payload = self._get_provider_settings().connect_provider(
                provider_id,
                api_key=self._coerce_optional_text(body.get("api_key")),
                base_url=self._coerce_optional_text(body.get("base_url")),
                name=self._coerce_optional_text(body.get("name")),
            )
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        return web.json_response(payload)

    async def _handle_settings_provider_disconnect(self, request: web.Request) -> web.Response:
        provider_id = self._coerce_optional_text(request.match_info.get("provider_id"))
        if provider_id is None:
            raise web.HTTPBadRequest(text="provider_id is required")
        try:
            payload = self._get_provider_settings().disconnect_provider(provider_id)
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        payload = self._reload_agent_llm_from_config(payload, force=True)
        return web.json_response(payload)

    async def _handle_settings_provider_options_update(self, request: web.Request) -> web.Response:
        provider_id = self._coerce_optional_text(request.match_info.get("provider_id"))
        if provider_id is None:
            raise web.HTTPBadRequest(text="provider_id is required")
        body = await self._read_json_body(request)
        try:
            payload = self._get_provider_settings().update_provider_options(provider_id, body)
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        payload = self._reload_agent_llm_from_config(payload, force=True)
        return web.json_response(payload)

    async def _handle_settings_models(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_provider_settings().list_models()
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        return web.json_response(payload)

    async def _handle_settings_media(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_media_settings().list_media()
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        return web.json_response(payload)

    async def _handle_settings_media_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        category = self._coerce_optional_text(body.get("category"))
        if category is None:
            raise web.HTTPBadRequest(text="category is required")
        try:
            payload = self._get_media_settings().update_media(
                category,
                enabled=bool(body.get("enabled")),
                provider_id=self._coerce_optional_text(body.get("provider_id")),
                model=self._coerce_optional_text(body.get("model")),
            )
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        payload = self._reload_media_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_model_select(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        provider_id = self._coerce_optional_text(body.get("provider_id"))
        model = self._coerce_optional_text(body.get("model"))
        if provider_id is None or model is None:
            raise web.HTTPBadRequest(text="provider_id and model are required")
        try:
            payload = self._get_provider_settings().select_model(provider_id, model)
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        payload = self._reload_agent_llm_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_schedule(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_schedule_settings().get_schedule()
        except ScheduleSettingsError as exc:
            self._raise_schedule_settings_error(exc)
        return web.json_response(payload)

    async def _handle_settings_schedule_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        try:
            payload = self._get_schedule_settings().update_schedule(
                default_timezone=self._coerce_optional_text(body.get("default_timezone")),
            )
        except ScheduleSettingsError as exc:
            self._raise_schedule_settings_error(exc)
        payload = self._reload_schedule_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_mcp(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_mcp_settings().list_servers()
        except MCPSettingsError as exc:
            self._raise_mcp_settings_error(exc)
        return web.json_response(self._with_mcp_runtime(payload))

    async def _handle_settings_mcp_create(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        server_id = self._coerce_optional_text(body.get("server_id"), default="") or ""
        try:
            payload = self._get_mcp_settings().upsert_server(server_id, body)
        except MCPSettingsError as exc:
            self._raise_mcp_settings_error(exc)
        payload = await self._reload_mcp_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_mcp_update(self, request: web.Request) -> web.Response:
        server_id = self._coerce_optional_text(request.match_info.get("server_id"), default="") or ""
        body = await self._read_json_body(request)
        try:
            payload = self._get_mcp_settings().upsert_server(server_id, body)
        except MCPSettingsError as exc:
            self._raise_mcp_settings_error(exc)
        payload = await self._reload_mcp_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_mcp_delete(self, request: web.Request) -> web.Response:
        server_id = self._coerce_optional_text(request.match_info.get("server_id"), default="") or ""
        try:
            payload = self._get_mcp_settings().remove_server(server_id)
        except MCPSettingsError as exc:
            self._raise_mcp_settings_error(exc)
        payload = await self._reload_mcp_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_mcp_reload(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_mcp_settings().list_servers()
        except MCPSettingsError as exc:
            self._raise_mcp_settings_error(exc)
        payload = await self._reload_mcp_from_config({**payload, "restart_required": True}, force=True)
        return web.json_response(payload)

    async def _handle_cron_jobs(self, request: web.Request) -> web.Response:
        default_timezone = self._cron_default_timezone()
        session_id = self._coerce_optional_text(request.query.get("session_id"))
        if session_id:
            service = await self._get_cron_service(session_id)
            jobs = [
                self._serialize_cron_job(job, default_timezone=default_timezone, session_id=session_id)
                for job in service.list_jobs(include_disabled=True)
            ]
        else:
            services = await self._require_cron_manager().get_all_services()
            jobs = [
                self._serialize_cron_job(job, default_timezone=default_timezone, session_id=job_session_id)
                for job_session_id, service in sorted(services.items())
                for job in service.list_jobs(include_disabled=True)
            ]
        return web.json_response(
            {
                "session_id": session_id,
                "default_timezone": default_timezone,
                "jobs": jobs,
            }
        )

    async def _handle_cron_job_create(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        session_id = self._require_session_id(body.get("session_id"))
        message = self._coerce_optional_text(body.get("message"), default="") or ""
        if not message:
            raise web.HTTPBadRequest(text="message is required")

        schedule, delete_after_run = self._build_cron_schedule_from_payload(body)
        channel, external_chat_id = self._split_session_for_cron(session_id)
        service = await self._get_cron_service(session_id)
        try:
            job = service.add_job(
                name=self._coerce_optional_text(body.get("name"), default=message[:30]) or message[:30],
                schedule=schedule,
                message=message,
                deliver=bool(body.get("deliver", True)),
                channel=channel,
                external_chat_id=external_chat_id,
                delete_after_run=delete_after_run,
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc

        return web.json_response(
            {
                "ok": True,
                "session_id": session_id,
                "job": self._serialize_cron_job(job, default_timezone=self._cron_default_timezone(), session_id=session_id),
            }
        )

    async def _handle_cron_job_update(self, request: web.Request) -> web.Response:
        job_id = self._coerce_optional_text(request.match_info.get("job_id"), default="") or ""
        body = await self._read_json_body(request)
        session_id = self._require_session_id(body.get("session_id"))
        message = self._coerce_optional_text(body.get("message"), default="") or ""
        if not message:
            raise web.HTTPBadRequest(text="message is required")

        schedule, delete_after_run = self._build_cron_schedule_from_payload(body)
        channel, external_chat_id = self._split_session_for_cron(session_id)
        service = await self._get_cron_service(session_id)
        try:
            job = service.update_job(
                job_id,
                name=self._coerce_optional_text(body.get("name"), default=message[:30]) or message[:30],
                schedule=schedule,
                message=message,
                deliver=bool(body.get("deliver", True)),
                channel=channel,
                external_chat_id=external_chat_id,
                delete_after_run=delete_after_run,
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        if job is None:
            raise web.HTTPNotFound(text="Cron job not found")

        return web.json_response(
            {
                "ok": True,
                "session_id": session_id,
                "job": self._serialize_cron_job(job, default_timezone=self._cron_default_timezone(), session_id=session_id),
            }
        )

    async def _handle_cron_job_delete(self, request: web.Request) -> web.Response:
        job_id = self._coerce_optional_text(request.match_info.get("job_id"), default="") or ""
        session_id = self._require_session_id(request.query.get("session_id"))
        service = await self._get_cron_service(session_id)
        if not service.remove_job(job_id):
            raise web.HTTPNotFound(text="Cron job not found")
        return web.json_response({"ok": True, "session_id": session_id, "job_id": job_id})

    async def _handle_cron_job_action(self, request: web.Request) -> web.Response:
        job_id = self._coerce_optional_text(request.match_info.get("job_id"), default="") or ""
        action = self._coerce_optional_text(request.match_info.get("action"), default="") or ""
        body = await self._read_json_body(request)
        session_id = self._require_session_id(body.get("session_id"))
        service = await self._get_cron_service(session_id)

        if action == "pause":
            ok = service.pause_job(job_id)
        elif action == "enable":
            ok = service.enable_job(job_id)
        elif action == "run":
            ok = await service.run_job(job_id)
        else:
            raise web.HTTPBadRequest(text="Unsupported cron job action")

        if not ok:
            raise web.HTTPNotFound(text="Cron job not found")
        job = service.get_job(job_id)
        return web.json_response(
            {
                "ok": True,
                "session_id": session_id,
                "job_id": job_id,
                "job": self._serialize_cron_job(job, default_timezone=self._cron_default_timezone(), session_id=session_id) if job else None,
            }
        )

    async def _handle_frontend_index(self, request: web.Request) -> web.FileResponse:
        if self._frontend_dir is None:
            npm_build = "npm.cmd run build" if os.name == "nt" else "npm run build"
            raise web.HTTPServiceUnavailable(
                text=(
                    "OpenSprite web frontend is not built yet. "
                    "Install Node.js 20.19+ or 22.12+ and npm if needed, "
                    f"then restart the gateway or run `{npm_build}` in apps/web."
                )
            )
        return web.FileResponse(self._resolve_frontend_asset("index.html"))

    async def _handle_frontend_asset(self, request: web.Request) -> web.FileResponse:
        asset_path = request.match_info.get("asset_path", "")
        return web.FileResponse(self._resolve_frontend_asset(asset_path))

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        if self.mq is None:
            raise RuntimeError("WebAdapter requires a MessageQueue instance")

        ws = web.WebSocketResponse(max_msg_size=self._get_max_message_size())
        await ws.prepare(request)

        default_external_chat_id = (request.query.get("external_chat_id") or uuid4().hex).strip() or uuid4().hex
        default_session_id = self._build_session_id(default_external_chat_id)
        self._bind_session(default_session_id, ws)

        await ws.send_json(
            {
                "type": "session",
                "channel": self.channel_instance_id,
                "channel_type": self.channel_type,
                "external_chat_id": default_external_chat_id,
                "session_id": default_session_id,
            }
        )

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        payload = self._parse_incoming_payload(msg.data)
                        payload_external_chat_id = self._coerce_optional_text(
                            payload.get("external_chat_id"), default=default_external_chat_id
                        )
                        payload["external_chat_id"] = payload_external_chat_id
                        payload.setdefault("session_id", self._build_session_id(payload_external_chat_id))
                        user_message = await self.to_user_message(payload)
                    except ValueError as exc:
                        await ws.send_json({"type": "error", "error": str(exc)})
                        continue

                    self._bind_session(user_message.session_id or default_session_id, ws)
                    await self.mq.enqueue(user_message)
                    continue

                if msg.type == WSMsgType.ERROR:
                    logger.warning("WebSocket connection closed with error: {}", ws.exception())
        finally:
            self._unbind_socket(ws)

        return ws

    async def _on_response(self, response: AssistantMessage, channel: str, external_chat_id: str | None) -> None:
        await self.send(response)

    async def _on_run_event(self, event: RunEvent) -> None:
        await self.send_run_event(event)

    async def _on_session_status(self, event: SessionStatusEvent) -> None:
        await self.send_session_status(event)

    async def _shutdown(self) -> None:
        for ws in list(self._socket_sessions):
            self._unbind_socket(ws)
            if not ws.closed:
                await ws.close()

        if self.mq is not None:
            self.mq.unregister_response_handler(self.channel_instance_id)
            self.mq.unregister_run_event_handler(self.channel_instance_id)
            self.mq.unregister_session_status_handler(self.channel_instance_id)

        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

        self.site = None
        self.app = None

    async def run(self) -> None:
        if self.mq is None:
            raise RuntimeError("WebAdapter requires a MessageQueue instance")

        host = self._get_host()
        port = self._get_port()
        ws_path = self._get_path("path")
        health_path = self._get_path("health_path")

        self.app = web.Application()
        self.app.router.add_get(ws_path, self._handle_websocket)
        self.app.router.add_get(health_path, self._handle_health)
        self.app.router.add_get("/api/commands", self._api.handle_command_catalog)
        self.app.router.add_get("/api/curator/status", self._api.handle_curator_status)
        self.app.router.add_get("/api/curator/history", self._api.handle_curator_history)
        self.app.router.add_post("/api/curator/{action}", self._api.handle_curator_action)
        self.app.router.add_get("/api/sessions/status", self._api.handle_session_status)
        self.app.router.add_get("/api/sessions", self._api.handle_sessions)
        self.app.router.add_get("/api/runs", self._api.handle_runs)
        self.app.router.add_get("/api/runs/{run_id}/summary", self._api.handle_run_summary)
        self.app.router.add_get("/api/runs/{run_id}", self._api.handle_run_trace)
        self.app.router.add_get("/api/runs/{run_id}/events", self._api.handle_run_events)
        self.app.router.add_post("/api/runs/{run_id}/cancel", self._api.handle_run_cancel)
        self.app.router.add_post(
            "/api/runs/{run_id}/file-changes/{change_id}/revert",
            self._api.handle_run_file_change_revert,
        )
        self.app.router.add_get("/api/permissions", self._api.handle_permissions)
        self.app.router.add_post("/api/permissions/{request_id}/approve", self._api.handle_permission_approve)
        self.app.router.add_post("/api/permissions/{request_id}/deny", self._api.handle_permission_deny)
        self.app.router.add_post("/api/worktrees/cleanup", self._api.handle_worktree_cleanup)
        self.app.router.add_get("/api/settings/channels", self._handle_settings_channels)
        self.app.router.add_post("/api/settings/channels", self._handle_settings_channel_create)
        self.app.router.add_put("/api/settings/channels/{channel_id}", self._handle_settings_channel_update)
        self.app.router.add_put("/api/settings/channels/{channel_id}/connect", self._handle_settings_channel_connect)
        self.app.router.add_post("/api/settings/channels/{channel_id}/disconnect", self._handle_settings_channel_disconnect)
        self.app.router.add_get("/api/settings/providers", self._handle_settings_providers)
        self.app.router.add_put("/api/settings/providers/{provider_id}/connect", self._handle_settings_provider_connect)
        self.app.router.add_post("/api/settings/providers/{provider_id}/disconnect", self._handle_settings_provider_disconnect)
        self.app.router.add_put("/api/settings/providers/{provider_id}/options", self._handle_settings_provider_options_update)
        self.app.router.add_get("/api/settings/models", self._handle_settings_models)
        self.app.router.add_post("/api/settings/models/select", self._handle_settings_model_select)
        self.app.router.add_get("/api/settings/media", self._handle_settings_media)
        self.app.router.add_put("/api/settings/media", self._handle_settings_media_update)
        self.app.router.add_get("/api/settings/schedule", self._handle_settings_schedule)
        self.app.router.add_put("/api/settings/schedule", self._handle_settings_schedule_update)
        self.app.router.add_get("/api/settings/mcp", self._handle_settings_mcp)
        self.app.router.add_post("/api/settings/mcp", self._handle_settings_mcp_create)
        self.app.router.add_post("/api/settings/mcp/reload", self._handle_settings_mcp_reload)
        self.app.router.add_put("/api/settings/mcp/{server_id}", self._handle_settings_mcp_update)
        self.app.router.add_delete("/api/settings/mcp/{server_id}", self._handle_settings_mcp_delete)
        self.app.router.add_get("/api/cron/jobs", self._handle_cron_jobs)
        self.app.router.add_post("/api/cron/jobs", self._handle_cron_job_create)
        self.app.router.add_put("/api/cron/jobs/{job_id}", self._handle_cron_job_update)
        self.app.router.add_delete("/api/cron/jobs/{job_id}", self._handle_cron_job_delete)
        self.app.router.add_post("/api/cron/jobs/{job_id}/{action}", self._handle_cron_job_action)
        self.app.router.add_get("/", self._handle_frontend_index)
        self.app.router.add_get("/index.html", self._handle_frontend_index)
        if self._frontend_dir is not None:
            self.app.router.add_get(r"/{asset_path:.+\..+}", self._handle_frontend_asset)
        else:
            logger.info("Web adapter did not find a frontend directory; serving API endpoints only")

        self.mq.register_response_handler(self.channel_instance_id, self._on_response)
        self.mq.register_run_event_handler(self.channel_instance_id, self._on_run_event)
        self.mq.register_session_status_handler(self.channel_instance_id, self._on_session_status)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host=host, port=port)
        await self.site.start()
        self._started_event.set()

        logger.info(
            "Web adapter listening on ws://{}:{}{} (health=http://{}:{}{}, frontend={})",
            host,
            self.bound_port,
            ws_path,
            host,
            self.bound_port,
            health_path,
            self._frontend_dir if self._frontend_dir is not None else "disabled",
        )

        try:
            await self._shutdown_event.wait()
        finally:
            await self._shutdown()
