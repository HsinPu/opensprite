"""Runtime tool parameter validation helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RequiredParamRule:
    key: str
    allow_empty: bool = False


FILE_TOOL_REQUIRED_PARAMS: dict[str, tuple[RequiredParamRule, ...]] = {
    "read_file": (RequiredParamRule("path"),),
    "write_file": (RequiredParamRule("path"), RequiredParamRule("content")),
    "edit_file": (
        RequiredParamRule("path"),
        RequiredParamRule("old_text"),
        RequiredParamRule("new_text", allow_empty=True),
    ),
}


def _describe_value(value: Any, *, allow_empty: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if allow_empty or value.strip():
            return None
        return "<empty-string>"
    if isinstance(value, list):
        return "<array>"
    if isinstance(value, dict):
        return "<object>"
    return f"<{type(value).__name__}>"


def format_param_preview(params: Any, max_chars: int = 240) -> str:
    """Return a compact JSON-ish preview for diagnostics."""
    try:
        text = json.dumps(params, ensure_ascii=False)
    except Exception:
        text = repr(params)
    text = text.replace("\n", "\\n")
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def validate_required_tool_params(name: str, params: Any) -> str | None:
    """Validate required params for high-risk filesystem tools."""
    rules = FILE_TOOL_REQUIRED_PARAMS.get(name)
    if not rules:
        return None

    if not isinstance(params, dict):
        return f"Error: Missing required argument(s) for {name}: {', '.join(rule.key for rule in rules)}."

    missing: list[str] = []
    received: list[str] = []
    for rule in rules:
        if rule.key not in params:
            missing.append(rule.key)
            continue
        value = params[rule.key]
        if not isinstance(value, str):
            missing.append(rule.key)
            detail = _describe_value(value, allow_empty=rule.allow_empty)
            if detail is not None:
                received.append(f"{rule.key}={detail}")
            continue
        if not rule.allow_empty and not value.strip():
            missing.append(rule.key)
            received.append(f"{rule.key}=<empty-string>")

    if not missing:
        return None

    message = f"Error: Missing required argument(s) for {name}: {', '.join(missing)}."
    if received:
        message += f" Received: {', '.join(received)}."
    return message
