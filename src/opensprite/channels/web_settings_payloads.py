"""Settings payload and LLM request helpers for the web adapter."""

from __future__ import annotations

import os
from typing import Any, Callable

from aiohttp import web

from ..config import Config
from ..config.defaults import DEFAULT_LOG_ENABLED, DEFAULT_LOG_REASONING_DETAILS, DEFAULT_LOG_RETENTION_DAYS, DEFAULT_LOG_SYSTEM_PROMPT, DEFAULT_LOG_SYSTEM_PROMPT_LINES
from ..config.llm_presets import provider_profile_defaults, provider_request_options


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


def llm_decoding_payload(config: Config) -> dict[str, Any]:
    llm = config.llm
    return {
        "temperature": llm.temperature,
        "max_tokens": llm.max_tokens,
        "top_p": llm.top_p,
        "frequency_penalty": llm.frequency_penalty,
        "presence_penalty": llm.presence_penalty,
    }


def llm_decoding_mode(config: Config, *, presets: dict[str, dict[str, Any]]) -> str:
    if not config.llm.pass_decoding_params:
        return "provider_default"
    decoding = llm_decoding_payload(config)
    for mode, preset in presets.items():
        if all(decoding.get(key) == value for key, value in preset.items()):
            return mode
    return "custom"


def apply_llm_decoding_preset(config: Config, mode: str, *, presets: dict[str, dict[str, Any]], mode_order: tuple[str, ...] | list[str]) -> None:
    if mode == "provider_default":
        config.llm.pass_decoding_params = False
        return
    preset = presets.get(mode)
    if preset is None:
        raise web.HTTPBadRequest(text=f"decoding_mode must be one of: {', '.join(mode_order)}")
    config.llm.pass_decoding_params = True
    for key, value in preset.items():
        setattr(config.llm, key, value)


def coerce_llm_float(value: Any, *, field: str, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"{field} must be a number") from exc
    if minimum is not None and number < minimum:
        raise web.HTTPBadRequest(text=f"{field} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise web.HTTPBadRequest(text=f"{field} must be at most {maximum}")
    return number


def apply_custom_llm_decoding(
    config: Config,
    decoding: dict[str, Any],
    *,
    coerce_positive_int_fn: Callable[..., int],
) -> None:
    config.llm.pass_decoding_params = True
    if "temperature" in decoding:
        config.llm.temperature = coerce_llm_float(decoding["temperature"], field="temperature")
    if "max_tokens" in decoding:
        config.llm.max_tokens = coerce_positive_int_fn(decoding["max_tokens"], field="max_tokens", default=config.llm.max_tokens, minimum=1, maximum=1_000_000)
    if "top_p" in decoding:
        config.llm.top_p = coerce_llm_float(decoding["top_p"], field="top_p", minimum=0.0, maximum=1.0)
    if "frequency_penalty" in decoding:
        config.llm.frequency_penalty = coerce_llm_float(decoding["frequency_penalty"], field="frequency_penalty", minimum=-2.0, maximum=2.0)
    if "presence_penalty" in decoding:
        config.llm.presence_penalty = coerce_llm_float(decoding["presence_penalty"], field="presence_penalty", minimum=-2.0, maximum=2.0)


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
    decoding = llm_decoding_payload(config)
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
            budget = anthropic_reasoning_budget(active.reasoning_effort)
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
        "decoding": {"status": "sent" if llm.pass_decoding_params else "omitted", "params": sent_decoding},
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
    *,
    mode_order: tuple[str, ...] | list[str],
    presets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "decoding_mode": llm_decoding_mode(config, presets=presets),
        "decoding_modes": list(mode_order),
        "pass_decoding_params": bool(config.llm.pass_decoding_params),
        "decoding": llm_decoding_payload(config),
        "effective_request": effective_llm_request_payload(config),
        "semantic_contract_classifier_enabled": bool(config.agent.semantic_contract_classifier_enabled),
        "semantic_contract_classifier_confidence_threshold": float(config.agent.semantic_contract_classifier_confidence_threshold),
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
    return {
        "enabled": bool(getattr(permissions, "enabled", True)),
        "approval_mode": getattr(permissions, "approval_mode", "auto") or "auto",
        "approval_timeout_seconds": float(getattr(permissions, "approval_timeout_seconds", 300.0) or 300.0),
        "allowed_tools": list(getattr(permissions, "allowed_tools", ["*"]) or ["*"]),
        "denied_tools": list(getattr(permissions, "denied_tools", []) or []),
        "allowed_risk_levels": list(getattr(permissions, "allowed_risk_levels", sorted(all_risk_levels)) or []),
        "denied_risk_levels": list(getattr(permissions, "denied_risk_levels", []) or []),
        "approval_required_tools": list(getattr(permissions, "approval_required_tools", []) or []),
        "approval_required_risk_levels": list(getattr(permissions, "approval_required_risk_levels", []) or []),
        "profile_overrides": {
            profile: override.model_dump(by_alias=True) if hasattr(override, "model_dump") else dict(override)
            for profile, override in profile_overrides.items()
        },
        "risk_level_options": sorted(all_risk_levels),
        "approval_mode_options": sorted(approval_modes),
    }
