"""Settings payload and LLM request helpers for the web adapter."""

from __future__ import annotations

import os
from typing import Any, Callable

from ..config import Config
from ..config.defaults import DEFAULT_LOG_ENABLED, DEFAULT_LOG_REASONING_DETAILS, DEFAULT_LOG_RETENTION_DAYS, DEFAULT_LOG_SYSTEM_PROMPT, DEFAULT_LOG_SYSTEM_PROMPT_LINES
from ..config.llm_presets import provider_profile_defaults, provider_request_options
from ..permission_constants import ALL_RISK_LEVELS_ORDER, APPROVAL_MODES_ORDER, DEFAULT_APPROVAL_MODE


def network_payload(config: Config, *, default_http_proxy: str, default_https_proxy: str, default_no_proxy: str) -> dict[str, Any]:
    network = getattr(config, "network", None)
    return {
        "http_proxy": str(getattr(network, "http_proxy", default_http_proxy) or default_http_proxy),
        "https_proxy": str(getattr(network, "https_proxy", default_https_proxy) or default_https_proxy),
        "no_proxy": str(getattr(network, "no_proxy", default_no_proxy) or default_no_proxy),
    }


def browser_payload(
    config: Config,
    *,
    default_backend: str,
    default_command_timeout: int,
    default_session_timeout: int,
    default_launch_args: str,
    backends: tuple[str, ...] | list[str],
    browser_cloud_status_fn: Callable[[Any], dict[str, Any]],
    browser_runtime_status_fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    browser = getattr(getattr(config, "tools", None), "browser", None)
    return {
        "enabled": bool(getattr(browser, "enabled", False)),
        "backend": str(getattr(browser, "backend", default_backend) or default_backend),
        "backends": list(backends),
        "command_timeout": int(getattr(browser, "command_timeout", default_command_timeout) or default_command_timeout),
        "session_timeout": int(getattr(browser, "session_timeout", default_session_timeout) or default_session_timeout),
        "cdp_url": str(getattr(browser, "cdp_url", "") or ""),
        "launch_args": str(getattr(browser, "launch_args", default_launch_args) or default_launch_args),
        "allow_private_urls": bool(getattr(browser, "allow_private_urls", False)),
        "cloud": browser_cloud_status_fn(browser),
        "runtime": browser_runtime_status_fn(),
    }


def web_search_payload(
    config: Config,
    *,
    default_provider: str,
    providers: tuple[str, ...] | list[str],
    default_freshness: str,
    freshness_values: tuple[str, ...] | list[str],
    default_max_results: int,
    default_duckduckgo_max_pages: int,
    default_searxng_max_pages: int,
    default_searxng_url: str,
    coerce_text_list_fn: Callable[..., list[str]],
) -> dict[str, Any]:
    search = getattr(getattr(config, "tools", None), "web_search", None)
    return {
        "provider": str(getattr(search, "provider", default_provider) or default_provider),
        "providers": list(providers),
        "freshness": str(getattr(search, "freshness", default_freshness) or default_freshness),
        "freshness_options": list(freshness_values),
        "max_results": int(getattr(search, "max_results", default_max_results) or default_max_results),
        "duckduckgo_max_pages": int(getattr(search, "duckduckgo_max_pages", default_duckduckgo_max_pages) or default_duckduckgo_max_pages),
        "searxng_max_pages": int(getattr(search, "searxng_max_pages", default_searxng_max_pages) or default_searxng_max_pages),
        "searxng_url": str(getattr(search, "searxng_url", default_searxng_url) or default_searxng_url),
        "searxng_engines": coerce_text_list_fn(getattr(search, "searxng_engines", []), field="searxng_engines", default=[]),
        "searxng_categories": coerce_text_list_fn(getattr(search, "searxng_categories", []), field="searxng_categories", default=[]),
        "proxy": str(getattr(search, "proxy", "") or ""),
        "jina_api_key_configured": bool(getattr(search, "jina_api_key", "") or os.environ.get("JINA_API_KEY", "")),
    }


def anthropic_reasoning_budget(effort: str | None) -> int:
    budgets = {"minimal": 4000, "low": 4000, "medium": 8000, "high": 16000, "xhigh": 32000}
    return budgets.get(str(effort or "medium").lower(), budgets["medium"])


def effective_llm_request_payload(config: Config) -> dict[str, Any]:
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
            budget = anthropic_reasoning_budget(active.reasoning_effort)
            reasoning_payload = {
                "thinking": {"type": "enabled", "budget_tokens": budget},
                "temperature": 1,
                "max_tokens": max(131072, budget + 4096),
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


def llm_payload(
    config: Config,
) -> dict[str, Any]:
    return {
        "effective_request": effective_llm_request_payload(config),
    }


def log_payload(config: Config, *, default_log_level: str, log_levels: tuple[str, ...] | list[str]) -> dict[str, Any]:
    log = getattr(config, "log", None)
    return {
        "enabled": bool(getattr(log, "enabled", DEFAULT_LOG_ENABLED)),
        "level": str(getattr(log, "level", default_log_level) or default_log_level).upper(),
        "retention_days": int(getattr(log, "retention_days", DEFAULT_LOG_RETENTION_DAYS) or DEFAULT_LOG_RETENTION_DAYS),
        "log_system_prompt": bool(getattr(log, "log_system_prompt", DEFAULT_LOG_SYSTEM_PROMPT)),
        "log_system_prompt_lines": int(getattr(log, "log_system_prompt_lines", DEFAULT_LOG_SYSTEM_PROMPT_LINES) or DEFAULT_LOG_SYSTEM_PROMPT_LINES),
        "log_reasoning_details": bool(getattr(log, "log_reasoning_details", DEFAULT_LOG_REASONING_DETAILS)),
        "levels": list(log_levels),
    }


def permissions_payload(
    config: Config,
    *,
    all_risk_levels: set[str] | frozenset[str],
    approval_modes: set[str] | frozenset[str],
) -> dict[str, Any]:
    permissions = getattr(getattr(config, "tools", None), "permissions", None)
    profile_overrides = getattr(permissions, "profile_overrides", {}) or {}
    risk_options = _ordered_subset(all_risk_levels, ALL_RISK_LEVELS_ORDER)
    approval_options = _ordered_subset(approval_modes, APPROVAL_MODES_ORDER)
    return {
        "enabled": bool(getattr(permissions, "enabled", True)),
        "approval_mode": getattr(permissions, "approval_mode", DEFAULT_APPROVAL_MODE) or DEFAULT_APPROVAL_MODE,
        "approval_timeout_seconds": float(getattr(permissions, "approval_timeout_seconds", 300.0) or 300.0),
        "allowed_tools": list(getattr(permissions, "allowed_tools", ["*"]) or ["*"]),
        "denied_tools": list(getattr(permissions, "denied_tools", []) or []),
        "allowed_risk_levels": list(getattr(permissions, "allowed_risk_levels", risk_options) or []),
        "denied_risk_levels": list(getattr(permissions, "denied_risk_levels", []) or []),
        "approval_required_tools": list(getattr(permissions, "approval_required_tools", []) or []),
        "approval_required_risk_levels": list(getattr(permissions, "approval_required_risk_levels", []) or []),
        "profile_overrides": {
            profile: override.model_dump(by_alias=True) if hasattr(override, "model_dump") else dict(override)
            for profile, override in profile_overrides.items()
        },
        "risk_level_options": risk_options,
        "approval_mode_options": approval_options,
    }


def _ordered_subset(values: set[str] | frozenset[str], canonical_order: tuple[str, ...]) -> list[str]:
    value_set = set(values)
    ordered = [value for value in canonical_order if value in value_set]
    ordered.extend(sorted(value_set - set(canonical_order)))
    return ordered
