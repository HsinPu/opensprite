"""Shared policy helpers for web source artifacts."""

from __future__ import annotations

FETCHED_WEB_SOURCE_ARTIFACT_TOOLS = frozenset({"web_fetch", "browser_navigate", "browser_snapshot"})
WEB_FETCH_SOURCE_RECORD_TOOL = "web_fetch"
WEB_RESEARCH_SOURCE_ARTIFACT_TOOL = "web_research"
WEB_SOURCE_ARTIFACT_KIND = "web_source"


def is_web_source_artifact_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == WEB_SOURCE_ARTIFACT_KIND


def is_fetched_web_source_artifact_tool(source_tool: str | None) -> bool:
    return str(source_tool or "").strip() in FETCHED_WEB_SOURCE_ARTIFACT_TOOLS


def is_web_research_source_artifact_tool(source_tool: str | None) -> bool:
    return str(source_tool or "").strip() == WEB_RESEARCH_SOURCE_ARTIFACT_TOOL


def is_web_fetch_source_record_tool(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() == WEB_FETCH_SOURCE_RECORD_TOOL
