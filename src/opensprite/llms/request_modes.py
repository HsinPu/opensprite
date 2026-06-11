"""Shared request-mode policy for internal LLM calls."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class LLMRequestMode(str, Enum):
    """High-level reason a provider request is being made."""

    MAIN_CHAT = "main_chat"
    JSON_PLANNING = "json_planning"
    COMPLETION_JUDGE = "completion_judge"


JSON_PLANNING_MIN_OUTPUT_TOKENS = 1200
_JSON_ONLY_MODES = {LLMRequestMode.JSON_PLANNING, LLMRequestMode.COMPLETION_JUDGE}


def normalize_request_mode(mode: LLMRequestMode | str | None) -> str:
    """Return a stable request-mode label for logging and provider kwargs."""
    if isinstance(mode, LLMRequestMode):
        return mode.value
    return str(mode or LLMRequestMode.MAIN_CHAT.value).strip() or LLMRequestMode.MAIN_CHAT.value


def request_kwargs_for_mode(
    base_kwargs: Mapping[str, Any] | None,
    mode: LLMRequestMode | str | None,
    *,
    min_output_tokens: int = JSON_PLANNING_MIN_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Apply common request policy for an internal request mode."""
    kwargs = dict(base_kwargs or {})
    normalized = normalize_request_mode(mode)
    kwargs["request_mode"] = normalized

    if normalized in {item.value for item in _JSON_ONLY_MODES}:
        kwargs["max_tokens"] = _coerce_min_tokens(kwargs.get("max_tokens"), min_output_tokens)

    return kwargs


def _coerce_min_tokens(value: Any, minimum: int) -> int:
    try:
        return max(int(value), int(minimum))
    except (TypeError, ValueError):
        return int(minimum)
