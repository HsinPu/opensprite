"""Planner capability catalog and planning-mode tool restrictions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...context.message_history import HISTORY_SEARCH_TOOL_NAME
from ...tool_names import (
    ANALYZE_IMAGE_TOOL_NAME,
    ANALYZE_VIDEO_TOOL_NAME,
    APPLY_PATCH_TOOL_NAME,
    BATCH_TOOL_NAME,
    CODE_NAVIGATION_TOOL_NAME,
    CONFIGURE_MCP_TOOL_NAME,
    CRON_TOOL_NAME,
    DELEGATE_MANY_TOOL_NAME,
    DELEGATE_TOOL_NAME,
    EDIT_FILE_TOOL_NAME,
    EXEC_TOOL_NAME,
    EXECUTION_TOOL_NAMES,
    GLOB_FILES_TOOL_NAME,
    GREP_FILES_TOOL_NAME,
    LIST_DIR_TOOL_NAME,
    LIST_RUN_FILE_CHANGES_TOOL_NAME,
    OCR_IMAGE_TOOL_NAME,
    PROCESS_TOOL_NAME,
    PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
    RUN_WORKFLOW_TOOL_NAME,
    TRANSCRIBE_AUDIO_TOOL_NAME,
    WEB_FETCH_TOOL_NAME,
    WEB_RESEARCH_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    WORKSPACE_DISCOVERY_TOOL_NAMES,
    WORKSPACE_WRITE_TOOL_NAMES,
    WRITE_FILE_TOOL_NAME,
)
from ...tools.evidence import (
    VERIFICATION_TOOL_NAME,
    WEB_RESEARCH_TASK_TYPE,
    WEB_SOURCE_ARTIFACT_TOOLS,
    WEB_SOURCE_EVIDENCE_TOOLS,
)
from ...tools.registry import ToolRegistry
from .capabilities import (
    ANALYSIS_TASK_TYPE,
    CODE_CHANGE_TASK_TYPE,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    MEDIA_EXTRACTION_TASK_TYPE,
    OPERATIONS_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    WORKSPACE_READ_TASK_TYPE,
    is_planning_task_type,
)

_DEFAULT_PLANNER_TOOL_NAMES = frozenset(
    {
        ANALYZE_IMAGE_TOOL_NAME,
        ANALYZE_VIDEO_TOOL_NAME,
        APPLY_PATCH_TOOL_NAME,
        BATCH_TOOL_NAME,
        CODE_NAVIGATION_TOOL_NAME,
        CONFIGURE_MCP_TOOL_NAME,
        CRON_TOOL_NAME,
        DELEGATE_MANY_TOOL_NAME,
        DELEGATE_TOOL_NAME,
        EDIT_FILE_TOOL_NAME,
        EXEC_TOOL_NAME,
        GLOB_FILES_TOOL_NAME,
        GREP_FILES_TOOL_NAME,
        HISTORY_SEARCH_TOOL_NAME,
        LIST_DIR_TOOL_NAME,
        LIST_RUN_FILE_CHANGES_TOOL_NAME,
        OCR_IMAGE_TOOL_NAME,
        PROCESS_TOOL_NAME,
        PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
        READ_FILE_TOOL_NAME,
        READ_SKILL_TOOL_NAME,
        RUN_WORKFLOW_TOOL_NAME,
        TRANSCRIBE_AUDIO_TOOL_NAME,
        VERIFICATION_TOOL_NAME,
        WEB_FETCH_TOOL_NAME,
        WEB_RESEARCH_TOOL_NAME,
        WEB_SEARCH_TOOL_NAME,
        WRITE_FILE_TOOL_NAME,
    }
)
_UNGROUPED_TOOL_PREFIX = "tool:"
_MAX_TOOL_DESCRIPTION_CHARS = 220
PLANNING_ALLOWED_TOOLS = frozenset(
    {
        READ_FILE_TOOL_NAME,
        LIST_DIR_TOOL_NAME,
        GLOB_FILES_TOOL_NAME,
        GREP_FILES_TOOL_NAME,
        BATCH_TOOL_NAME,
        READ_SKILL_TOOL_NAME,
        HISTORY_SEARCH_TOOL_NAME,
        LIST_RUN_FILE_CHANGES_TOOL_NAME,
        PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
        *WEB_SOURCE_EVIDENCE_TOOLS,
        ANALYZE_IMAGE_TOOL_NAME,
        OCR_IMAGE_TOOL_NAME,
        TRANSCRIBE_AUDIO_TOOL_NAME,
        ANALYZE_VIDEO_TOOL_NAME,
    }
)


@dataclass(frozen=True)
class PlannerCapability:
    """One planner-visible capability derived from runtime tools."""

    id: str
    task_type: str
    tools: tuple[str, ...]
    tool_summaries: tuple[dict[str, str], ...] = ()

    def to_prompt_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "tools": list(self.tool_summaries) or [{"name": name} for name in self.tools],
        }


@dataclass(frozen=True)
class PlannerCapabilityCatalog:
    """Planner-facing view of capabilities available in the current runtime."""

    capabilities: tuple[PlannerCapability, ...]

    @property
    def task_types(self) -> tuple[str, ...]:
        values = [PURE_ANSWER_TASK_TYPE, PLANNING_TASK_TYPE, GENERIC_TASK_TYPE, ANALYSIS_TASK_TYPE]
        for capability in self.capabilities:
            if capability.task_type not in values:
                values.append(capability.task_type)
        return tuple(values)

    @property
    def available_tool_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for capability in self.capabilities:
            for tool_name in capability.tools:
                if tool_name not in names:
                    names.append(tool_name)
        return tuple(names)

    @property
    def available_tools(self) -> tuple[dict[str, Any], ...]:
        tools_by_name: dict[str, dict[str, Any]] = {}
        for capability in self.capabilities:
            summaries = capability.tool_summaries or tuple({"name": name} for name in capability.tools)
            for summary in summaries:
                name = str(summary.get("name") or "").strip()
                if not name:
                    continue
                item = tools_by_name.setdefault(
                    name,
                    {
                        "name": name,
                        "task_type": capability.task_type,
                    },
                )
                if summary.get("description"):
                    item["description"] = summary["description"]
        return tuple(tools_by_name[name] for name in tools_by_name)

    def to_prompt_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "available_task_types": list(self.task_types),
            "available_tools": list(self.available_tools),
        }


@dataclass(frozen=True)
class PlanningModeState:
    """Resolved planning-mode state for one user turn."""

    enabled: bool = False
    overlay: str = ""
    tool_registry: ToolRegistry | None = None


def build_planner_capability_catalog(tool_registry: ToolRegistry | None = None) -> PlannerCapabilityCatalog:
    """Build a planner capability catalog from current runtime tools."""
    if tool_registry is None:
        return _catalog_from_static_tools()
    available_tools = _available_tools(tool_registry)
    return PlannerCapabilityCatalog(tuple(_capability_from_tool(tool) for tool in available_tools))


def build_planning_mode_overlay() -> str:
    """Return the temporary system overlay for contract-selected planning turns."""
    return """# Planning Mode

