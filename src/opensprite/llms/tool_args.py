"""Helpers for parsing tool-call arguments from provider responses."""

from __future__ import annotations

import json
from typing import Any

from ..utils.log import logger


def _preview_value(value: Any, max_chars: int = 240) -> str:
    """Build a bounded preview for diagnostics."""
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:
        text = repr(value)
    text = text.replace("\n", "\\n")
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def parse_tool_arguments(
    raw_args: Any,
    *,
    provider_name: str,
    tool_name: str,
) -> dict[str, Any]:
    """Parse provider tool arguments while keeping parse failures observable."""
    if isinstance(raw_args, dict):
        return raw_args

    if raw_args is None:
        return {}

    if isinstance(raw_args, str):
        trimmed = raw_args.strip()
        if not trimmed:
            return {}
        try:
            parsed = json.loads(trimmed)
        except Exception as exc:
            logger.warning(
                "{} tool args parse failed: tool={} raw_type=str raw_preview={} error={}",
                provider_name,
                tool_name,
                _preview_value(raw_args),
                exc,
            )
            return {}
        if isinstance(parsed, dict):
            return parsed
        logger.warning(
            "{} tool args parse produced non-object: tool={} parsed_type={} raw_preview={}",
            provider_name,
            tool_name,
            type(parsed).__name__,
            _preview_value(raw_args),
        )
        return {}

    logger.warning(
        "{} tool args unexpected type: tool={} raw_type={} raw_preview={}",
        provider_name,
        tool_name,
        type(raw_args).__name__,
        _preview_value(raw_args),
    )
    return {}
