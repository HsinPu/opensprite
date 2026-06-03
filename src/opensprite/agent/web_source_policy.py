"""Shared policy helpers for web source artifacts."""

from __future__ import annotations

FETCHED_WEB_SOURCE_ARTIFACT_TOOLS = frozenset({"web_fetch", "browser_navigate", "browser_snapshot"})
WEB_DISCOVERY_TOOLS = frozenset({"web_search", "web_research"})
WEB_SOURCE_ARTIFACT_TOOLS = frozenset(
    {
        "web_search",
        "web_fetch",
        "web_research",
        "browser_navigate",
        "browser_snapshot",
    }
)
WEB_SOURCE_EVIDENCE_TOOLS = frozenset({"web_search", "web_fetch", "web_research"})
WEB_FETCH_SOURCE_RECORD_TOOL = "web_fetch"
WEB_RESEARCH_SOURCE_ARTIFACT_TOOL = "web_research"
WEB_RESEARCH_TASK_TYPE = "web_research"
WEB_RESEARCH_TOOL_GROUP = "web_research"
WEB_SOURCE_ARTIFACT_KIND = "web_source"
SOURCE_ARTIFACT_CRITERION_KIND = "source_artifact"
SOURCE_DETAIL_CRITERION_KIND = "source_detail"
SOURCE_REFERENCE_CRITERION_KIND = "source_reference"
SOURCE_ACCEPTANCE_CRITERION_KINDS = frozenset(
    {
        SOURCE_ARTIFACT_CRITERION_KIND,
        SOURCE_DETAIL_CRITERION_KIND,
        SOURCE_REFERENCE_CRITERION_KIND,
    }
)


def is_web_source_artifact_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == WEB_SOURCE_ARTIFACT_KIND


def is_fetched_web_source_artifact_tool(source_tool: str | None) -> bool:
    return str(source_tool or "").strip() in FETCHED_WEB_SOURCE_ARTIFACT_TOOLS


def is_web_source_evidence_tool(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in WEB_SOURCE_EVIDENCE_TOOLS


def is_web_discovery_tool(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in WEB_DISCOVERY_TOOLS


def is_web_research_source_artifact_tool(source_tool: str | None) -> bool:
    return str(source_tool or "").strip() == WEB_RESEARCH_SOURCE_ARTIFACT_TOOL


def is_web_research_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == WEB_RESEARCH_TASK_TYPE


def is_web_research_tool_group(tool_group: str | None) -> bool:
    return str(tool_group or "").strip() == WEB_RESEARCH_TOOL_GROUP


def is_source_acceptance_criterion_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in SOURCE_ACCEPTANCE_CRITERION_KINDS


def is_web_fetch_source_record_tool(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() == WEB_FETCH_SOURCE_RECORD_TOOL
