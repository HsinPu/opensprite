"""Shared task and intent classification policy for completion gating."""

from __future__ import annotations

from typing import Any

from .harness_profile import (
    ANALYSIS_TASK_TYPE,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    OPERATIONS_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    VERIFICATION_REQUIREMENT_KIND,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP,
)
from .task_contract import PLANNING_ERROR_TASK_TYPE
from .task_intent import ANALYSIS_INTENT_KIND, GENERIC_TASK_INTENT_KIND, ONE_TURN_INTENT_KINDS
from .tool_groups import OPERATION_TOOL_GROUPS
from .web_source_policy import is_web_research_task_type


NO_FALLBACK_ACTIVE_TASK_UPDATE_TYPES = frozenset({PURE_ANSWER_TASK_TYPE, PLANNING_ERROR_TASK_TYPE})
READ_ONLY_TASK_TYPES = frozenset(
    {ANALYSIS_TASK_TYPE, OPERATIONS_TASK_TYPE, WORKSPACE_READ_TASK_TYPE, HISTORY_RETRIEVAL_TASK_TYPE}
)
FINAL_RESPONSE_ACCEPTED_TASK_TYPES = frozenset({ANALYSIS_TASK_TYPE, PLANNING_TASK_TYPE, GENERIC_TASK_TYPE})
READ_ONLY_BLOCKING_REQUIREMENT_KINDS = frozenset({FILE_CHANGE_REQUIREMENT_KIND, VERIFICATION_REQUIREMENT_KIND})
READ_ONLY_BLOCKING_TOOL_GROUPS = frozenset(
    {WORKSPACE_WRITE_TOOL_GROUP, VERIFICATION_TOOL_GROUP, *OPERATION_TOOL_GROUPS}
)


def intent_supports_fallback_active_task_update(task_intent: Any, task_contract: Any) -> bool:
    if getattr(task_intent, "needs_clarification", False):
        return False
    task_type = str(getattr(task_contract, "task_type", "") or "").strip()
    if not task_type:
        return False
    return task_type not in NO_FALLBACK_ACTIVE_TASK_UPDATE_TYPES


def is_read_only_task_type(task_type: str | None) -> bool:
    normalized = str(task_type or "").strip()
    return is_web_research_task_type(normalized) or normalized in READ_ONLY_TASK_TYPES


def is_plain_answer_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == PURE_ANSWER_TASK_TYPE


def is_one_turn_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in ONE_TURN_INTENT_KINDS


def is_analysis_response_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == ANALYSIS_INTENT_KIND


def is_generic_task_response_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == GENERIC_TASK_INTENT_KIND


def is_read_only_blocking_requirement_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in READ_ONLY_BLOCKING_REQUIREMENT_KINDS


def is_read_only_blocking_tool_group(tool_group: str | None) -> bool:
    return str(tool_group or "").strip() in READ_ONLY_BLOCKING_TOOL_GROUPS


def accepts_final_response_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() in FINAL_RESPONSE_ACCEPTED_TASK_TYPES
