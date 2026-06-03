"""Shared chat-history retrieval tool policy helpers."""

from __future__ import annotations


HISTORY_SEARCH_TOOL_NAME = "search_history"


def is_history_retrieval_tool_name(tool_name: str | None) -> bool:
    """Return whether a tool name represents chat-history retrieval."""
    return str(tool_name or "").strip() == HISTORY_SEARCH_TOOL_NAME
