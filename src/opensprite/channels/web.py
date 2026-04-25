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

from ..bus.message import AssistantMessage, MessageAdapter, UserMessage
from ..config import MessagesConfig
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
        "frontend_build_timeout": 120,
    }

    def __init__(self, mq=None, config: dict[str, Any] | None = None):
        self.mq = mq
        self.messages = getattr(mq, "messages", None) or MessagesConfig()
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
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

        logger.info("Building web frontend before gateway startup: {}", source_dir)
        try:
            result = subprocess.run(
                [npm, "run", "build"],
                cwd=source_dir,
                capture_output=True,
                check=False,
                text=True,
                timeout=self._get_frontend_build_timeout(),
            )
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

    def _build_session_chat_id(self, chat_id: str | None) -> str:
        normalized_chat_id = self._coerce_optional_text(chat_id, default="default") or "default"
        if self.mq is not None:
            return self.mq.build_session_chat_id("web", normalized_chat_id)
        return f"web:{normalized_chat_id}"

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

    def _bind_session(self, session_chat_id: str, ws: web.WebSocketResponse) -> None:
        self._session_connections[session_chat_id] = ws
        self._socket_sessions.setdefault(ws, set()).add(session_chat_id)

    def _unbind_socket(self, ws: web.WebSocketResponse) -> None:
        for session_chat_id in self._socket_sessions.pop(ws, set()):
            if self._session_connections.get(session_chat_id) is ws:
                self._session_connections.pop(session_chat_id, None)

    @staticmethod
    def _coerce_metadata(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

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
        chat_id = self._coerce_optional_text(payload.get("chat_id"))
        session_chat_id = self._coerce_optional_text(payload.get("session_chat_id"))
        if session_chat_id is None:
            session_chat_id = self._build_session_chat_id(chat_id)

        return UserMessage(
            text=self._coerce_optional_text(payload.get("text"), default="") or "",
            channel="web",
            chat_id=chat_id,
            session_chat_id=session_chat_id,
            sender_id=self._coerce_optional_text(payload.get("sender_id"), default="web-user"),
            sender_name=self._coerce_optional_text(payload.get("sender_name")),
            images=self._coerce_media_list(payload.get("images")),
            audios=self._coerce_media_list(payload.get("audios")),
            videos=self._coerce_media_list(payload.get("videos")),
            metadata=self._coerce_metadata(payload.get("metadata")),
            raw=payload,
        )

    async def send(self, message: AssistantMessage) -> None:
        session_chat_id = message.session_chat_id or self._build_session_chat_id(message.chat_id)
        ws = self._session_connections.get(session_chat_id)
        if ws is None or ws.closed:
            logger.warning("Web reply dropped because no active socket is bound to session {}", session_chat_id)
            return

        await ws.send_json(
            {
                "type": "message",
                "channel": "web",
                "chat_id": message.chat_id,
                "session_chat_id": session_chat_id,
                "text": message.text,
                "metadata": dict(message.metadata or {}),
            }
        )

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "channel": "web"})

    async def _handle_frontend_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self._resolve_frontend_asset("index.html"))

    async def _handle_frontend_asset(self, request: web.Request) -> web.FileResponse:
        asset_path = request.match_info.get("asset_path", "")
        return web.FileResponse(self._resolve_frontend_asset(asset_path))

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        if self.mq is None:
            raise RuntimeError("WebAdapter requires a MessageQueue instance")

        ws = web.WebSocketResponse(max_msg_size=self._get_max_message_size())
        await ws.prepare(request)

        default_chat_id = (request.query.get("chat_id") or uuid4().hex).strip() or uuid4().hex
        default_session_chat_id = self._build_session_chat_id(default_chat_id)
        self._bind_session(default_session_chat_id, ws)

        await ws.send_json(
            {
                "type": "session",
                "channel": "web",
                "chat_id": default_chat_id,
                "session_chat_id": default_session_chat_id,
            }
        )

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        payload = self._parse_incoming_payload(msg.data)
                        payload_chat_id = self._coerce_optional_text(payload.get("chat_id"), default=default_chat_id)
                        payload["chat_id"] = payload_chat_id
                        payload.setdefault("session_chat_id", self._build_session_chat_id(payload_chat_id))
                        user_message = await self.to_user_message(payload)
                    except ValueError as exc:
                        await ws.send_json({"type": "error", "error": str(exc)})
                        continue

                    self._bind_session(user_message.session_chat_id or default_session_chat_id, ws)
                    await self.mq.enqueue(user_message)
                    continue

                if msg.type == WSMsgType.ERROR:
                    logger.warning("WebSocket connection closed with error: {}", ws.exception())
        finally:
            self._unbind_socket(ws)

        return ws

    async def _on_response(self, response: AssistantMessage, channel: str, chat_id: str | None) -> None:
        await self.send(response)

    async def _shutdown(self) -> None:
        for ws in list(self._socket_sessions):
            self._unbind_socket(ws)
            if not ws.closed:
                await ws.close()

        if self.mq is not None:
            self.mq.unregister_response_handler("web")

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
        if self._frontend_dir is not None:
            self.app.router.add_get("/", self._handle_frontend_index)
            self.app.router.add_get("/index.html", self._handle_frontend_index)
            self.app.router.add_get(r"/{asset_path:.+\..+}", self._handle_frontend_asset)
        else:
            logger.info("Web adapter did not find a frontend directory; serving API endpoints only")

        self.mq.register_response_handler("web", self._on_response)
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
