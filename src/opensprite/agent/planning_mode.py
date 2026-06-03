"""Contract-driven plan-before-build mode and overlay text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .harness_profile import PLANNING_TASK_TYPE
from .history_retrieval_policy import HISTORY_SEARCH_TOOL_NAME
from .web_source_policy import WEB_SOURCE_EVIDENCE_TOOLS
from ..tool_names import (
    BATCH_TOOL_NAME,
    GLOB_FILES_TOOL_NAME,
    GREP_FILES_TOOL_NAME,
    LIST_DIR_TOOL_NAME,
    READ_FILE_TOOL_NAME,
)

if TYPE_CHECKING:
    from .task_contract import TaskContract
    from ..tools import ToolRegistry


PLANNING_ALLOWED_TOOLS = frozenset(
    {
        READ_FILE_TOOL_NAME,
        LIST_DIR_TOOL_NAME,
        GLOB_FILES_TOOL_NAME,
        GREP_FILES_TOOL_NAME,
        BATCH_TOOL_NAME,
        "read_skill",
        HISTORY_SEARCH_TOOL_NAME,
        "list_run_file_changes",
        "preview_run_file_change_revert",
        *WEB_SOURCE_EVIDENCE_TOOLS,
        "analyze_image",
        "ocr_image",
        "transcribe_audio",
        "analyze_video",
    }
)


@dataclass(frozen=True)
class PlanningModeState:
    """Resolved planning-mode state for one user turn."""

    enabled: bool = False
    overlay: str = ""
    tool_registry: "ToolRegistry | None" = None


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
    text: str | None,
    *,
    base_registry: "ToolRegistry | None" = None,
    task_contract: "TaskContract | None" = None,
) -> PlanningModeState:
    """Resolve the full planning-mode state for one user turn."""
    del text
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


def build_planning_mode_tool_registry(base_registry: "ToolRegistry") -> "ToolRegistry":
    """Return a read-only registry used for plan-only turns."""
    from .tool_access import ToolAccessResolver, planning_mode_permission_policy

    resolution = ToolAccessResolver().resolve_overlay(
        base_registry,
        overlay_policy=planning_mode_permission_policy(PLANNING_ALLOWED_TOOLS),
        include_names=PLANNING_ALLOWED_TOOLS,
        metadata_kind="planning_mode",
    )
    return resolution.registry


def _contract_requests_planning_mode(task_contract: "TaskContract | None") -> bool:
    if task_contract is None:
        return False
    return str(getattr(task_contract, "task_type", "") or "").strip() == PLANNING_TASK_TYPE
