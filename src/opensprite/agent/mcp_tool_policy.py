"""Shared MCP tool-name classification helpers."""

from __future__ import annotations

from collections.abc import Iterable

from ..tool_names import (
    DELEGATE_MANY_TOOL_NAME,
    DELEGATE_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
    RUN_WORKFLOW_TOOL_NAME,
)


MCP_TOOL_NAME_PREFIX = "mcp_"
PROGRESS_NOTICE_TOOL_NAMES = frozenset(
    {
        READ_SKILL_TOOL_NAME,
        DELEGATE_TOOL_NAME,
        DELEGATE_MANY_TOOL_NAME,
        RUN_WORKFLOW_TOOL_NAME,
    }
)


def is_mcp_tool_name(tool_name: str | None) -> bool:
    return str(tool_name or "").startswith(MCP_TOOL_NAME_PREFIX)


def mcp_tool_display_name(tool_name: str | None) -> str:
    text = str(tool_name or "")
    return text[len(MCP_TOOL_NAME_PREFIX) :] if is_mcp_tool_name(text) else text


def mcp_tool_names(tool_names: Iterable[str]) -> list[str]:
    return sorted(name for name in tool_names if is_mcp_tool_name(name))


def tool_warrants_progress_notice(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in PROGRESS_NOTICE_TOOL_NAMES or is_mcp_tool_name(tool_name)
