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
from pydantic import ValidationError

from .identity import build_session_id, normalize_identifier
from ..agent.harness_policy import HarnessPolicyService
from ..agent.harness_profile import HarnessProfile
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
from ..config import Config, MessagesConfig, ToolPermissionProfileOverrideConfig
from ..config.defaults import (
    DEFAULT_BROWSER_BACKEND,
    DEFAULT_BROWSER_COMMAND_TIMEOUT,
    DEFAULT_BROWSER_LAUNCH_ARGS,
    DEFAULT_BROWSER_SESSION_TIMEOUT,
    DEFAULT_CRON_TIMEZONE,
    DEFAULT_DUCKDUCKGO_MAX_PAGES,
    DEFAULT_LOG_ENABLED,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_REASONING_DETAILS,
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_LOG_SYSTEM_PROMPT,
    DEFAULT_LOG_SYSTEM_PROMPT_LINES,
    DEFAULT_HTTP_PROXY,
    DEFAULT_HTTPS_PROXY,
    DEFAULT_NO_PROXY,
    DEFAULT_SEARXNG_URL,
    DEFAULT_SEARXNG_MAX_PAGES,
    DEFAULT_WEB_SEARCH_FRESHNESS,
    DEFAULT_WEB_SEARCH_MAX_RESULTS,
    DEFAULT_WEB_SEARCH_PROVIDER,
    WEB_SEARCH_FRESHNESS_OPTIONS,
    BROWSER_BACKENDS as DEFAULT_BROWSER_BACKENDS,
    LOG_LEVELS as DEFAULT_LOG_LEVELS,
    WEB_SEARCH_PROVIDERS as DEFAULT_WEB_SEARCH_PROVIDERS,
)
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
from ..ops import OperationAuditRecord
from ..cron.presentation import format_cron_timestamp, format_cron_timing
from ..runs.schema import serialize_diff_summary, serialize_run_event, serialize_work_state_todos
from ..runs.session_entries import serialize_session_entries
from ..tools.approval import classify_permission_request
from ..tools.browser import _validate_navigation_url
from ..tools.browser_runtime import AgentBrowserRuntime, browser_cloud_status, cloud_provider_from_config
from ..tools.permissions import ALL_RISK_LEVELS, APPROVAL_MODES, ToolPermissionPolicy
from ..utils.log import logger, setup_log
from ..utils.url import join_url_path
from .web_api import WebApiHandlers
from . import web_frontend_runtime
from . import web_settings_coercion, web_settings_reload
from . import web_settings_support
from . import web_settings_payloads
from .web_routes import register_web_routes


