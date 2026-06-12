"""Token and usage accounting helpers for agent execution."""

from __future__ import annotations

import json
from typing import Any

from ...llms import ChatMessage, LLMProvider
from ...utils import count_messages_tokens, count_text_tokens


def get_token_model(provider: LLMProvider | None) -> str | None:
    """Best-effort model name lookup for local token estimates."""
    get_default_model = getattr(provider, "get_default_model", None)
    if not callable(get_default_model):
        return None
    try:
        return str(get_default_model() or "") or None
    except Exception:
        return None


def estimate_tool_schema_tokens(tools: list[dict[str, Any]] | None, *, model: str | None) -> int:
    if not tools:
        return 0
    try:
        tool_schema_text = json.dumps(tools, ensure_ascii=False, sort_keys=True)
    except Exception:
        tool_schema_text = str(tools)
    return count_text_tokens(tool_schema_text, model=model)


def estimate_request_tokens(
    chat_messages: list[ChatMessage],
    tools: list[dict[str, Any]] | None,
    *,
    provider: LLMProvider | None,
) -> tuple[int, int, int]:
    model = get_token_model(provider)
    message_tokens = count_messages_tokens(chat_messages, model=model)
    tool_schema_tokens = estimate_tool_schema_tokens(tools, model=model)
    return message_tokens + tool_schema_tokens, message_tokens, tool_schema_tokens


def usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def reasoning_tokens(usage: dict[str, Any]) -> int | None:
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return None
    return usage_int(details, "reasoning_tokens")


def cached_tokens(usage: dict[str, Any]) -> int | None:
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        return None
    return usage_int(details, "cached_tokens")
