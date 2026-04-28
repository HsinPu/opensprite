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
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import WSMsgType, web

from .identity import build_session_id, normalize_identifier
from ..bus.events import RunEvent
from ..bus.message import AssistantMessage, MessageAdapter, UserMessage
from ..config import Config, MessagesConfig
from ..config.channel_settings import (
    ChannelSettingsError,
    ChannelSettingsNotFound,
    ChannelSettingsService,
    ChannelSettingsValidationError,
)
from ..config.provider_settings import (
    ProviderSettingsConflict,
    ProviderSettingsError,
    ProviderSettingsNotFound,
    ProviderSettingsService,
    ProviderSettingsValidationError,
)
from ..utils.log import logger


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

    def _get_config_path(self) -> Path:
        agent = self._get_agent()
        raw_path = getattr(agent, "config_path", None) if agent is not None else None
        if raw_path is not None:
            return Path(raw_path).expanduser().resolve()
        return (Path.home() / ".opensprite" / "opensprite.json").resolve()

    def _get_provider_settings(self) -> ProviderSettingsService:
        return ProviderSettingsService(self._get_config_path())

    def _get_channel_settings(self) -> ChannelSettingsService:
        return ChannelSettingsService(self._get_config_path())

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

    def _serialize_run_event(self, event: Any) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "session_id": event.session_id,
            "event_type": event.event_type,
            "payload": self._json_safe(dict(event.payload or {})),
            "created_at": event.created_at,
        }

    def _serialize_run_part(self, part: Any) -> dict[str, Any]:
        return {
            "part_id": part.part_id,
            "run_id": part.run_id,
            "session_id": part.session_id,
            "part_type": part.part_type,
            "content": part.content,
            "tool_name": part.tool_name,
            "metadata": self._json_safe(dict(part.metadata or {})),
            "created_at": part.created_at,
        }

    def _serialize_file_change(self, change: Any) -> dict[str, Any]:
        return {
            "change_id": change.change_id,
            "run_id": change.run_id,
            "session_id": change.session_id,
            "tool_name": change.tool_name,
            "path": change.path,
            "action": change.action,
            "before_sha256": change.before_sha256,
            "after_sha256": change.after_sha256,
            "before_content": change.before_content,
            "after_content": change.after_content,
            "diff": change.diff,
            "metadata": self._json_safe(dict(change.metadata or {})),
            "created_at": change.created_at,
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
            {
                "type": "run_event",
                "channel": self.channel_instance_id,
                "channel_type": self.channel_type,
                "external_chat_id": event.external_chat_id,
                "session_id": event.session_id,
                "run_id": event.run_id,
                "event_type": event.event_type,
                "payload": dict(event.payload or {}),
                "created_at": event.created_at,
            }
        )

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "channel": self.channel_instance_id, "channel_type": self.channel_type})

    async def _handle_run_events(self, request: web.Request) -> web.Response:
        storage = self._require_storage()

        run_id = self._coerce_optional_text(request.match_info.get("run_id"))
        session_id = self._coerce_optional_text(request.query.get("session_id"))
        if run_id is None or session_id is None:
            raise web.HTTPBadRequest(text="Both run_id and session_id are required")

        run = await storage.get_run(session_id, run_id)
        if run is None:
            raise web.HTTPNotFound(text="Run not found")

        events = await storage.get_run_events(session_id, run_id)
        return web.json_response(
            {
                "run_id": run_id,
                "session_id": session_id,
                "events": [self._serialize_run_event(event) for event in events],
            }
        )

    async def _handle_runs(self, request: web.Request) -> web.Response:
        storage = self._require_storage()
        session_id = self._coerce_optional_text(request.query.get("session_id"))
        if session_id is None:
            raise web.HTTPBadRequest(text="session_id is required")

        runs = await storage.get_runs(session_id, limit=self._coerce_limit(request.query.get("limit")))
        return web.json_response(
            {
                "session_id": session_id,
                "runs": [self._serialize_run(run) for run in runs],
            }
        )

    async def _handle_run_trace(self, request: web.Request) -> web.Response:
        storage = self._require_storage()
        run_id = self._coerce_optional_text(request.match_info.get("run_id"))
        session_id = self._coerce_optional_text(request.query.get("session_id"))
        if run_id is None or session_id is None:
            raise web.HTTPBadRequest(text="Both run_id and session_id are required")

        trace = await storage.get_run_trace(session_id, run_id)
        if trace is None:
            raise web.HTTPNotFound(text="Run not found")

        return web.json_response(
            {
                "run": self._serialize_run(trace.run),
                "events": [self._serialize_run_event(event) for event in trace.events],
                "parts": [self._serialize_run_part(part) for part in trace.parts],
                "file_changes": [self._serialize_file_change(change) for change in trace.file_changes],
            }
        )

    async def _handle_run_cancel(self, request: web.Request) -> web.Response:
        storage = self._require_storage()
        agent = self._get_agent()
        if agent is None or not hasattr(agent, "request_run_cancel"):
            raise web.HTTPServiceUnavailable(text="Run cancellation is not available")

        run_id = self._coerce_optional_text(request.match_info.get("run_id"))
        session_id = self._coerce_optional_text(request.query.get("session_id"))
        if run_id is None or session_id is None:
            raise web.HTTPBadRequest(text="Both run_id and session_id are required")

        run = await storage.get_run(session_id, run_id)
        if run is None:
            raise web.HTTPNotFound(text="Run not found")

        accepted = await agent.request_run_cancel(
            session_id,
            run_id,
            channel="web",
            external_chat_id=self._external_chat_id_from_session(session_id),
        )
        if not accepted:
            raise web.HTTPConflict(text="Run is not active")

        cancel_chat = getattr(self.mq, "cancel_chat", None)
        if callable(cancel_chat):
            await cancel_chat(session_id)

        return web.json_response({"ok": True, "session_id": session_id, "run_id": run_id, "status": "cancelling"})

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
        return web.json_response(payload)

    async def _handle_settings_channel_disconnect(self, request: web.Request) -> web.Response:
        channel_id = self._coerce_optional_text(request.match_info.get("channel_id"))
        if channel_id is None:
            raise web.HTTPBadRequest(text="channel_id is required")
        try:
            payload = self._get_channel_settings().disconnect_channel(channel_id)
        except ChannelSettingsError as exc:
            self._raise_channel_settings_error(exc)
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

    async def _handle_settings_models(self, request: web.Request) -> web.Response:
        try:
            payload = self._get_provider_settings().list_models()
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
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

    async def _handle_frontend_index(self, request: web.Request) -> web.FileResponse:
        if self._frontend_dir is None:
            raise web.HTTPServiceUnavailable(
                text=(
                    "OpenSprite web frontend is not built yet. "
                    "Install Node.js/npm if needed, then restart the gateway or run `npm.cmd run build` in apps/web."
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

    async def _shutdown(self) -> None:
        for ws in list(self._socket_sessions):
            self._unbind_socket(ws)
            if not ws.closed:
                await ws.close()

        if self.mq is not None:
            self.mq.unregister_response_handler(self.channel_instance_id)
            self.mq.unregister_run_event_handler(self.channel_instance_id)

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
        self.app.router.add_get("/api/runs", self._handle_runs)
        self.app.router.add_get("/api/runs/{run_id}", self._handle_run_trace)
        self.app.router.add_get("/api/runs/{run_id}/events", self._handle_run_events)
        self.app.router.add_post("/api/runs/{run_id}/cancel", self._handle_run_cancel)
        self.app.router.add_get("/api/settings/channels", self._handle_settings_channels)
        self.app.router.add_post("/api/settings/channels", self._handle_settings_channel_create)
        self.app.router.add_put("/api/settings/channels/{channel_id}", self._handle_settings_channel_update)
        self.app.router.add_put("/api/settings/channels/{channel_id}/connect", self._handle_settings_channel_connect)
        self.app.router.add_post("/api/settings/channels/{channel_id}/disconnect", self._handle_settings_channel_disconnect)
        self.app.router.add_get("/api/settings/providers", self._handle_settings_providers)
        self.app.router.add_put("/api/settings/providers/{provider_id}/connect", self._handle_settings_provider_connect)
        self.app.router.add_post("/api/settings/providers/{provider_id}/disconnect", self._handle_settings_provider_disconnect)
        self.app.router.add_get("/api/settings/models", self._handle_settings_models)
        self.app.router.add_post("/api/settings/models/select", self._handle_settings_model_select)
        self.app.router.add_get("/", self._handle_frontend_index)
        self.app.router.add_get("/index.html", self._handle_frontend_index)
        if self._frontend_dir is not None:
            self.app.router.add_get(r"/{asset_path:.+\..+}", self._handle_frontend_asset)
        else:
            logger.info("Web adapter did not find a frontend directory; serving API endpoints only")

        self.mq.register_response_handler(self.channel_instance_id, self._on_response)
        self.mq.register_run_event_handler(self.channel_instance_id, self._on_run_event)
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