class WebAdapter(MessageAdapter):
    """WebSocket adapter for browser-based chat clients."""

    LOG_LEVELS = DEFAULT_LOG_LEVELS
    LLM_DECODING_MODE_ORDER = ("provider_default", "precise", "balanced", "creative", "custom")
    WEB_SEARCH_PROVIDERS = DEFAULT_WEB_SEARCH_PROVIDERS
    WEB_SEARCH_FRESHNESS = WEB_SEARCH_FRESHNESS_OPTIONS
    SEARXNG_OPTIONS_USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 OpenSprite/0.1"
    SEARXNG_FALLBACK_ENGINES = (
        "duckduckgo",
        "google",
        "bing",
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
    BROWSER_BACKENDS = DEFAULT_BROWSER_BACKENDS
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
        return web_frontend_runtime.is_frontend_source_dir(path)

    def _resolve_frontend_source_dir(self) -> Path | None:
        return web_frontend_runtime.resolve_frontend_source_dir(self.config, module_path=Path(__file__).resolve())

    @staticmethod
    def _trim_process_output(value: str | None, limit: int = 2000) -> str:
        return web_frontend_runtime.trim_process_output(value, limit=limit)

    def _resolve_npm_executable(self) -> str | None:
        return web_frontend_runtime.resolve_npm_executable()

    def _is_frontend_auto_build_enabled(self) -> bool:
        value = self.config.get("frontend_auto_build", self.DEFAULT_CONFIG["frontend_auto_build"])
        return web_frontend_runtime.is_feature_enabled(value)

    def _is_frontend_auto_install_enabled(self) -> bool:
        value = self.config.get("frontend_auto_install", self.DEFAULT_CONFIG["frontend_auto_install"])
        return web_frontend_runtime.is_feature_enabled(value)

    def _frontend_dependencies_ready(self, source_dir: Path) -> bool:
        return web_frontend_runtime.frontend_dependencies_ready(source_dir)

    def _run_frontend_command(self, source_dir: Path, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return web_frontend_runtime.run_frontend_command(source_dir, args, timeout)

    def _maybe_install_frontend_dependencies(self, source_dir: Path, npm: str) -> bool:
        return web_frontend_runtime.maybe_install_frontend_dependencies(
            source_dir,
            npm,
            auto_install_enabled=self._is_frontend_auto_install_enabled(),
            install_timeout=self._get_frontend_install_timeout(),
            logger=logger,
        )

    def _maybe_build_frontend(self) -> None:
        web_frontend_runtime.maybe_build_frontend(
            self.config,
            default_config=self.DEFAULT_CONFIG,
            module_path=Path(__file__).resolve(),
            build_timeout=self._get_frontend_build_timeout(),
            install_timeout=self._get_frontend_install_timeout(),
            logger=logger,
        )

    def _resolve_frontend_dir(self) -> Path | None:
        return web_frontend_runtime.resolve_frontend_dir(self.config, module_path=Path(__file__).resolve())

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
        return web_settings_support.get_provider_settings(self)

    def _get_channel_settings(self) -> ChannelSettingsService:
        return web_settings_support.get_channel_settings(self)

    def _get_schedule_settings(self) -> ScheduleSettingsService:
        return web_settings_support.get_schedule_settings(self)

    def _get_mcp_settings(self) -> MCPSettingsService:
        return web_settings_support.get_mcp_settings(self)

    def _get_media_settings(self) -> MediaSettingsService:
        return web_settings_support.get_media_settings(self)

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
        return web_settings_payloads.network_payload(
            config,
            default_http_proxy=DEFAULT_HTTP_PROXY,
            default_https_proxy=DEFAULT_HTTPS_PROXY,
            default_no_proxy=DEFAULT_NO_PROXY,
        )

    @staticmethod
    def _browser_runtime_status() -> dict[str, Any]:
        return web_frontend_runtime.browser_runtime_status(WebAdapter._browser_command_prefix())

    @staticmethod
    def _browser_command_prefix() -> list[str]:
        return web_frontend_runtime.browser_command_prefix()

    @classmethod
    async def _run_browser_doctor_command(
        cls,
        args: list[str],
        *,
        timeout: int = 20,
        launch_args: str = "",
    ) -> dict[str, Any]:
        return await web_frontend_runtime.run_browser_doctor_command(
            args,
            timeout=timeout,
            launch_args=launch_args,
            command_prefix=cls._browser_command_prefix(),
        )

    @classmethod
    async def _run_browser_install_command(cls, *, timeout: int = 300) -> dict[str, Any]:
        return await web_frontend_runtime.run_browser_install_command(
            timeout=timeout,
            command_prefix=cls._browser_command_prefix(),
        )

    @staticmethod
    def _with_browser_diagnostic(result: dict[str, Any] | None) -> dict[str, Any]:
        return web_frontend_runtime.with_browser_diagnostic(result)

    @classmethod
    def _browser_payload(cls, config: Config) -> dict[str, Any]:
        return web_settings_payloads.browser_payload(
            config,
            default_backend=DEFAULT_BROWSER_BACKEND,
            default_command_timeout=DEFAULT_BROWSER_COMMAND_TIMEOUT,
            default_session_timeout=DEFAULT_BROWSER_SESSION_TIMEOUT,
            default_launch_args=DEFAULT_BROWSER_LAUNCH_ARGS,
            backends=cls.BROWSER_BACKENDS,
            browser_cloud_status_fn=browser_cloud_status,
            browser_runtime_status_fn=cls._browser_runtime_status,
        )

    @classmethod
    def _web_search_payload(cls, config: Config) -> dict[str, Any]:
        return web_settings_payloads.web_search_payload(
            config,
            default_provider=DEFAULT_WEB_SEARCH_PROVIDER,
            providers=cls.WEB_SEARCH_PROVIDERS,
            default_freshness=DEFAULT_WEB_SEARCH_FRESHNESS,
            freshness_values=cls.WEB_SEARCH_FRESHNESS,
            default_max_results=DEFAULT_WEB_SEARCH_MAX_RESULTS,
            default_duckduckgo_max_pages=DEFAULT_DUCKDUCKGO_MAX_PAGES,
            default_searxng_max_pages=DEFAULT_SEARXNG_MAX_PAGES,
            default_searxng_url=DEFAULT_SEARXNG_URL,
            coerce_text_list_fn=cls._coerce_text_list,
        )

    @staticmethod
    def _llm_decoding_payload(config: Config) -> dict[str, Any]:
        return web_settings_payloads.llm_decoding_payload(config)

    @classmethod
    def _llm_decoding_mode(cls, config: Config) -> str:
        return web_settings_payloads.llm_decoding_mode(config, presets=cls.LLM_DECODING_PRESETS)

    @classmethod
    def _apply_llm_decoding_preset(cls, config: Config, mode: str) -> None:
        web_settings_payloads.apply_llm_decoding_preset(
            config,
            mode,
            presets=cls.LLM_DECODING_PRESETS,
            mode_order=cls.LLM_DECODING_MODE_ORDER,
        )

    @staticmethod
    def _coerce_llm_float(value: Any, *, field: str, minimum: float | None = None, maximum: float | None = None) -> float:
        return web_settings_payloads.coerce_llm_float(value, field=field, minimum=minimum, maximum=maximum)

    @classmethod
    def _apply_custom_llm_decoding(cls, config: Config, decoding: dict[str, Any]) -> None:
        web_settings_payloads.apply_custom_llm_decoding(
            config,
            decoding,
            coerce_positive_int_fn=cls._coerce_positive_int,
        )

    @staticmethod
    def _anthropic_reasoning_budget(effort: str | None) -> int:
        return web_settings_payloads.anthropic_reasoning_budget(effort)

    @classmethod
    def _effective_llm_request_payload(cls, config: Config) -> dict[str, Any]:
        return web_settings_payloads.effective_llm_request_payload(config)

    @classmethod
    def _llm_payload(cls, config: Config) -> dict[str, Any]:
        return web_settings_payloads.llm_payload(
            config,
            mode_order=cls.LLM_DECODING_MODE_ORDER,
            presets=cls.LLM_DECODING_PRESETS,
        )

    @classmethod
    def _log_payload(cls, config: Config) -> dict[str, Any]:
        return web_settings_payloads.log_payload(config, default_log_level=DEFAULT_LOG_LEVEL, log_levels=cls.LOG_LEVELS)

    @classmethod
    def _permissions_payload(cls, config: Config) -> dict[str, Any]:
        return web_settings_payloads.permissions_payload(
            config,
            all_risk_levels=ALL_RISK_LEVELS,
            approval_modes=APPROVAL_MODES,
        )

    @classmethod
    def _harness_policy_preview_payload(cls, config: Config) -> dict[str, Any]:
        user_permissions = cls._permissions_payload(config)
        user_allowed_risks = set(user_permissions["allowed_risk_levels"])
        user_denied_risks = set(user_permissions["denied_risk_levels"])
        user_approval_mode = user_permissions.get("approval_mode") or "auto"
        user_approval_risks = set(user_permissions["approval_required_risk_levels"])
        profile_overrides = user_permissions.get("profile_overrides") or {}
        policy_service = HarnessPolicyService()
        profiles = cls._harness_policy_preview_profiles()
        rows = []
        for profile in profiles:
            policy = policy_service.select(profile)
            profile_override = profile_overrides.get(profile.name) or {}
            override_allowed_risks = set(profile_override.get("allowed_risk_levels") or sorted(ALL_RISK_LEVELS))
            override_denied_risks = set(profile_override.get("denied_risk_levels") or [])
            override_approval_mode = profile_override.get("approval_mode") or user_approval_mode
            override_approval_risks = set(profile_override.get("approval_required_risk_levels") or [])
            harness_allowed_risks = set(policy.allowed_risk_levels)
            harness_denied_risks = set(policy.denied_risk_levels)
            effective_allowed = sorted((user_allowed_risks & override_allowed_risks & harness_allowed_risks) - user_denied_risks - override_denied_risks - harness_denied_risks)
            effective_denied = sorted(user_denied_risks | override_denied_risks | harness_denied_risks | (ALL_RISK_LEVELS - set(effective_allowed)))
            effective_approval = set(policy.approval_required_risk_levels)
            if user_approval_mode != "auto":
                effective_approval |= user_approval_risks
            if override_approval_mode != "auto":
                effective_approval |= override_approval_risks
            effective_approval = sorted(effective_approval & set(effective_allowed))
            rows.append(
                {
                    "profile": profile.to_metadata(),
                    "profile_override": profile_override,
                    "policy": policy.to_metadata(),
                    "effective": {
                        "allowed_risk_levels": effective_allowed,
                        "denied_risk_levels": effective_denied,
                        "approval_required_risk_levels": effective_approval,
                        "user_approval_mode": user_approval_mode,
                        "profile_approval_mode": override_approval_mode,
                        "user_permissions_enabled": bool(user_permissions["enabled"]),
                    },
                }
            )
        return {
            "schema_version": 1,
            "user_permissions": user_permissions,
            "rows": rows,
        }

    @staticmethod
    def _harness_policy_preview_profiles() -> tuple[HarnessProfile, ...]:
        return (
            HarnessProfile(
                name="chat",
                task_type="conversation",
                verification_policy="none",
                continuation_policy="minimal",
                reason="preview profile for low-risk chat turns",
            ),
            HarnessProfile(
                name="research",
                task_type="web_research",
                required_tool_groups=("web_research",),
                required_evidence=("web_source", "source_reference"),
                verification_policy="source_grounded",
                continuation_policy="bounded_with_source_fetch",
                approval_required_risk_levels=("external_side_effect",),
                reason="preview profile for source-grounded web research turns",
            ),
            HarnessProfile(
                name="coding",
                task_type="workspace_analysis",
                required_tool_groups=("workspace_read",),
                required_evidence=("workspace_evidence",),
                verification_policy="focused_if_possible",
                continuation_policy="bounded_with_verification",
                approval_required_risk_levels=("external_side_effect", "configuration"),
                reason="preview profile for workspace analysis turns",
            ),
            HarnessProfile(
                name="coding",
                task_type="workspace_change",
                required_tool_groups=("workspace_read", "workspace_write"),
                required_evidence=("file_change",),
                verification_policy="focused_if_possible",
                continuation_policy="bounded_with_verification",
                approval_required_risk_levels=("external_side_effect", "configuration"),
                reason="preview profile for workspace change turns",
            ),
            HarnessProfile(
                name="media",
                task_type="media_extraction",
                required_tool_groups=("media",),
                required_evidence=("media_artifact",),
                verification_policy="artifact_required",
                continuation_policy="bounded",
                reason="preview profile for media extraction turns",
            ),
            HarnessProfile(
                name="ops",
                task_type="operations",
                required_tool_groups=("workspace_read",),
                required_evidence=("audit_trace",),
                verification_policy="validate_or_report",
                continuation_policy="approval_bounded",
                approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
                reason="preview profile for operations turns",
            ),
        )

    @classmethod
    def _coerce_approval_mode(cls, value: Any) -> str | None:
        return web_settings_coercion.coerce_approval_mode(value, approval_modes=APPROVAL_MODES)

    @classmethod
    def _coerce_risk_level_list(cls, value: Any, *, field: str, default: list[str] | None = None) -> list[str]:
        return web_settings_coercion.coerce_risk_level_list(
            value,
            field=field,
            default=default,
            all_risk_levels=ALL_RISK_LEVELS,
        )

    @classmethod
    def _coerce_permission_profile_overrides(cls, value: Any, *, default: dict[str, ToolPermissionProfileOverrideConfig]) -> dict[str, ToolPermissionProfileOverrideConfig]:
        return web_settings_coercion.coerce_permission_profile_overrides(
            value,
            default=default,
            all_risk_levels=ALL_RISK_LEVELS,
        )

    @classmethod
    def _coerce_log_level(cls, value: Any) -> str:
        return web_settings_coercion.coerce_log_level(value, default_log_level=DEFAULT_LOG_LEVEL, log_levels=cls.LOG_LEVELS)

    @staticmethod
    def _coerce_positive_int(value: Any, *, field: str, default: int, minimum: int = 0, maximum: int = 3650) -> int:
        return web_settings_coercion.coerce_positive_int(value, field=field, default=default, minimum=minimum, maximum=maximum)

    @staticmethod
    def _coerce_float_range(value: Any, *, field: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
        return web_settings_coercion.coerce_float_range(value, field=field, default=default, minimum=minimum, maximum=maximum)

    @staticmethod
    def _coerce_bool(value: Any, *, field: str, default: bool) -> bool:
        return web_settings_coercion.coerce_bool(value, field=field, default=default)

    @classmethod
    def _coerce_browser_backend(cls, value: Any) -> str:
        return web_settings_coercion.coerce_browser_backend(
            value,
            default_backend=DEFAULT_BROWSER_BACKEND,
            backends=cls.BROWSER_BACKENDS,
        )

    @classmethod
    def _coerce_web_search_provider(cls, value: Any) -> str:
        return web_settings_coercion.coerce_web_search_provider(
            value,
            default_provider=DEFAULT_WEB_SEARCH_PROVIDER,
            providers=cls.WEB_SEARCH_PROVIDERS,
        )

    @classmethod
    def _coerce_web_search_freshness(cls, value: Any) -> str:
        return web_settings_coercion.coerce_web_search_freshness(
            value,
            default_freshness=DEFAULT_WEB_SEARCH_FRESHNESS,
            freshness_values=cls.WEB_SEARCH_FRESHNESS,
        )

    @staticmethod
    def _coerce_text_list(value: Any, *, field: str, default: list[str] | None = None) -> list[str]:
        return web_settings_coercion.coerce_text_list(value, field=field, default=default)

    @classmethod
    def _normalize_searxng_engine_options(cls, engines: Any) -> list[dict[str, Any]]:
        return web_settings_coercion.normalize_searxng_engine_options(engines)

    @classmethod
    def _normalize_searxng_category_options(cls, categories: Any) -> list[dict[str, str]]:
        return web_settings_coercion.normalize_searxng_category_options(categories)

    @classmethod
    def _searxng_options_payload(cls, config_payload: dict[str, Any], *, url: str) -> dict[str, Any]:
        return web_settings_coercion.searxng_options_payload(config_payload, url=url)

    @classmethod
    def _fallback_searxng_options_payload(cls, *, url: str, warning: str) -> dict[str, Any]:
        return web_settings_coercion.fallback_searxng_options_payload(
            url=url,
            warning=warning,
            fallback_engines=cls.SEARXNG_FALLBACK_ENGINES,
            fallback_categories=cls.SEARXNG_FALLBACK_CATEGORIES,
        )

    @staticmethod
    def _searxng_config_url(searxng_url: str) -> str:
        return web_settings_coercion.searxng_config_url(searxng_url)

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
        return web_settings_reload.reload_agent_llm_from_config(self, payload, force=force, logger=logger)

    async def _reload_channels_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted channel settings to running adapters when possible."""
        return await web_settings_reload.reload_channels_from_config(self, payload, force=force, logger=logger)

    def _reload_schedule_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted scheduling settings to the running agent when possible."""
        return web_settings_reload.reload_schedule_from_config(self, payload, force=force, logger=logger)

    def _reload_permissions_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted tool permission settings to the running agent when possible."""
        return web_settings_reload.reload_permissions_from_config(self, payload, force=force, logger=logger)

    def _reload_media_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted media settings to the running agent when possible."""
        return web_settings_reload.reload_media_from_config(self, payload, force=force, logger=logger)

    def _reload_web_search_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted web search settings to running web tools when possible."""
        return web_settings_reload.reload_web_search_from_config(self, payload, force=force, logger=logger)

    def _reload_browser_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted browser settings to running browser tools when possible."""
        return web_settings_reload.reload_browser_from_config(self, payload, force=force, logger=logger)

    async def _reload_mcp_from_config(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Hot-apply persisted MCP settings to the running agent when possible."""
        return await web_settings_reload.reload_mcp_from_config(self, payload, force=force, logger=logger)

    @staticmethod
    async def _read_json_body(request: web.Request) -> dict[str, Any]:
        return await web_settings_support.read_json_body(request)

    @staticmethod
    def _raise_provider_settings_error(exc: ProviderSettingsError) -> None:
        web_settings_support.raise_provider_settings_error(exc)

    @staticmethod
    def _raise_channel_settings_error(exc: ChannelSettingsError) -> None:
        web_settings_support.raise_channel_settings_error(exc)

    @staticmethod
    def _raise_credential_store_error(exc: CredentialStoreError) -> None:
        web_settings_support.raise_credential_store_error(exc)

    @staticmethod
    def _raise_schedule_settings_error(exc: ScheduleSettingsError) -> None:
        web_settings_support.raise_schedule_settings_error(exc)

    @staticmethod
    def _raise_mcp_settings_error(exc: MCPSettingsError) -> None:
        web_settings_support.raise_mcp_settings_error(exc)

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
            return Config.load(self._get_config_path()).tools.cron.default_timezone or DEFAULT_CRON_TIMEZONE
        except Exception:
            return DEFAULT_CRON_TIMEZONE

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
            config.llm.pass_decoding_params = self._coerce_bool(
                body.get("pass_decoding_params"),
                field="pass_decoding_params",
                default=config.llm.pass_decoding_params,
            )
        if "semantic_contract_classifier_enabled" in body:
            config.agent.semantic_contract_classifier_enabled = self._coerce_bool(
                body.get("semantic_contract_classifier_enabled"),
                field="semantic_contract_classifier_enabled",
                default=config.agent.semantic_contract_classifier_enabled,
            )
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
                enabled=self._coerce_bool(body.get("enabled"), field="enabled", default=False),
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
        restart = self._coerce_bool(body.get("restart"), field="restart", default=True)
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

    async def _handle_settings_permissions(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"permissions": self._permissions_payload(config)})

    async def _handle_settings_harness_policy_preview(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"harness_policy_preview": self._harness_policy_preview_payload(config)})

    async def _handle_settings_permissions_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config_path = self._get_config_path()
        config = Config.load(config_path)
        permissions = config.tools.permissions
        before_permissions = self._permissions_payload(config)
        permissions.enabled = self._coerce_bool(body.get("enabled"), field="enabled", default=permissions.enabled)
        permissions.approval_mode = self._coerce_approval_mode(body.get("approval_mode", permissions.approval_mode))
        permissions.approval_timeout_seconds = self._coerce_float_range(
            body.get("approval_timeout_seconds"),
            field="approval_timeout_seconds",
            default=permissions.approval_timeout_seconds,
            minimum=1.0,
            maximum=86400.0,
        )
        permissions.allowed_tools = self._coerce_text_list(body.get("allowed_tools"), field="allowed_tools", default=permissions.allowed_tools)
        if not permissions.allowed_tools:
            permissions.allowed_tools = ["*"]
        permissions.denied_tools = self._coerce_text_list(body.get("denied_tools"), field="denied_tools", default=permissions.denied_tools)
        permissions.allowed_risk_levels = self._coerce_risk_level_list(
            body.get("allowed_risk_levels"),
            field="allowed_risk_levels",
            default=permissions.allowed_risk_levels,
        )
        permissions.denied_risk_levels = self._coerce_risk_level_list(
            body.get("denied_risk_levels"),
            field="denied_risk_levels",
            default=permissions.denied_risk_levels,
        )
        permissions.approval_required_tools = self._coerce_text_list(
            body.get("approval_required_tools"),
            field="approval_required_tools",
            default=permissions.approval_required_tools,
        )
        permissions.approval_required_risk_levels = self._coerce_risk_level_list(
            body.get("approval_required_risk_levels"),
            field="approval_required_risk_levels",
            default=permissions.approval_required_risk_levels,
        )
        permissions.profile_overrides = self._coerce_permission_profile_overrides(
            body.get("profile_overrides"),
            default=permissions.profile_overrides,
        )
        config.save(config_path)
        payload = {"permissions": self._permissions_payload(config), "restart_required": True}
        payload = self._reload_permissions_from_config(payload)
        payload["operation_audit"] = OperationAuditRecord(
            operation_type="settings.permissions.update",
            target="tools.permissions",
            before=before_permissions,
            after=payload["permissions"],
            validation={"runtime_reloaded": bool(payload.get("runtime_reloaded")), "restart_required": bool(payload.get("restart_required"))},
            rollback_available=True,
        ).to_metadata()
        return web.json_response(payload)

    async def _handle_settings_search(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"search": self._web_search_payload(config)})

    async def _handle_settings_search_searxng_options(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        search = config.tools.web_search
        searxng_url = self._coerce_optional_text(request.query.get("url"), default=search.searxng_url) or DEFAULT_SEARXNG_URL
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
            search.searxng_url = self._coerce_optional_text(body.get("searxng_url"), default="") or DEFAULT_SEARXNG_URL
        if "searxng_engines" in body:
            search.searxng_engines = self._coerce_text_list(body.get("searxng_engines"), field="searxng_engines", default=search.searxng_engines)
        if "searxng_categories" in body:
            search.searxng_categories = self._coerce_text_list(body.get("searxng_categories"), field="searxng_categories", default=search.searxng_categories)
        if "proxy" in body:
            search.proxy = self._coerce_optional_text(body.get("proxy"), default="") or None
        for field in ("jina_api_key",):
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
        if "launch_args" in body:
            browser.launch_args = self._coerce_optional_text(body.get("launch_args"), default="") or ""
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

    async def _handle_settings_browser_test(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config = Config.load(self._get_config_path())
        browser = config.tools.browser
        url = str(body.get("url") or "https://quotes.toscrape.com/js/").strip()
        blocked = _validate_navigation_url(url, allow_private_urls=bool(browser.allow_private_urls))
        if blocked:
            raise web.HTTPBadRequest(text=blocked)
        if not browser.enabled:
            diagnostic = self._with_browser_diagnostic(
                {
                    "ok": False,
                    "error": "Browser tools are disabled. Enable and save browser settings before running the manual test.",
                }
            )
            return web.json_response(
                {
                    "ok": False,
                    "url": url,
                    "backend": browser.backend,
                    "error": diagnostic["error"],
                    "diagnostic_code": diagnostic["diagnostic_code"],
                    "suggestion": diagnostic["suggestion"],
                    "browser": self._browser_payload(config),
                }
            )

        runtime = AgentBrowserRuntime(
            command_timeout=browser.command_timeout,
            session_timeout=browser.session_timeout,
            cdp_url=browser.cdp_url,
            launch_args=browser.launch_args,
            cloud_provider=cloud_provider_from_config(browser),
        )
        session_key = f"settings-test-{uuid4().hex[:8]}"
        open_result = self._with_browser_diagnostic(
            await runtime.run(session_key=session_key, command="open", args=[url], timeout=max(30, browser.command_timeout))
        )
        snapshot_result: dict[str, Any] | None = None
        if bool(open_result.get("success")):
            snapshot_result = self._with_browser_diagnostic(
                await runtime.run(session_key=session_key, command="snapshot", args=["-c"], timeout=browser.command_timeout)
            )
        ok = bool(open_result.get("success")) and bool((snapshot_result or {}).get("success"))
        diagnostic_source = snapshot_result if snapshot_result is not None and not snapshot_result.get("success") else open_result
        return web.json_response(
            {
                "ok": ok,
                "url": url,
                "backend": browser.backend,
                "diagnostic_code": "ok" if ok else diagnostic_source.get("diagnostic_code", "unknown"),
                "suggestion": "" if ok else diagnostic_source.get("suggestion", ""),
                "open": self._json_safe(open_result),
                "snapshot": self._json_safe(snapshot_result) if snapshot_result is not None else None,
                "browser": self._browser_payload(config),
            }
        )

    async def _handle_settings_browser_doctor(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        browser = config.tools.browser
        version_result = await self._run_browser_doctor_command(["--version"], timeout=10)
        doctor_result = await self._run_browser_doctor_command(["doctor"], timeout=30, launch_args=browser.launch_args)
        checks = [
            {"name": "version", "command": "agent-browser --version", **version_result},
            {"name": "doctor", "command": "agent-browser doctor", **doctor_result},
        ]
        return web.json_response(
            {
                "ok": all(bool(check.get("ok")) for check in checks),
                "browser": self._browser_payload(config),
                "runtime": self._browser_runtime_status(),
                "checks": checks,
            }
        )

    async def _handle_settings_browser_install(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        browser = config.tools.browser
        before = await self._run_browser_doctor_command(["doctor"], timeout=30, launch_args=browser.launch_args)
        if bool(before.get("ok")):
            return web.json_response(
                {
                    "ok": True,
                    "installed": False,
                    "already_installed": True,
                    "browser": self._browser_payload(config),
                    "runtime": self._browser_runtime_status(),
                    "before": before,
                    "install": None,
                    "after": before,
                }
            )

        install_result = await self._run_browser_install_command(timeout=300)
        after = await self._run_browser_doctor_command(["doctor"], timeout=30, launch_args=browser.launch_args)
        install_ok = bool(install_result.get("ok"))
        after_ok = bool(after.get("ok"))
        sandbox_only_after_install = (
            install_ok
            and not after_ok
            and after.get("diagnostic_code") == "sandbox_unavailable"
            and DEFAULT_BROWSER_LAUNCH_ARGS in str(browser.launch_args or "")
        )
        return web.json_response(
            {
                "ok": after_ok or sandbox_only_after_install,
                "installed": install_ok,
                "doctor_warning": sandbox_only_after_install,
                "already_installed": False,
                "browser": self._browser_payload(config),
                "runtime": self._browser_runtime_status(),
                "before": before,
                "install": install_result,
                "after": after,
            }
        )

    async def _handle_settings_log(self, request: web.Request) -> web.Response:
        config = Config.load(self._get_config_path())
        return web.json_response({"log": self._log_payload(config)})

    async def _handle_settings_log_update(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        config_path = self._get_config_path()
        config = Config.load(config_path)
        if "enabled" in body:
            config.log.enabled = self._coerce_bool(body.get("enabled"), field="enabled", default=config.log.enabled)
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
            config.log.log_system_prompt = self._coerce_bool(
                body.get("log_system_prompt"),
                field="log_system_prompt",
                default=config.log.log_system_prompt,
            )
        if "log_system_prompt_lines" in body:
            config.log.log_system_prompt_lines = self._coerce_positive_int(
                body.get("log_system_prompt_lines"),
                field="log_system_prompt_lines",
                default=config.log.log_system_prompt_lines,
                minimum=0,
            )
        if "log_reasoning_details" in body:
            config.log.log_reasoning_details = self._coerce_bool(
                body.get("log_reasoning_details"),
                field="log_reasoning_details",
                default=config.log.log_reasoning_details,
            )
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
        register_web_routes(self, ws_path=ws_path, health_path=health_path)

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
