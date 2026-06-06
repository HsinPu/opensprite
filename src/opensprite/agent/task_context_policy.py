"""Shared task-context continuation markers."""

from __future__ import annotations

import re


ACK_CONTINUATION_TYPE = "ack"
FOLLOW_UP_CONTINUATION_TYPE = "follow_up"
CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE = "continue_active_task"
CONTINUE_LAST_ANSWER_CONTINUATION_TYPE = "continue_last_answer"
CONTINUE_TOOL_WORK_CONTINUATION_TYPE = "continue_tool_work"
ADVANCE_CURRENT_STEP_CONTINUATION_TYPE = "advance_current_step"
TASK_SWITCH_CONTINUATION_TYPE = "task_switch"
NEW_TASK_CONTINUATION_TYPE = "new_task"
REPLACE_ACTIVE_TASK_CONTINUATION_TYPE = "replace_active_task"
TOPIC_SHIFT_CONTINUATION_TYPE = "topic_shift"
AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE = "ambiguous_boundary"
NONE_CONTINUATION_TYPE = "none"
BOUNDARY_SWITCH_REPLY_COMMAND = "switch"
BOUNDARY_CONTINUE_REPLY_COMMAND = "continue"
LLM_EMPTY_VALUE_SENTINELS = frozenset({NONE_CONTINUATION_TYPE, "null", "n/a"})
TASK_TEXT_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")
LLM_UNAVAILABLE_REASON_PREFIX = "llm unavailable"
LLM_FAILED_REASON_PREFIX = "llm failed"
LLM_LOW_CONFIDENCE_REASON_PREFIX = "llm confidence too low"
TASK_CONTEXT_RESOLUTION_PURPOSE = "task context was not inferred"
TASK_OBJECTIVE_RESOLUTION_PURPOSE = "objective was not enriched"

FOLLOW_UP_CONTINUATION_TYPES = frozenset(
    {
        FOLLOW_UP_CONTINUATION_TYPE,
        CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE,
        CONTINUE_LAST_ANSWER_CONTINUATION_TYPE,
        CONTINUE_TOOL_WORK_CONTINUATION_TYPE,
        ADVANCE_CURRENT_STEP_CONTINUATION_TYPE,
    }
)
NEW_TASK_CONTINUATION_TYPES = frozenset({TASK_SWITCH_CONTINUATION_TYPE, NEW_TASK_CONTINUATION_TYPE})
CURRENT_TASK_CONTINUATION_TYPES = FOLLOW_UP_CONTINUATION_TYPES
CURRENT_TASK_REPLACEMENT_TYPES = NEW_TASK_CONTINUATION_TYPES
OBJECTIVE_RESOLUTION_SKIP_CONTINUATION_TYPES = frozenset(
    {
        ACK_CONTINUATION_TYPE,
        AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE,
        CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE,
    }
)
OBJECTIVE_RESOLUTION_ENRICHABLE_CONTINUATION_TYPES = frozenset(
    {
        FOLLOW_UP_CONTINUATION_TYPE,
        CONTINUE_LAST_ANSWER_CONTINUATION_TYPE,
        CONTINUE_TOOL_WORK_CONTINUATION_TYPE,
    }
)
PRESERVE_STATE_RESET_CONTINUATION_TYPES = frozenset(
    {
        NEW_TASK_CONTINUATION_TYPE,
        REPLACE_ACTIVE_TASK_CONTINUATION_TYPE,
        TOPIC_SHIFT_CONTINUATION_TYPE,
    }
)
ALLOWED_CONTINUATION_TYPES = frozenset(
    {
        ACK_CONTINUATION_TYPE,
        *FOLLOW_UP_CONTINUATION_TYPES,
        *NEW_TASK_CONTINUATION_TYPES,
        AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE,
        NONE_CONTINUATION_TYPE,
    }
)


def is_allowed_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() in ALLOWED_CONTINUATION_TYPES


def llm_string_or_none(value: object) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or normalized.lower() in LLM_EMPTY_VALUE_SENTINELS:
        return None
    return normalized


def task_text_tokens(text: str | None) -> tuple[str, ...]:
    """Return coarse language-neutral tokens for short follow-up heuristics."""
    return tuple(TASK_TEXT_TOKEN_RE.findall(str(text or "")))


def llm_unavailable_reason(purpose: str) -> str:
    return _resolution_reason(LLM_UNAVAILABLE_REASON_PREFIX, purpose)


def llm_failed_reason(purpose: str) -> str:
    return _resolution_reason(LLM_FAILED_REASON_PREFIX, purpose)


def llm_low_confidence_reason(confidence: float, purpose: str) -> str:
    return f"{LLM_LOW_CONFIDENCE_REASON_PREFIX} ({confidence:.2f}); {purpose}"


def _resolution_reason(prefix: str, purpose: str) -> str:
    return f"{prefix}; {purpose}"


def is_follow_up_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() in FOLLOW_UP_CONTINUATION_TYPES


def is_new_task_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() in NEW_TASK_CONTINUATION_TYPES


def is_current_task_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() in CURRENT_TASK_CONTINUATION_TYPES


def is_current_task_replacement_type(value: str | None) -> bool:
    return str(value or "").strip() in CURRENT_TASK_REPLACEMENT_TYPES


def is_objective_resolution_skip_type(value: str | None) -> bool:
    return str(value or "").strip() in OBJECTIVE_RESOLUTION_SKIP_CONTINUATION_TYPES


def is_objective_resolution_enrichable_type(value: str | None) -> bool:
    return str(value or "").strip() in OBJECTIVE_RESOLUTION_ENRICHABLE_CONTINUATION_TYPES


def is_ambiguous_boundary_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() == AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE
