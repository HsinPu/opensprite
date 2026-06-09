"""Settings payload and LLM request helpers for the web adapter."""

from __future__ import annotations

import os
from typing import Any, Callable

from ..config import Config
from ..config.defaults import DEFAULT_LOG_ENABLED, DEFAULT_LOG_REASONING_DETAILS, DEFAULT_LOG_RETENTION_DAYS, DEFAULT_LOG_SYSTEM_PROMPT, DEFAULT_LOG_SYSTEM_PROMPT_LINES


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
