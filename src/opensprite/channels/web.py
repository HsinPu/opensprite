"""
opensprite/channels/web.py - WebSocket chat adapter

Expose a lightweight WebSocket endpoint that feeds browser messages into
MessageQueue and routes assistant replies back to the same web session.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from aiohttp import WSMsgType, web

from .identity import build_session_id, normalize_identifier
from ..auth.credentials import (
    CredentialNotFoundError,
    CredentialStoreError,
    add_credential,
    list_credentials,
    remove_credential,
    set_capability_default,
    set_provider_default,
)
from ..bus.events import RunEvent, SessionStatusEvent
from ..bus.message import AssistantMessage, MessageAdapter, UserMessage
from ..cli import update as update_cli
from ..cli import service_background, service_linux
from ..config import Config, MessagesConfig
from ..config.channel_settings import (
    ChannelSettingsError,
    ChannelSettingsNotFound,
    ChannelSettingsService,
    ChannelSettingsValidationError,
)
from ..config.llm_presets import provider_profile_defaults, provider_request_options
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
from ..tools.browser_runtime import SUPPORTED_BROWSER_BACKENDS, browser_cloud_status
from ..utils.log import logger, setup_log
from ..utils.url import join_url_path
from .web_api import WebApiHandlers


class WebAdapter(MessageAdapter):
    """WebSocket adapter for browser-based chat clients."""

    LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")
    LLM_DECODING_MODE_ORDER = ("provider_default", "precise", "balanced", "creative", "custom")
    WEB_SEARCH_PROVIDERS = ("duckduckgo", "brave", "tavily", "searxng", "jina")
    WEB_SEARCH_FRESHNESS = ("none", "day", "week", "month", "year")
    SEARXNG_OPTIONS_USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 OpenSprite/0.1"
    SEARXNG_FALLBACK_ENGINES = (
        "duckduckgo",
        "google",
        "bing",
        "brave",
        "qwant",
        "startpage",
        "wikipedia",
        "wikidata",
        "github",
        "stackoverflow",
        "reddit",
        "youtube",
        "arxiv",
        "semantic scholar",
    )
    SEARXNG_FALLBACK_CATEGORIES = ("general", "images", "videos", "news", "map", "music", "it", "science", "files", "social media")
    BROWSER_BACKENDS = SUPPORTED_BROWSER_BACKENDS
    LLM_DECODING_PRESETS = {
        "precise": {
            "temperature": 0.25,
            "max_tokens": 32768,
            "top_p": 0.95,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
        },
        "balanced": {
            "temperature": 0.7,
            "max_tokens": 32768,
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
        },
        "creative": {
            "temperature": 1.0,
            "max_tokens": 32768,
            "top_p": 0.95,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
        },
    }

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
        "auth_token": "",
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

    def _get_auth_token(self) -> str:
        return str(self.config.get("auth_token", "") or "").strip()

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        normalized = str(host or "").strip().strip("[]").lower()
        if normalized in {"localhost"}:
            return True
        if normalized in {"", "*", "0.0.0.0", "::", "::0"}:
            return False
        try:
            return ipaddress.ip_address(normalized).is_loopback
        except ValueError:
            return False

    def _validate_bind_auth_config(self, host: str) -> None:
        if self._is_loopback_host(host):
            return
        if self._get_auth_token():
            return
        raise RuntimeError(
            "WebAdapter refuses to bind to a non-loopback host without auth_token configured. "
            "Use host=127.0.0.1 for local-only access or set a strong web auth_token."
        )

    def _auth_required(self, request: web.Request) -> bool:
        token = self._get_auth_token()
        if not token:
            return False
        path = request.path or ""
        return path == self._get_path("path") or path.startswith("/api/")

    def _request_has_valid_auth(self, request: web.Request) -> bool:
        token = self._get_auth_token()
        if not token:
            return True
        auth_header = request.headers.get("Authorization", "").strip()
        supplied = ""
        if auth_header.lower().startswith("bearer "):
            supplied = auth_header[7:].strip()
        if not supplied:
            supplied = str(request.query.get("access_token") or "").strip()
        return bool(supplied) and hmac.compare_digest(supplied, token)

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if self._auth_required(request) and not self._request_has_valid_auth(request):
            raise web.HTTPUnauthorized(text="Unauthorized")
        return await handler(request)

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
            encoding="utf-8",
            errors="replace",
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

    def _get_app_home(self) -> Path:
        return self._get_config_path().parent

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

    @staticmethod
    def _apply_network_environment(config: Config) -> None:
        network = getattr(config, "network", None)
        if network is None:
            return
        for env_key, value in {
            "HTTP_PROXY": getattr(network, "http_proxy", "") or "",
            "HTTPS_PROXY": getattr(network, "https_proxy", "") or "",
            "NO_PROXY": getattr(network, "no_proxy", "") or "",
        }.items():
            normalized = str(value or "").strip()
            if normalized:
                os.environ[env_key] = normalized
                os.environ[env_key.lower()] = normalized
            else:
                os.environ.pop(env_key, None)
                os.environ.pop(env_key.lower(), None)

    @staticmethod
    def _network_payload(config: Config) -> dict[str, Any]:
        network = getattr(config, "network", None)
        return {
            "http_proxy": str(getattr(network, "http_proxy", "") or ""),
            "https_proxy": str(getattr(network, "https_proxy", "") or ""),
            "no_proxy": str(getattr(network, "no_proxy", "") or ""),
        }

    @staticmethod
    def _browser_runtime_status() -> dict[str, Any]:
        agent_browser = shutil.which("agent-browser")
        if agent_browser:
            return {
                "available": True,
                "command": agent_browser,
                "install_hint": "",
            }

        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if npx:
            return {
                "available": True,
                "command": f"{npx} agent-browser",
                "install_hint": "agent-browser is not on PATH; OpenSprite will fall back to npx agent-browser.",
            }

        return {
            "available": False,
            "command": "",
            "install_hint": "Install agent-browser on PATH, or install Node.js/npm so npx agent-browser can run.",
        }

    @classmethod
    def _browser_payload(cls, config: Config) -> dict[str, Any]:
        browser = getattr(getattr(config, "tools", None), "browser", None)
        return {
            "enabled": bool(getattr(browser, "enabled", False)),
            "backend": str(getattr(browser, "backend", "agent-browser") or "agent-browser"),
            "backends": list(cls.BROWSER_BACKENDS),
            "command_timeout": int(getattr(browser, "command_timeout", 30) or 30),
            "session_timeout": int(getattr(browser, "session_timeout", 300) or 300),
            "cdp_url": str(getattr(browser, "cdp_url", "") or ""),
            "allow_private_urls": bool(getattr(browser, "allow_private_urls", False)),
            "cloud": browser_cloud_status(browser),
            "runtime": cls._browser_runtime_status(),
        }

    @classmethod
    def _web_search_payload(cls, config: Config) -> dict[str, Any]:
        search = getattr(getattr(config, "tools", None), "web_search", None)
        return {
            "provider": str(getattr(search, "provider", "searxng") or "searxng"),
            "providers": list(cls.WEB_SEARCH_PROVIDERS),
            "freshness": str(getattr(search, "freshness", "year") or "year"),
            "freshness_options": list(cls.WEB_SEARCH_FRESHNESS),
            "max_results": int(getattr(search, "max_results", 25) or 25),
            "duckduckgo_max_pages": int(getattr(search, "duckduckgo_max_pages", 10) or 10),
            "searxng_max_pages": int(getattr(search, "searxng_max_pages", 5) or 5),
            "searxng_url": str(getattr(search, "searxng_url", "https://searx.be") or "https://searx.be"),
            "searxng_engines": cls._coerce_text_list(getattr(search, "searxng_engines", []), field="searxng_engines", default=[]),
            "searxng_categories": cls._coerce_text_list(getattr(search, "searxng_categories", []), field="searxng_categories", default=[]),
            "proxy": str(getattr(search, "proxy", "") or ""),
            "brave_api_key_configured": bool(getattr(search, "brave_api_key", "") or os.environ.get("BRAVE_API_KEY", "")),
            "tavily_api_key_configured": bool(getattr(search, "tavily_api_key", "") or os.environ.get("TAVILY_API_KEY", "")),
            "jina_api_key_configured": bool(getattr(search, "jina_api_key", "") or os.environ.get("JINA_API_KEY", "")),
        }

    @staticmethod
    def _llm_decoding_payload(config: Config) -> dict[str, Any]:
        llm = config.llm
        return {
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            "top_p": llm.top_p,
            "frequency_penalty": llm.frequency_penalty,
            "presence_penalty": llm.presence_penalty,
        }

    @classmethod
    def _llm_decoding_mode(cls, config: Config) -> str:
        if not config.llm.pass_decoding_params:
            return "provider_default"
        decoding = cls._llm_decoding_payload(config)
        for mode, preset in cls.LLM_DECODING_PRESETS.items():
            if all(decoding.get(key) == value for key, value in preset.items()):
                return mode
        return "custom"

    @classmethod
    def _apply_llm_decoding_preset(cls, config: Config, mode: str) -> None:
        if mode == "provider_default":
            config.llm.pass_decoding_params = False
            return
        preset = cls.LLM_DECODING_PRESETS.get(mode)
        if preset is None:
            raise web.HTTPBadRequest(text=f"decoding_mode must be one of: {', '.join(cls.LLM_DECODING_MODE_ORDER)}")
        config.llm.pass_decoding_params = True
        for key, value in preset.items():
            setattr(config.llm, key, value)

    @staticmethod
    def _coerce_llm_float(value: Any, *, field: str, minimum: float | None = None, maximum: float | None = None) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=f"{field} must be a number") from exc
        if minimum is not None and number < minimum:
            raise web.HTTPBadRequest(text=f"{field} must be at least {minimum}")
        if maximum is not None and number > maximum:
            raise web.HTTPBadRequest(text=f"{field} must be at most {maximum}")
        return number

    @classmethod
    def _apply_custom_llm_decoding(cls, config: Config, decoding: dict[str, Any]) -> None:
        config.llm.pass_decoding_params = True
        if "temperature" in decoding:
            config.llm.temperature = cls._coerce_llm_float(decoding["temperature"], field="temperature")
        if "max_tokens" in decoding:
            config.llm.max_tokens = cls._coerce_positive_int(decoding["max_tokens"], field="max_tokens", default=config.llm.max_tokens, minimum=1, maximum=1_000_000)
        if "top_p" in decoding:
            config.llm.top_p = cls._coerce_llm_float(decoding["top_p"], field="top_p", minimum=0.0, maximum=1.0)
        if "frequency_penalty" in decoding:
            config.llm.frequency_penalty = cls._coerce_llm_float(decoding["frequency_penalty"], field="frequency_penalty", minimum=-2.0, maximum=2.0)
        if "presence_penalty" in decoding:
            config.llm.presence_penalty = cls._coerce_llm_float(decoding["presence_penalty"], field="presence_penalty", minimum=-2.0, maximum=2.0)

    @staticmethod
    def _anthropic_reasoning_budget(effort: str | None) -> int:
        budgets = {"minimal": 4000, "low": 4000, "medium": 8000, "high": 16000, "xhigh": 32000}
        return budgets.get(str(effort or "medium").lower(), budgets["medium"])

    @classmethod
    def _effective_llm_request_payload(cls, config: Config) -> dict[str, Any]:
        llm = config.llm
        provider_id = str(llm.default or "").strip()
        active = llm.get_active()
        provider_name = str(getattr(active, "provider", None) or provider_id or "").strip()
        defaults = provider_profile_defaults(
            provider_name,
            auth_type=getattr(active, "auth_type", "api_key"),
            api_mode=getattr(active, "api_mode", None),
        )
        provider_name = defaults.provider_id or provider_name
        api_mode = str(defaults.api_mode or "chat_completions").strip()
        decoding = cls._llm_decoding_payload(config)
        sent_decoding = dict(decoding) if llm.pass_decoding_params else {key: None for key in decoding}
        reasoning_source = "none"
        reasoning_payload: dict[str, Any] = {}
        provider_options: dict[str, Any] = {}
        request_options = provider_request_options(provider_name)

        if request_options:
            reasoning: dict[str, Any] = {}
            if "reasoning" in request_options and active.reasoning_enabled:
                if active.reasoning_effort:
                    reasoning["effort"] = active.reasoning_effort
                if active.reasoning_max_tokens is not None:
                    reasoning["max_tokens"] = active.reasoning_max_tokens
            if "reasoning" in request_options and active.reasoning_exclude:
                reasoning["exclude"] = True
            if "reasoning" in request_options:
                reasoning_source = provider_name or "provider_request_options"
            reasoning_payload = reasoning
            if "provider_sort" in request_options and active.provider_sort:
                provider_options["sort"] = active.provider_sort
            if "require_parameters" in request_options and active.require_parameters:
                provider_options["require_parameters"] = True
        elif api_mode == "anthropic_messages":
            reasoning_source = "anthropic_messages"
            if active.reasoning_enabled:
                budget = cls._anthropic_reasoning_budget(active.reasoning_effort)
                base_max_tokens = sent_decoding.get("max_tokens") or 131072
                reasoning_payload = {
                    "thinking": {"type": "enabled", "budget_tokens": budget},
                    "temperature": 1,
                    "max_tokens": max(int(base_max_tokens), budget + 4096),
                }
        elif provider_name == "minimax":
            reasoning_source = "minimax_chat_completions"
            reasoning_payload = {"extra_body": {"reasoning_split": True}}

        return {
            "configured": bool(config.is_llm_configured),
            "provider_id": provider_id,
            "provider": provider_name,
            "api_mode": api_mode,
            "model": str(getattr(active, "model", "") or llm.model or ""),
            "context_window_tokens": active.context_window_tokens,
            "decoding": {
                "status": "sent" if llm.pass_decoding_params else "omitted",
                "params": sent_decoding,
            },
            "reasoning": {
                "source": reasoning_source,
                "sent": bool(reasoning_payload),
                "enabled": bool(getattr(active, "reasoning_enabled", False)),
                "effort": getattr(active, "reasoning_effort", None),
                "max_tokens": getattr(active, "reasoning_max_tokens", None),
                "exclude": bool(getattr(active, "reasoning_exclude", False)),
                "payload": reasoning_payload,
            },
            "provider_options": provider_options,
        }

    @classmethod
    def _llm_payload(cls, config: Config) -> dict[str, Any]:
        return {
            "decoding_mode": cls._llm_decoding_mode(config),
            "decoding_modes": list(cls.LLM_DECODING_MODE_ORDER),
            "pass_decoding_params": bool(config.llm.pass_decoding_params),
            "decoding": cls._llm_decoding_payload(config),
            "effective_request": cls._effective_llm_request_payload(config),
            "semantic_contract_classifier_enabled": bool(config.agent.semantic_contract_classifier_enabled),
            "semantic_contract_classifier_confidence_threshold": float(config.agent.semantic_contract_classifier_confidence_threshold),
        }

    @classmethod
    def _log_payload(cls, config: Config) -> dict[str, Any]:
        log = getattr(config, "log", None)
        return {
            "enabled": bool(getattr(log, "enabled", False)),
            "level": str(getattr(log, "level", "INFO") or "INFO").upper(),
            "retention_days": int(getattr(log, "retention_days", 365) or 365),
            "log_system_prompt": bool(getattr(log, "log_system_prompt", True)),
            "log_system_prompt_lines": int(getattr(log, "log_system_prompt_lines", 0) or 0),
            "log_reasoning_details": bool(getattr(log, "log_reasoning_details", False)),
            "levels": list(cls.LOG_LEVELS),
        }

    @classmethod
    def _coerce_log_level(cls, value: Any) -> str:
        level = str(value or "INFO").strip().upper()
        if level not in cls.LOG_LEVELS:
            raise web.HTTPBadRequest(text=f"level must be one of: {', '.join(cls.LOG_LEVELS)}")
        return level

    @staticmethod
    def _coerce_positive_int(value: Any, *, field: str, default: int, minimum: int = 0, maximum: int = 3650) -> int:
        if value is None or value == "":
            return default
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=f"{field} must be an integer") from exc
        if number < minimum:
            raise web.HTTPBadRequest(text=f"{field} must be at least {minimum}")
        if number > maximum:
            raise web.HTTPBadRequest(text=f"{field} must be at most {maximum}")
        return number

    @staticmethod
    def _coerce_float_range(value: Any, *, field: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
        if value is None or value == "":
            return default
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=f"{field} must be a number") from exc
        if number < minimum:
            raise web.HTTPBadRequest(text=f"{field} must be at least {minimum}")
        if number > maximum:
            raise web.HTTPBadRequest(text=f"{field} must be at most {maximum}")
        return number

    @staticmethod
    def _coerce_bool(value: Any, *, field: str, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise web.HTTPBadRequest(text=f"{field} must be a boolean")

    @classmethod
    def _coerce_browser_backend(cls, value: Any) -> str:
        backend = str(value or "agent-browser").strip() or "agent-browser"
        if backend not in cls.BROWSER_BACKENDS:
            raise web.HTTPBadRequest(text=f"backend must be one of: {', '.join(cls.BROWSER_BACKENDS)}")
        return backend

    @classmethod
    def _coerce_web_search_provider(cls, value: Any) -> str:
        provider = str(value or "searxng").strip().lower() or "searxng"
        if provider not in cls.WEB_SEARCH_PROVIDERS:
            raise web.HTTPBadRequest(text=f"provider must be one of: {', '.join(cls.WEB_SEARCH_PROVIDERS)}")
        return provider

    @classmethod
    def _coerce_web_search_freshness(cls, value: Any) -> str:
        freshness = str(value or "year").strip().lower() or "year"
        if freshness not in cls.WEB_SEARCH_FRESHNESS:
            raise web.HTTPBadRequest(text=f"freshness must be one of: {', '.join(cls.WEB_SEARCH_FRESHNESS)}")
        return freshness

    @staticmethod
    def _coerce_text_list(value: Any, *, field: str, default: list[str] | None = None) -> list[str]:
        if value is None or value == "":
            return list(default or [])
        if isinstance(value, str):
            candidates = value.replace("\n", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            raise web.HTTPBadRequest(text=f"{field} must be a list or comma-separated text")
        items: list[str] = []
        for item in candidates:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @classmethod
    def _normalize_searxng_engine_options(cls, engines: Any) -> list[dict[str, Any]]:
        if not isinstance(engines, list):
            return []
        options: list[dict[str, Any]] = []
        seen: set[str] = set()
        for engine in engines:
            if isinstance(engine, str):
                engine_id = engine.strip()
                label = engine_id
                shortcut = ""
                categories: list[str] = []
                enabled = None
            elif isinstance(engine, dict):
                engine_id = str(engine.get("name") or engine.get("id") or "").strip()
                label = str(engine.get("display_name") or engine.get("displayName") or engine_id).strip()
                shortcut = str(engine.get("shortcut") or "").strip()
                categories = cls._coerce_text_list(engine.get("categories", []), field="categories", default=[])
                enabled = engine.get("enabled") if isinstance(engine.get("enabled"), bool) else None
            else:
                continue
            if not engine_id or engine_id in seen:
                continue
            seen.add(engine_id)
            options.append({
                "id": engine_id,
                "label": label or engine_id,
                "shortcut": shortcut,
                "categories": categories,
                "enabled": enabled,
            })
        return options

    @classmethod
    def _normalize_searxng_category_options(cls, categories: Any) -> list[dict[str, str]]:
        if isinstance(categories, dict):
            candidates = list(categories.keys())
        else:
            candidates = categories
        options: list[dict[str, str]] = []
        seen: set[str] = set()
        for category in cls._coerce_text_list(candidates, field="categories", default=[]):
            if category in seen:
                continue
            seen.add(category)
            options.append({"id": category, "label": category})
        return options

    @classmethod
    def _searxng_options_payload(cls, config_payload: dict[str, Any], *, url: str) -> dict[str, Any]:
        engines = cls._normalize_searxng_engine_options(config_payload.get("engines"))
        categories = cls._normalize_searxng_category_options(config_payload.get("categories"))
        if not categories:
            category_names: list[str] = []
            for engine in engines:
                category_names.extend(engine.get("categories") or [])
            categories = cls._normalize_searxng_category_options(category_names)
        return {
            "url": url,
            "engines": engines,
            "categories": categories,
            "fallback": False,
            "warning": "",
        }

    @classmethod
    def _fallback_searxng_options_payload(cls, *, url: str, warning: str) -> dict[str, Any]:
        return {
            "url": url,
            "engines": [
                {"id": engine, "label": engine, "shortcut": "", "categories": [], "enabled": None}
                for engine in cls.SEARXNG_FALLBACK_ENGINES
            ],
            "categories": [{"id": category, "label": category} for category in cls.SEARXNG_FALLBACK_CATEGORIES],
            "fallback": True,
            "warning": warning,
        }

    @staticmethod
    def _searxng_config_url(searxng_url: str) -> str:
        base = str(searxng_url or "").strip().rstrip("/")
        if base.lower().endswith("/search"):
            base = base[:-len("/search")]
        return join_url_path(base, "/config")

    def _apply_optional_secret_field(self, target: Any, body: dict[str, Any], field: str) -> None:
        clear_field = f"clear_{field}"
        if self._coerce_bool(body.get(clear_field), field=clear_field, default=False):
            setattr(target, field, "")
            return
        if field not in body:
            return
        value = self._coerce_optional_text(body.get(field), default="") or ""
        if value:
            setattr(target, field, value)

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

    def _reload_web_search_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted web search settings to running web tools when possible."""
        if not force and not payload.get("restart_required"):
            return payload

        updated = dict(payload)
        agent = self._get_agent()
        reload_web_search = getattr(agent, "reload_web_search_from_config", None) if agent is not None else None
        if not callable(reload_web_search):
            updated["runtime_reloaded"] = False
            return updated

        try:
            runtime = reload_web_search(Config.load(self._get_config_path()))
        except Exception as exc:
            logger.warning("Web search runtime reload failed after settings change: {}", exc)
            updated["runtime_reloaded"] = False
            updated["reload_error"] = str(exc)
            return updated

        updated["restart_required"] = False
        updated["runtime_reloaded"] = True
        updated["runtime"] = self._json_safe(runtime)
        return updated

    def _reload_browser_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted browser settings to running browser tools when possible."""
        if not force and not payload.get("restart_required"):
            return payload

        updated = dict(payload)
        agent = self._get_agent()
        reload_browser = getattr(agent, "reload_browser_from_config", None) if agent is not None else None
        if not callable(reload_browser):
            updated["runtime_reloaded"] = False
            return updated

        try:
            runtime = reload_browser(Config.load(self._get_config_path()))
        except Exception as exc:
            logger.warning("Browser runtime reload failed after settings change: {}", exc)
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
    def _raise_credential_store_error(exc: CredentialStoreError) -> None:
        if isinstance(exc, CredentialNotFoundError):
            raise web.HTTPNotFound(text=str(exc)) from exc
        raise web.HTTPBadRequest(text=str(exc)) from exc

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
        payload = {
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
        destructive_reason = getattr(request, "destructive_reason", None) or classification.get("destructive_reason")
        if destructive_reason:
            payload["destructive_reason"] = destructive_reason
        return payload

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
        payload = serialize_run_event(
            event,
            include_event_id=False,
            extra={
                "type": "run_event",
                "channel": event.channel or self._channel_from_session(event.session_id),
                "channel_type": self.channel_type,
                "external_chat_id": event.external_chat_id,
            },
        )
        sent: set[web.WebSocketResponse] = set()
        session_ws = self._session_connections.get(event.session_id)
        if session_ws is not None and not session_ws.closed:
            await session_ws.send_json(payload)
            sent.add(session_ws)

        if self._channel_from_session(event.session_id) == self.channel_instance_id:
            return

        # Browser clients can inspect external-channel sessions, so broadcast
        # non-web run events to connected Web inspectors as live trace updates.
        for ws in list(self._socket_sessions.keys()):
            if ws in sent or ws.closed:
                continue
            await ws.send_json(payload)

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

    async def _handle_settings_codex_auth_status(self, request: web.Request) -> web.Response:
        from ..auth.codex import CodexAuthError, get_codex_status

        try:
            status = get_codex_status(self._get_app_home())
        except CodexAuthError as exc:
            return web.json_response(
                {
                    "provider": "openai-codex",
                    "configured": False,
                    "error": str(exc),
                },
                status=400,
            )
        return web.json_response(
            {
                "provider": "openai-codex",
                "configured": status.configured,
                "path": str(status.path),
                "expires_at": status.expires_at,
                "expired": status.expired,
                "account_id": status.account_id,
            }
        )

    async def _handle_settings_codex_auth_login(self, request: web.Request) -> web.Response:
        from ..auth.codex import CodexAuthError, codex_start_device_auth

        try:
            device_auth = codex_start_device_auth()
        except CodexAuthError as exc:
            return web.json_response({"ok": False, "provider": "openai-codex", "error": str(exc)}, status=502)
        return web.json_response(
            {
                "ok": True,
                "provider": "openai-codex",
                "mode": "web_device_code",
                "verification_uri": device_auth.verification_uri,
                "user_code": device_auth.user_code,
                "device_auth_id": device_auth.device_auth_id,
                "interval": device_auth.poll_interval,
                "expires_in": device_auth.expires_in,
                "message": "Open the verification URL and enter the code to complete OpenAI Codex login.",
            }
        )

    async def _handle_settings_codex_auth_poll(self, request: web.Request) -> web.Response:
        from ..auth.codex import CodexAuthError, codex_poll_device_auth, get_codex_status

        body = await self._read_json_body(request)
        try:
            result = codex_poll_device_auth(
                self._coerce_optional_text(body.get("device_auth_id")),
                self._coerce_optional_text(body.get("user_code")),
                app_home=self._get_app_home(),
            )
            status = get_codex_status(self._get_app_home()) if result.status == "authorized" else None
        except CodexAuthError as exc:
            return web.json_response({"ok": False, "provider": "openai-codex", "error": str(exc)}, status=400)
        payload: dict[str, Any] = {"ok": True, "provider": "openai-codex", "status": result.status}
        if status is not None:
            payload["auth"] = {
                "configured": status.configured,
                "path": str(status.path),
                "expires_at": status.expires_at,
                "expired": status.expired,
                "account_id": status.account_id,
            }
            payload = self._reload_agent_llm_from_config(payload, force=True)
        return web.json_response(payload)

    async def _handle_settings_codex_auth_logout(self, request: web.Request) -> web.Response:
        from ..auth.codex import codex_auth_path, delete_codex_token

        app_home = self._get_app_home()
        path = codex_auth_path(app_home)
        removed = delete_codex_token(app_home)
        return web.json_response(
            {
                "ok": True,
                "provider": "openai-codex",
                "removed": removed,
                "path": str(path),
            }
        )

    async def _handle_settings_copilot_auth_status(self, request: web.Request) -> web.Response:
        from ..auth.copilot import CopilotAuthError, get_copilot_status

        try:
            status = get_copilot_status(self._get_app_home())
        except CopilotAuthError as exc:
            return web.json_response({"provider": "copilot", "configured": False, "error": str(exc)}, status=400)
        return web.json_response({"provider": "copilot", "configured": status.configured, "path": str(status.path)})

    async def _handle_settings_copilot_auth_login(self, request: web.Request) -> web.Response:
        from ..auth.copilot import CopilotAuthError, copilot_start_device_auth

        try:
            device_auth = copilot_start_device_auth()
        except CopilotAuthError as exc:
            return web.json_response({"ok": False, "provider": "copilot", "error": str(exc)}, status=502)
        return web.json_response(
            {
                "ok": True,
                "provider": "copilot",
                "mode": "web_device_code",
                "verification_uri": device_auth.verification_uri,
                "user_code": device_auth.user_code,
                "device_code": device_auth.device_code,
                "interval": device_auth.poll_interval,
                "expires_in": device_auth.expires_in,
            }
        )

    async def _handle_settings_copilot_auth_poll(self, request: web.Request) -> web.Response:
        from ..auth.copilot import CopilotAuthError, copilot_poll_device_auth, get_copilot_status

        body = await self._read_json_body(request)
        try:
            result = copilot_poll_device_auth(
                self._coerce_optional_text(body.get("device_code")),
                app_home=self._get_app_home(),
            )
            status = get_copilot_status(self._get_app_home()) if result.status == "authorized" else None
        except CopilotAuthError as exc:
            return web.json_response({"ok": False, "provider": "copilot", "error": str(exc)}, status=400)
        payload: dict[str, Any] = {"ok": True, "provider": "copilot", "status": result.status}
        if status is not None:
            payload["auth"] = {"configured": status.configured, "path": str(status.path)}
            payload = self._reload_agent_llm_from_config(payload, force=True)
        return web.json_response(payload)

    async def _handle_settings_copilot_auth_logout(self, request: web.Request) -> web.Response:
        from ..auth.copilot import copilot_auth_path, delete_copilot_token

        app_home = self._get_app_home()
        path = copilot_auth_path(app_home)
        removed = delete_copilot_token(app_home)
        return web.json_response({"ok": True, "provider": "copilot", "removed": removed, "path": str(path)})

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

    async def _handle_settings_credentials(self, request: web.Request) -> web.Response:
        provider = self._coerce_optional_text(request.query.get("provider"))
        try:
            credentials = list_credentials(provider, app_home=self._get_config_path().parent)
        except CredentialStoreError as exc:
            self._raise_credential_store_error(exc)
        return web.json_response({"credentials": credentials})

    async def _handle_settings_credential_create(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        provider = self._coerce_optional_text(body.get("provider"))
        secret = self._coerce_optional_text(body.get("secret")) or self._coerce_optional_text(body.get("api_key"))
        if provider is None or secret is None:
            raise web.HTTPBadRequest(text="provider and secret are required")
        scopes = body.get("scopes")
        if not isinstance(scopes, list):
            scopes = None
        try:
            credential = add_credential(
                provider,
                secret,
                label=self._coerce_optional_text(body.get("label")),
                auth_type=self._coerce_optional_text(body.get("auth_type"), default="api_key") or "api_key",
                base_url=self._coerce_optional_text(body.get("base_url")),
                scopes=scopes,
                app_home=self._get_config_path().parent,
            )
        except CredentialStoreError as exc:
            self._raise_credential_store_error(exc)
        return web.json_response({"ok": True, "credential": credential})

    async def _handle_settings_credential_delete(self, request: web.Request) -> web.Response:
        provider = self._coerce_optional_text(request.match_info.get("provider"))
        credential_id = self._coerce_optional_text(request.match_info.get("credential_id"))
        if provider is None or credential_id is None:
            raise web.HTTPBadRequest(text="provider and credential_id are required")
        try:
            payload = remove_credential(provider, credential_id, app_home=self._get_config_path().parent)
            cleanup = self._get_provider_settings().remove_credential_references(provider, credential_id)
            payload.update(cleanup)
        except CredentialStoreError as exc:
            self._raise_credential_store_error(exc)
        except ProviderSettingsError as exc:
            self._raise_provider_settings_error(exc)
        payload = self._reload_agent_llm_from_config(payload, force=bool(payload.get("restart_required")))
        return web.json_response(payload)

    async def _handle_settings_credential_default(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        provider = self._coerce_optional_text(body.get("provider"))
        capability = self._coerce_optional_text(body.get("capability"))
        credential_id = self._coerce_optional_text(body.get("credential_id"))
        if credential_id is None or (provider is None and capability is None):
            raise web.HTTPBadRequest(text="credential_id plus provider or capability is required")
        try:
            if provider is not None:
                credential = set_provider_default(provider, credential_id, app_home=self._get_config_path().parent)
            else:
                credential = set_capability_default(capability or "", credential_id, app_home=self._get_config_path().parent)
        except CredentialStoreError as exc:
            self._raise_credential_store_error(exc)
        return web.json_response({"ok": True, "credential": credential})

    async def _handle_settings_provider_credential(self, request: web.Request) -> web.Response:
        provider_id = self._coerce_optional_text(request.match_info.get("provider_id"))
        if provider_id is None:
            raise web.HTTPBadRequest(text="provider_id is required")
        body = await self._read_json_body(request)
        credential_id = self._coerce_optional_text(body.get("credential_id"))
        if credential_id is None:
            raise web.HTTPBadRequest(text="credential_id is required")
        try:
            payload = self._get_provider_settings().set_provider_credential(provider_id, credential_id)
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

    async def _handle_settings_llm(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"llm": self._llm_payload(config)})

    async def _handle_settings_llm_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config_path = self._get_config_path()
        config = Config.load(config_path)
        decoding_mode = self._coerce_optional_text(body.get("decoding_mode"))
        if decoding_mode:
            if decoding_mode == "custom":
                decoding = body.get("decoding")
                if decoding is not None and not isinstance(decoding, dict):
                    raise web.HTTPBadRequest(text="decoding must be a JSON object")
                self._apply_custom_llm_decoding(config, decoding or {})
            else:
                self._apply_llm_decoding_preset(config, decoding_mode)
        elif "pass_decoding_params" in body:
            config.llm.pass_decoding_params = bool(body.get("pass_decoding_params"))
        if "semantic_contract_classifier_enabled" in body:
            config.agent.semantic_contract_classifier_enabled = bool(body.get("semantic_contract_classifier_enabled"))
        if "semantic_contract_classifier_confidence_threshold" in body:
            config.agent.semantic_contract_classifier_confidence_threshold = self._coerce_float_range(
                body.get("semantic_contract_classifier_confidence_threshold"),
                field="semantic_contract_classifier_confidence_threshold",
                default=config.agent.semantic_contract_classifier_confidence_threshold,
                minimum=0.0,
                maximum=1.0,
            )
        config.save(config_path)
        payload = {
            "llm": self._llm_payload(config),
            "restart_required": True,
        }
        payload = self._reload_agent_llm_from_config(payload, force=True)
        agent = self._get_agent()
        if agent is not None:
            agent.config = config.agent
            llm_calls = getattr(agent, "llm_calls", None)
            if llm_calls is not None:
                llm_calls.config = config.agent
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

    @staticmethod
    def _git_output(args: list[str], *, cwd: Path) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "git command failed").strip())
        return result.stdout.strip()

    def _build_update_status_payload(self) -> dict[str, Any]:
        try:
            root = update_cli.find_project_root()
            current_rev = self._git_output(["rev-parse", "HEAD"], cwd=root)
            branch = self._git_output(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
            dirty = bool(self._git_output(["status", "--porcelain"], cwd=root))
            commits_behind = update_cli.check_update_available(project_root=root, branch=branch)
            return {
                "ok": True,
                "supported": True,
                "project_root": str(root),
                "branch": branch,
                "current_rev": current_rev,
                "current_rev_short": current_rev[:7],
                "dirty": dirty,
                "commits_behind": commits_behind,
                "update_available": commits_behind > 0,
            }
        except Exception as exc:
            return {
                "ok": False,
                "supported": False,
                "error": str(exc),
                "dirty": False,
                "commits_behind": 0,
                "update_available": False,
            }

    async def _handle_settings_update_status(self, request: web.Request) -> web.Response:
        payload = await asyncio.to_thread(self._build_update_status_payload)
        return web.json_response(payload)

    async def _restart_gateway_after_response(self, config_path: Path | None = None) -> None:
        await asyncio.sleep(1.0)
        try:
            try:
                linux_status = service_linux.get_service_status()
            except RuntimeError:
                linux_status = None
            if linux_status is not None and getattr(linux_status, "installed", False):
                service_linux.restart_service()
                return

            pid_file = service_background.get_pid_file()
            try:
                pid_file.unlink()
            except FileNotFoundError:
                pass
            service_background.start_service(config_path=config_path, python_executable=Path(sys.executable))
        except Exception:
            logger.exception("Failed to restart OpenSprite gateway after update")
            return
        os._exit(0)

    async def _handle_settings_update_apply(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        restart = bool(body.get("restart", True))
        try:
            result = await asyncio.to_thread(update_cli.update_checkout, branch="main", install_dev=False)
        except update_cli.UpdateError as exc:
            raise web.HTTPConflict(text=str(exc)) from exc
        except Exception as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc

        payload = {
            "ok": True,
            "updated": result.updated,
            "before_rev": result.before_rev,
            "before_rev_short": result.before_rev[:7],
            "after_rev": result.after_rev,
            "after_rev_short": result.after_rev[:7],
            "branch": result.branch,
            "project_root": str(result.project_root),
            "python": str(result.python_executable),
            "restart_scheduled": restart,
        }
        if restart:
            config_path = Path(self.config["config_path"]).expanduser() if self.config.get("config_path") else None
            asyncio.create_task(self._restart_gateway_after_response(config_path=config_path))
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

    async def _handle_settings_network(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"network": self._network_payload(config)})

    async def _handle_settings_network_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config_path = self._get_config_path()
        config = Config.load(config_path)
        config.network.http_proxy = self._coerce_optional_text(body.get("http_proxy"), default="") or ""
        config.network.https_proxy = self._coerce_optional_text(body.get("https_proxy"), default="") or ""
        config.network.no_proxy = self._coerce_optional_text(body.get("no_proxy"), default="") or ""
        config.save(config_path)
        self._apply_network_environment(config)
        return web.json_response({"network": self._network_payload(config), "restart_required": False})

    async def _handle_settings_search(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"search": self._web_search_payload(config)})

    async def _handle_settings_search_searxng_options(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        search = config.tools.web_search
        searxng_url = self._coerce_optional_text(request.query.get("url"), default=search.searxng_url) or "https://searx.be"
        try:
            async with httpx.AsyncClient(proxy=search.proxy) as client:
                response = await client.get(
                    self._searxng_config_url(searxng_url),
                    headers={"Accept": "application/json", "User-Agent": self.SEARXNG_OPTIONS_USER_AGENT},
                    timeout=10.0,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("SearXNG options metadata unavailable | url={} error={}", searxng_url, exc)
            return web.json_response({
                "searxng": self._fallback_searxng_options_payload(
                    url=searxng_url,
                    warning=f"Unable to load SearXNG /config metadata: {exc}",
                )
            })
        if not isinstance(payload, dict):
            return web.json_response({
                "searxng": self._fallback_searxng_options_payload(
                    url=searxng_url,
                    warning="SearXNG /config response was not a JSON object.",
                )
            })
        return web.json_response({"searxng": self._searxng_options_payload(payload, url=searxng_url)})

    async def _handle_settings_search_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config_path = self._get_config_path()
        config = Config.load(config_path)
        search = config.tools.web_search
        search.provider = self._coerce_web_search_provider(body.get("provider", search.provider))
        search.freshness = self._coerce_web_search_freshness(body.get("freshness", search.freshness))
        search.max_results = self._coerce_positive_int(
            body.get("max_results"),
            field="max_results",
            default=search.max_results,
            minimum=1,
            maximum=100,
        )
        search.duckduckgo_max_pages = self._coerce_positive_int(
            body.get("duckduckgo_max_pages"),
            field="duckduckgo_max_pages",
            default=search.duckduckgo_max_pages,
            minimum=1,
            maximum=50,
        )
        search.searxng_max_pages = self._coerce_positive_int(
            body.get("searxng_max_pages"),
            field="searxng_max_pages",
            default=search.searxng_max_pages,
            minimum=1,
            maximum=50,
        )
        if "searxng_url" in body:
            search.searxng_url = self._coerce_optional_text(body.get("searxng_url"), default="") or "https://searx.be"
        if "searxng_engines" in body:
            search.searxng_engines = self._coerce_text_list(body.get("searxng_engines"), field="searxng_engines", default=search.searxng_engines)
        if "searxng_categories" in body:
            search.searxng_categories = self._coerce_text_list(body.get("searxng_categories"), field="searxng_categories", default=search.searxng_categories)
        if "proxy" in body:
            search.proxy = self._coerce_optional_text(body.get("proxy"), default="") or None
        for field in ("brave_api_key", "tavily_api_key", "jina_api_key"):
            self._apply_optional_secret_field(search, body, field)
        config.save(config_path)
        payload = {"search": self._web_search_payload(config), "restart_required": True}
        payload = self._reload_web_search_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_browser(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"browser": self._browser_payload(config)})

    async def _handle_settings_browser_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config_path = self._get_config_path()
        config = Config.load(config_path)
        browser = config.tools.browser
        browser.enabled = self._coerce_bool(body.get("enabled"), field="enabled", default=browser.enabled)
        browser.backend = self._coerce_browser_backend(body.get("backend", browser.backend))
        browser.command_timeout = self._coerce_positive_int(
            body.get("command_timeout"),
            field="command_timeout",
            default=browser.command_timeout,
            minimum=1,
            maximum=600,
        )
        browser.session_timeout = self._coerce_positive_int(
            body.get("session_timeout"),
            field="session_timeout",
            default=browser.session_timeout,
            minimum=1,
            maximum=86400,
        )
        if "cdp_url" in body:
            browser.cdp_url = self._coerce_optional_text(body.get("cdp_url"), default="") or ""
        browser.allow_private_urls = self._coerce_bool(
            body.get("allow_private_urls"),
            field="allow_private_urls",
            default=browser.allow_private_urls,
        )
        for field in (
            "browserbase_api_key",
            "browserbase_project_id",
            "browserbase_base_url",
            "browser_use_api_key",
            "browser_use_base_url",
            "firecrawl_api_key",
            "firecrawl_base_url",
        ):
            if field in body:
                setattr(browser, field, self._coerce_optional_text(body.get(field), default="") or "")
        for field in ("browserbase_proxies", "browserbase_advanced_stealth", "browserbase_keep_alive"):
            if field in body:
                setattr(browser, field, self._coerce_bool(body.get(field), field=field, default=getattr(browser, field)))
        config.save(config_path)
        payload = {"browser": self._browser_payload(config), "restart_required": True}
        payload = self._reload_browser_from_config(payload)
        return web.json_response(payload)

    async def _handle_settings_log(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"log": self._log_payload(config)})

    async def _handle_settings_log_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config_path = self._get_config_path()
        config = Config.load(config_path)
        if "enabled" in body:
            config.log.enabled = bool(body.get("enabled"))
        if "level" in body:
            config.log.level = self._coerce_log_level(body.get("level"))
        if "retention_days" in body:
            config.log.retention_days = self._coerce_positive_int(
                body.get("retention_days"),
                field="retention_days",
                default=config.log.retention_days,
                minimum=1,
            )
        if "log_system_prompt" in body:
            config.log.log_system_prompt = bool(body.get("log_system_prompt"))
        if "log_system_prompt_lines" in body:
            config.log.log_system_prompt_lines = self._coerce_positive_int(
                body.get("log_system_prompt_lines"),
                field="log_system_prompt_lines",
                default=config.log.log_system_prompt_lines,
                minimum=0,
            )
        if "log_reasoning_details" in body:
            config.log.log_reasoning_details = bool(body.get("log_reasoning_details"))
        config.save(config_path)
        setup_log(config.log)
        return web.json_response({"log": self._log_payload(config), "restart_required": False, "runtime_reloaded": True})

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
        self._validate_bind_auth_config(host)

        middlewares = [self._auth_middleware] if self._get_auth_token() else []
        self.app = web.Application(middlewares=middlewares)
        self.app.router.add_get(ws_path, self._handle_websocket)
        self.app.router.add_get(health_path, self._handle_health)
        self.app.router.add_get("/api/commands", self._api.handle_command_catalog)
        self.app.router.add_get("/api/curator/status", self._api.handle_curator_status)
        self.app.router.add_get("/api/curator/history", self._api.handle_curator_history)
        self.app.router.add_post("/api/curator/{action}", self._api.handle_curator_action)
        self.app.router.add_get("/api/sessions/status", self._api.handle_session_status)
        self.app.router.add_get("/api/sessions/timeline", self._api.handle_session_timeline)
        self.app.router.add_get("/api/sessions", self._api.handle_sessions)
        self.app.router.add_delete("/api/sessions", self._api.handle_sessions_delete)
        self.app.router.add_delete("/api/sessions/{session_id}", self._api.handle_sessions_delete)
        self.app.router.add_get("/api/storage/status", self._api.handle_storage_status)
        self.app.router.add_get("/api/background-processes", self._api.handle_background_processes)
        self.app.router.add_get("/api/evals/long-task", self._api.handle_long_task_eval_status)
        self.app.router.add_post("/api/evals/long-task/smoke", self._api.handle_long_task_eval_smoke)
        self.app.router.add_post("/api/evals/long-task/controlled", self._api.handle_long_task_eval_controlled)
        self.app.router.add_post("/api/evals/task-completion/smoke", self._api.handle_task_completion_eval_smoke)
        self.app.router.add_post("/api/evals/task-completion/run", self._api.handle_task_completion_eval_run)
        self.app.router.add_get("/api/evals/task-completion/history", self._api.handle_task_completion_eval_history)
        self.app.router.add_delete("/api/evals/task-completion/history", self._api.handle_task_completion_eval_history_clear)
        self.app.router.add_delete(
            "/api/evals/task-completion/history/{eval_id}",
            self._api.handle_task_completion_eval_history_delete,
        )
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
        self.app.router.add_get("/api/settings/auth/openai-codex", self._handle_settings_codex_auth_status)
        self.app.router.add_post("/api/settings/auth/openai-codex/login", self._handle_settings_codex_auth_login)
        self.app.router.add_post("/api/settings/auth/openai-codex/poll", self._handle_settings_codex_auth_poll)
        self.app.router.add_post("/api/settings/auth/openai-codex/logout", self._handle_settings_codex_auth_logout)
        self.app.router.add_get("/api/settings/auth/copilot", self._handle_settings_copilot_auth_status)
        self.app.router.add_post("/api/settings/auth/copilot/login", self._handle_settings_copilot_auth_login)
        self.app.router.add_post("/api/settings/auth/copilot/poll", self._handle_settings_copilot_auth_poll)
        self.app.router.add_post("/api/settings/auth/copilot/logout", self._handle_settings_copilot_auth_logout)
        self.app.router.add_get("/api/settings/credentials", self._handle_settings_credentials)
        self.app.router.add_post("/api/settings/credentials", self._handle_settings_credential_create)
        self.app.router.add_delete(
            "/api/settings/credentials/{provider}/{credential_id}",
            self._handle_settings_credential_delete,
        )
        self.app.router.add_post("/api/settings/credentials/default", self._handle_settings_credential_default)
        self.app.router.add_put("/api/settings/providers/{provider_id}/connect", self._handle_settings_provider_connect)
        self.app.router.add_post("/api/settings/providers/{provider_id}/credential", self._handle_settings_provider_credential)
        self.app.router.add_post("/api/settings/providers/{provider_id}/disconnect", self._handle_settings_provider_disconnect)
        self.app.router.add_put("/api/settings/providers/{provider_id}/options", self._handle_settings_provider_options_update)
        self.app.router.add_get("/api/settings/models", self._handle_settings_models)
        self.app.router.add_post("/api/settings/models/select", self._handle_settings_model_select)
        self.app.router.add_get("/api/settings/llm", self._handle_settings_llm)
        self.app.router.add_put("/api/settings/llm", self._handle_settings_llm_update)
        self.app.router.add_get("/api/settings/update", self._handle_settings_update_status)
        self.app.router.add_post("/api/settings/update", self._handle_settings_update_apply)
        self.app.router.add_get("/api/settings/media", self._handle_settings_media)
        self.app.router.add_put("/api/settings/media", self._handle_settings_media_update)
        self.app.router.add_get("/api/settings/schedule", self._handle_settings_schedule)
        self.app.router.add_put("/api/settings/schedule", self._handle_settings_schedule_update)
        self.app.router.add_get("/api/settings/network", self._handle_settings_network)
        self.app.router.add_put("/api/settings/network", self._handle_settings_network_update)
        self.app.router.add_get("/api/settings/search", self._handle_settings_search)
        self.app.router.add_get("/api/settings/search/searxng-options", self._handle_settings_search_searxng_options)
        self.app.router.add_put("/api/settings/search", self._handle_settings_search_update)
        self.app.router.add_get("/api/settings/browser", self._handle_settings_browser)
        self.app.router.add_put("/api/settings/browser", self._handle_settings_browser_update)
        self.app.router.add_get("/api/settings/log", self._handle_settings_log)
        self.app.router.add_put("/api/settings/log", self._handle_settings_log_update)
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
