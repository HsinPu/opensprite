"""Shared classification for tool result strings."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


_ERROR_STATES = {"error", "failed", "cancelled"}


@dataclass(frozen=True)
class ToolResultStatus:
    ok: bool
    error: str = ""
    error_type: str = ""
    category: str = ""
    status_code: int | None = None
    repeated_error_key: str | None = None
    invalid_arguments: bool = False

    def error_metadata(self) -> dict[str, Any]:
        if not self.error:
            return {}
        metadata: dict[str, Any] = {
            "error": self.error,
            "error_type": self.error_type or "ToolError",
        }
        if self.status_code is not None:
            metadata["status_code"] = self.status_code
        return metadata


def tool_error_result(
    error: str,
    *,
    error_type: str = "ToolError",
    category: str = "",
    repeated_error_key: str | None = None,
    invalid_arguments: bool = False,
) -> str:
    """Return a structured OpenSprite-owned tool error payload."""
    payload: dict[str, Any] = {
        "ok": False,
        "error": str(error or "").strip(),
        "error_type": error_type or "ToolError",
    }
    if category:
        payload["category"] = category
    if repeated_error_key:
        payload["repeated_error_key"] = repeated_error_key
    if invalid_arguments:
        payload["invalid_arguments"] = True
    return json.dumps(payload, ensure_ascii=False)


def classify_tool_result_status(result_text: str, *, state: str | None = None) -> ToolResultStatus:
    """Return normalized status for a structured or OpenSprite-owned tool result."""
    text = str(result_text or "")
    stripped = text.lstrip()
    state_text = str(state or "").strip().lower()
    forced_error = state_text in _ERROR_STATES

    batch_failure = _batch_result_failure(stripped)
    if batch_failure is not None:
        if batch_failure == 0 and not forced_error:
            return ToolResultStatus(ok=True)
        return _failed_status(_first_line(stripped), error_type="ToolFailure", fallback=stripped)

    payload = _json_object(stripped)
    if payload is not None:
        if payload.get("ok") is False:
            return _failed_status(
                payload.get("error"),
                error_type=str(payload.get("error_type") or "ToolError"),
                category=str(payload.get("category") or ""),
                fallback=stripped,
                repeated_error_key=_optional_text(payload.get("repeated_error_key")),
                invalid_arguments=bool(payload.get("invalid_arguments")),
            )
        error = payload.get("error")
        if error is not None and str(error).strip():
            return _failed_status(
                error,
                error_type=str(payload.get("error_type") or "ToolError"),
                category=str(payload.get("category") or ""),
                fallback=stripped,
            )
        return ToolResultStatus(ok=not forced_error)

    invalid_prefix = "Error: Invalid arguments for "
    if stripped.startswith(invalid_prefix):
        return _failed_status(
            stripped.removeprefix("Error:").strip(),
            error_type="ToolError",
            fallback=stripped,
            repeated_error_key=stripped,
            invalid_arguments=True,
        )

    if stripped.startswith("Error executing "):
        _, _, detail = stripped.partition(":")
        return _failed_status(detail, error_type="ToolExecutionError", fallback=stripped)

    if stripped.startswith("Error:"):
        return _failed_status(stripped.removeprefix("Error:").strip(), error_type="ToolError", fallback=stripped)

    lowered = stripped.lower()
    if lowered.startswith("(mcp tool call failed") or lowered.startswith("(mcp tool call timed out"):
        return _failed_status(stripped, error_type="McpToolError", fallback=stripped)

    return ToolResultStatus(ok=not forced_error)


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(str(value or "").lstrip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _batch_result_failure(text: str) -> int | None:
    first_line = _first_line(text)
    match = re.match(r"Batch completed: \d+ call\(s\), (\d+) failed\.", first_line)
    return int(match.group(1)) if match else None


def _first_line(text: str) -> str:
    return (text or "").splitlines()[0] if text else ""


def _failed_status(
    error: Any,
    *,
    error_type: str,
    fallback: str,
    category: str = "",
    repeated_error_key: str | None = None,
    invalid_arguments: bool = False,
) -> ToolResultStatus:
    error_text = str(error or "").strip() or fallback
    return ToolResultStatus(
        ok=False,
        error=error_text,
        error_type=error_type,
        category=category,
        status_code=_status_code(error_text),
        repeated_error_key=repeated_error_key,
        invalid_arguments=invalid_arguments,
    )


def _status_code(error: str) -> int | None:
    match = re.search(r"\b(?:HTTP(?:\s+Error)?|status(?:\s+code)?)[:\s]+(\d{3})\b", error, re.IGNORECASE)
    return int(match.group(1)) if match else None
