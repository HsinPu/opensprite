"""Explicit plan-before-build mode detection and overlay text."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tools import ToolRegistry


PLANNING_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "glob_files",
        "grep_files",
        "batch",
        "read_skill",
        "search_history",
        "search_knowledge",
        "list_run_file_changes",
        "preview_run_file_change_revert",
        "web_search",
        "web_fetch",
        "analyze_image",
        "ocr_image",
        "transcribe_audio",
        "analyze_video",
    }
)

_EXPLICIT_PLAN_ONLY_PHRASES = (
    "plan only",
    "planning only",
    "read-only planning mode",
    "do not implement yet",
    "don't implement yet",
    "don't start implementing",
    "先規劃不要動手",
    "先不要動手",
    "只要規劃",
    "先給我計畫",
    "先給我方案",
    "先規劃一下",
    "先不要實作",
    "先別改",
)
_PLAN_MARKERS = (
    "plan",
    "planning",
    "proposal",
    "outline",
    "approach",
    "game plan",
    "implementation plan",
    "規劃",
    "計畫",
    "方案",
    "步驟",
)
_NO_EXECUTION_MARKERS = (
    "read-only",
    "readonly",
    "do not implement",
    "don't implement",
    "do not edit",
    "don't edit",
    "do not change code",
    "不要動手",
    "不要實作",
    "不要修改",
    "不要改",
    "先不要做",
    "先別做",
)


@dataclass(frozen=True)
class PlanningModeState:
    """Resolved planning-mode state for one user turn."""

    enabled: bool = False
    overlay: str = ""
    tool_registry: "ToolRegistry | None" = None


def is_explicit_planning_mode_request(text: str | None) -> bool:
    """Return whether the user explicitly asked for planning before implementation."""
    compact = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not compact:
        return False
    if any(phrase in compact for phrase in _EXPLICIT_PLAN_ONLY_PHRASES):
        return True
    return any(marker in compact for marker in _PLAN_MARKERS) and any(
        marker in compact for marker in _NO_EXECUTION_MARKERS
    )


def build_planning_mode_overlay() -> str:
    """Return the temporary system overlay for explicit plan-only turns."""
    return """# Planning Mode

The user explicitly asked for planning before implementation. This turn is read-only planning mode.

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
) -> PlanningModeState:
    """Resolve the full planning-mode state for one user turn."""
    if not is_explicit_planning_mode_request(text):
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
    """Return a read-only registry used for explicit plan-only turns."""
    from ..tools import BatchTool
    from ..tools.permissions import CompositeToolPermissionPolicy, ToolPermissionPolicy

    planning_policy = ToolPermissionPolicy(
        allowed_tools=list(PLANNING_ALLOWED_TOOLS),
        allowed_risk_levels=["read", "network"],
        denied_risk_levels=[
            "write",
            "execute",
            "external_side_effect",
            "configuration",
            "delegation",
            "memory",
            "mcp",
        ],
    )
    registry = base_registry.filtered(
        include_names=PLANNING_ALLOWED_TOOLS,
        permission_policy=CompositeToolPermissionPolicy(base_registry.permission_policy, planning_policy),
    )
    if "batch" in registry.tool_names:
        registry.register(BatchTool(registry_resolver=lambda: registry))
    return registry