The task contract selected read-only planning mode for this turn.

- You MUST NOT edit files, apply patches, write files, run exec/process/verify, change configuration, save memory, schedule jobs, delegate subagents, or cause external side effects.
- Use only inspection, retrieval, and research actions to understand the current state.
- Focus on clarifying scope, identifying risks, and producing a concrete implementation plan grounded in real workspace evidence.
- Ask at most one short blocking question only when a missing decision prevents a useful plan.
- Your response should end with either a concise implementation plan or one concise blocker question.

This planning-mode restriction overrides normal workspace autonomy for this turn.
"""


def resolve_planning_mode(
    *,
    base_registry: ToolRegistry | None = None,
    task_contract: Any = None,
) -> PlanningModeState:
    """Resolve the full planning-mode state for one user turn."""
    if not _contract_requests_planning_mode(task_contract):
        return PlanningModeState()
    return PlanningModeState(
        enabled=True,
        overlay=build_planning_mode_overlay(),
        tool_registry=(
            build_planning_mode_tool_registry(base_registry)
            if base_registry is not None
            else None
        ),
    )


def build_planning_mode_tool_registry(base_registry: ToolRegistry) -> ToolRegistry:
    """Return a read-only registry used for plan-only turns."""
    from ...tools.selection import ToolSelectionResolver

    resolution = ToolSelectionResolver().resolve_overlay(
        base_registry,
        include_names=PLANNING_ALLOWED_TOOLS,
        metadata_kind="planning_mode",
    )
    return resolution.registry


def _catalog_from_static_tools() -> PlannerCapabilityCatalog:
    capabilities = [
        PlannerCapability(
            id=tool_name,
            task_type=_task_type_for_tool_name(tool_name),
            tools=(tool_name,),
            tool_summaries=({"name": tool_name},),
        )
        for tool_name in sorted(_DEFAULT_PLANNER_TOOL_NAMES)
    ]
    return PlannerCapabilityCatalog(tuple(capabilities))


def _available_tools(tool_registry: ToolRegistry) -> list[Any]:
    exposed_names = set(tool_registry.tool_names)
    return [
        tool
        for tool in tool_registry.registered_tools()
        if tool.name in exposed_names
    ]


def _capability_from_tool(tool: Any) -> PlannerCapability:
    tool_name = str(getattr(tool, "name", "") or "").strip()
    return PlannerCapability(
        id=tool_name or _UNGROUPED_TOOL_PREFIX,
        task_type=_task_type_for_tool_name(tool_name),
        tools=(tool_name,) if tool_name else (),
        tool_summaries=(_tool_summary(tool),),
    )


def _task_type_for_tool_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name in WEB_SOURCE_ARTIFACT_TOOLS:
        return WEB_RESEARCH_TASK_TYPE
    if name in WORKSPACE_WRITE_TOOL_NAMES:
        return CODE_CHANGE_TASK_TYPE
    if name in WORKSPACE_DISCOVERY_TOOL_NAMES or name in {
        LIST_RUN_FILE_CHANGES_TOOL_NAME,
        PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
    }:
        return WORKSPACE_READ_TASK_TYPE
    if name in {ANALYZE_IMAGE_TOOL_NAME, OCR_IMAGE_TOOL_NAME, TRANSCRIBE_AUDIO_TOOL_NAME, ANALYZE_VIDEO_TOOL_NAME}:
        return MEDIA_EXTRACTION_TASK_TYPE
    if name in {HISTORY_SEARCH_TOOL_NAME, LIST_RUN_FILE_CHANGES_TOOL_NAME}:
        return HISTORY_RETRIEVAL_TASK_TYPE
    if name in EXECUTION_TOOL_NAMES or name in {
        CONFIGURE_MCP_TOOL_NAME,
        CRON_TOOL_NAME,
        DELEGATE_MANY_TOOL_NAME,
        DELEGATE_TOOL_NAME,
        RUN_WORKFLOW_TOOL_NAME,
    }:
        return OPERATIONS_TASK_TYPE
    return GENERIC_TASK_TYPE


def _tool_summary(tool: Any) -> dict[str, str]:
    description = str(getattr(tool, "description", "") or "").strip()
    summary = {
        "name": str(getattr(tool, "name", "") or ""),
    }
    if description:
        summary["description"] = _truncate_tool_description(description, _MAX_TOOL_DESCRIPTION_CHARS)
    return summary


def _truncate_tool_description(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _contract_requests_planning_mode(task_contract: Any) -> bool:
    if task_contract is None:
        return False
    return is_planning_task_type(getattr(task_contract, "task_type", None))
