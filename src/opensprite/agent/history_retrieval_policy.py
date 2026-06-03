"""Shared chat-history retrieval tool policy helpers."""

from __future__ import annotations

from typing import Any


HISTORY_SEARCH_TOOL_NAME = "search_history"
HISTORY_RESULT_COUNT_METADATA_KEYS = ("result_count", "hit_count", "hits", "count")


def is_history_retrieval_tool_name(tool_name: str | None) -> bool:
    """Return whether a tool name represents chat-history retrieval."""
    return str(tool_name or "").strip() == HISTORY_SEARCH_TOOL_NAME


def history_retrieval_metadata_reports_empty(metadata: dict[str, Any] | None) -> bool:
    """Return whether search-history metadata explicitly reports zero matches."""
    if not isinstance(metadata, dict):
        return False
    saw_count_field = False
    for key in HISTORY_RESULT_COUNT_METADATA_KEYS:
        if key not in metadata:
            continue
        value = metadata.get(key)
        if _metadata_value_has_results(value):
            return False
        saw_count_field = True
    return saw_count_field


def history_retrieval_metadata_has_results(metadata: dict[str, Any] | None) -> bool:
    """Return whether search-history metadata explicitly reports one or more matches."""
    if not isinstance(metadata, dict):
        return False
    return any(_metadata_value_has_results(metadata.get(key)) for key in HISTORY_RESULT_COUNT_METADATA_KEYS if key in metadata)


def _metadata_value_has_results(value: object) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    return _coerce_int(value, default=0) > 0


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(float(stripped))
            except ValueError:
                return default
    return default
