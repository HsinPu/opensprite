"""Safe provider request logging shared by LLM adapters."""

from __future__ import annotations

import json
from typing import Any

from ..utils.log import logger
from .request_modes import normalize_request_mode


def request_param_log_fields(
    params: dict[str, Any],
    *,
    input_key: str | None = None,
    request_mode: str | None = None,
) -> dict[str, Any]:
    """Return log-safe request metadata without prompt, tool schema, or auth data."""
    resolved_input_key = input_key or ("input" if "input" in params else "messages")
    return {
        "mode": normalize_request_mode(request_mode),
        "model": str(params.get("model") or "-"),
        "messages": _safe_len(params.get(resolved_input_key)),
        "tools": _safe_len(params.get("tools")),
        "tool_choice": _tool_choice_summary(params.get("tool_choice")) if "tool_choice" in params else "-",
        "stream": bool(params.get("stream", False)),
        "max_tokens": _max_tokens(params),
        "reasoning": _reasoning_summary(params),
    }


def log_llm_request_params(
    provider_name: str,
    params: dict[str, Any],
    *,
    input_key: str | None = None,
    request_mode: str | None = None,
) -> None:
    """Log provider request metadata using one consistent, sanitized shape."""
    fields = request_param_log_fields(params, input_key=input_key, request_mode=request_mode)
    logger.info(
        "{} request params | mode={} model={} messages={} tools={} tool_choice={} stream={} max_tokens={} reasoning={}",
        provider_name,
        fields["mode"],
        fields["model"],
        fields["messages"],
        fields["tools"],
        fields["tool_choice"],
        fields["stream"],
        fields["max_tokens"],
        fields["reasoning"],
    )


def _safe_len(value: Any) -> int:
    try:
        return len(value or [])
    except Exception:
        return 0


def _max_tokens(params: dict[str, Any]) -> Any:
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        if params.get(key) is not None:
            return params[key]
    return "-"


def _reasoning_summary(params: dict[str, Any]) -> str:
    extra_body = params.get("extra_body")
    if isinstance(extra_body, dict) and extra_body.get("reasoning") is not None:
        return _safe_json(extra_body.get("reasoning"))
    if params.get("reasoning") is not None:
        return _safe_json(params.get("reasoning"))
    if params.get("reasoning_effort") is not None:
        return _safe_json({"effort": params.get("reasoning_effort")})
    return "-"


def _tool_choice_summary(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _safe_json(value)


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)
