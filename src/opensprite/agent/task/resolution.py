"""Task context and objective resolution for turn planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from ...config.schema import DocumentLlmConfig
from ...documents.active_task import has_current_active_task
from ...llms import ChatMessage, is_unconfigured_llm
from ...llms.request_modes import LLMRequestMode, request_kwargs_for_mode
from ...tools.evidence import WEB_RESEARCH_TASK_TYPE
from ...utils.log import logger
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
)
from .intent import CONVERSATION_INTENT_KIND as _CONVERSATION_INTENT_KIND
from .intent import TaskIntent as _TaskIntent
from .value_utils import (
    _allowed_policy_value,
    _coerce_policy_bool,
    _coerce_policy_confidence,
    _compact_text,
    _policy_value,
    _truncate_middle_text,
    _truncate_text,
)

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
    return _policy_value(value) in ALLOWED_CONTINUATION_TYPES


def llm_string_or_none(value: object) -> str | None:
    normalized = _policy_value(value)
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
    return _policy_value(value) in FOLLOW_UP_CONTINUATION_TYPES


def is_new_task_continuation_type(value: str | None) -> bool:
    return _policy_value(value) in NEW_TASK_CONTINUATION_TYPES


def is_current_task_continuation_type(value: str | None) -> bool:
    return _policy_value(value) in CURRENT_TASK_CONTINUATION_TYPES


def is_current_task_replacement_type(value: str | None) -> bool:
    return _policy_value(value) in CURRENT_TASK_REPLACEMENT_TYPES


def is_objective_resolution_skip_type(value: str | None) -> bool:
    return _policy_value(value) in OBJECTIVE_RESOLUTION_SKIP_CONTINUATION_TYPES


def is_objective_resolution_enrichable_type(value: str | None) -> bool:
    return _policy_value(value) in OBJECTIVE_RESOLUTION_ENRICHABLE_CONTINUATION_TYPES


def is_ambiguous_boundary_continuation_type(value: str | None) -> bool:
    return _policy_value(value) == AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE


_ALLOWED_TASK_TYPES = frozenset(
    {
        ANALYSIS_TASK_TYPE,
        CODE_CHANGE_TASK_TYPE,
        "debug",
        HISTORY_RETRIEVAL_TASK_TYPE,
        MEDIA_EXTRACTION_TASK_TYPE,
        PLANNING_TASK_TYPE,
        PURE_ANSWER_TASK_TYPE,
        "review",
        GENERIC_TASK_TYPE,
        WEB_RESEARCH_TASK_TYPE,
        WORKSPACE_READ_TASK_TYPE,
        "writing",
    }
)
_LLM_TASK_SWITCH_CONFIDENCE = 0.80
_OBJECTIVE_MIN_CONFIDENCE = 0.65
DETERMINISTIC_CONTEXT_METHOD = "deterministic"
DETERMINISTIC_OBJECTIVE_METHOD = "deterministic"
DETERMINISTIC_DECISION_CONTEXT_FIELD = "deterministic_decision"
CURRENT_MESSAGE_ACKNOWLEDGEMENT_REASON = "current message is an acknowledgement"
TASK_CONTEXT_REQUIRES_LLM_CLASSIFICATION_REASON = "task context requires LLM classification"
LLM_RESOLVED_TASK_CONTEXT_REASON = "llm resolved task context"
OBJECTIVE_ENRICHMENT_NOT_NEEDED_REASON = "objective enrichment not needed"
LLM_RESOLVED_TASK_OBJECTIVE_REASON = "llm resolved task objective"
LLM_OBJECTIVE_NOT_MORE_SPECIFIC_REASON = "llm objective was not more specific"
TASK_BOUNDARY_CONFIDENCE_TOO_LOW_REASON_PREFIX = "task boundary confidence too low"
@dataclass(frozen=True)
class TaskContextDecision:
    """Resolved context for one user turn before task contracts are built."""

    is_follow_up: bool = False
    should_inherit_active_task: bool = False
    should_seed_active_task: bool = False
    should_replace_active_task: bool = False
    inherited_task_type: str | None = None
    continuation_type: str = NONE_CONTINUATION_TYPE
    confidence: float = 0.0
    method: str = DETERMINISTIC_CONTEXT_METHOD
    reason: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "is_follow_up": self.is_follow_up,
            "should_inherit_active_task": self.should_inherit_active_task,
            "should_seed_active_task": self.should_seed_active_task,
            "should_replace_active_task": self.should_replace_active_task,
            "inherited_task_type": self.inherited_task_type,
            "continuation_type": self.continuation_type,
            "confidence": self.confidence,
            "method": self.method,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TaskObjectiveDecision:
    """Resolved objective text for ACTIVE_TASK seeding."""

    original_message: str
    resolved_objective: str
    should_use_resolved_objective: bool = False
    confidence: float = 0.0
    method: str = DETERMINISTIC_OBJECTIVE_METHOD
    reason: str = ""

    @property
    def effective_objective(self) -> str:
        return self.resolved_objective if self.should_use_resolved_objective else self.original_message

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "original_message": self.original_message,
            "resolved_objective": self.resolved_objective,
            "should_use_resolved_objective": self.should_use_resolved_objective,
            "confidence": self.confidence,
            "method": self.method,
            "reason": self.reason,
        }


class TaskContextResolver:
    """Resolve whether a turn should inherit recent task context."""

    def __init__(self, llm_config: DocumentLlmConfig):
        self.llm_config = llm_config

    async def resolve(
        self,
        *,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        task_intent: _TaskIntent | None = None,
        active_task: str | None = None,
        work_state_summary: str | None = None,
        provider: Any | None = None,
        model: str | None = None,
    ) -> TaskContextDecision:
        deterministic = self.resolve_deterministic(
            current_message=current_message,
            history=history,
            task_intent=task_intent,
            active_task=active_task,
            work_state_summary=work_state_summary,
        )
        if not _should_consult_llm(
            current_message,
            deterministic,
            active_task,
            task_intent=task_intent,
            history=history,
            work_state_summary=work_state_summary,
        ):
            return deterministic
        if is_unconfigured_llm(provider, model):
            return _unresolved_llm_decision(llm_unavailable_reason(TASK_CONTEXT_RESOLUTION_PURPOSE))

        try:
            llm_decision = await self._resolve_with_llm(
                current_message=current_message,
                history=history or [],
                task_intent=task_intent,
                active_task=active_task or "",
                work_state_summary=work_state_summary or "",
                deterministic=deterministic,
                provider=provider,
                model=model,
            )
        except Exception as exc:
            logger.warning("Task context LLM resolution failed: {}", exc)
            return _unresolved_llm_decision(llm_failed_reason(TASK_CONTEXT_RESOLUTION_PURPOSE))

        llm_decision = _merge_with_deterministic(
            deterministic,
            llm_decision,
            has_active_task=has_current_active_task(active_task),
        )
        if llm_decision.confidence < 0.55:
            return _unresolved_llm_decision(llm_low_confidence_reason(llm_decision.confidence, TASK_CONTEXT_RESOLUTION_PURPOSE))
        return llm_decision

    @classmethod
    def resolve_deterministic(
        cls,
        *,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        task_intent: _TaskIntent | None = None,
        active_task: str | None = None,
        work_state_summary: str | None = None,
    ) -> TaskContextDecision:
        del history
        current = _resolver_compact(current_message)
        if not current or (task_intent is not None and task_intent.kind == _CONVERSATION_INTENT_KIND):
            return TaskContextDecision(
                continuation_type=ACK_CONTINUATION_TYPE,
                confidence=0.9,
                reason=CURRENT_MESSAGE_ACKNOWLEDGEMENT_REASON,
            )

        return TaskContextDecision(
            continuation_type=NONE_CONTINUATION_TYPE,
            confidence=0.45,
            reason=TASK_CONTEXT_REQUIRES_LLM_CLASSIFICATION_REASON,
        )

    async def _resolve_with_llm(
        self,
        *,
        current_message: str,
        history: list[dict[str, Any]],
        task_intent: _TaskIntent | None,
        active_task: str,
        work_state_summary: str,
        deterministic: TaskContextDecision,
        provider: Any,
        model: str | None,
    ) -> TaskContextDecision:
        prompt = _build_task_context_llm_prompt(
            current_message=current_message,
            history=history,
            task_intent=task_intent,
            active_task=active_task,
            work_state_summary=work_state_summary,
            deterministic=deterministic,
        )
        response = await _chat_json_planning_llm(
            provider=provider,
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "You classify whether the latest user turn inherits task context. "
                        "Return only one JSON object. Do not answer the user."
                    ),
                ),
                ChatMessage(role="user", content=prompt),
            ],
            model=model,
            llm_config=self.llm_config,
        )
        payload = _resolver_parse_json_object(_llm_response_text(response))
        return _task_context_decision_from_payload(payload, has_active_task=has_current_active_task(active_task))


class TaskObjectiveResolver:
    """Infer a clear ACTIVE_TASK objective when the user turn is too short."""

    def __init__(self, llm_config: DocumentLlmConfig):
        self.llm_config = llm_config

    async def resolve(
        self,
        *,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        task_intent: _TaskIntent | None = None,
        task_context_decision: TaskContextDecision | None = None,
        active_task: str | None = None,
        work_state_summary: str | None = None,
        provider: Any | None = None,
        model: str | None = None,
    ) -> TaskObjectiveDecision:
        original = _resolver_compact(current_message)
        deterministic = TaskObjectiveDecision(
            original_message=original,
            resolved_objective=original,
            reason=OBJECTIVE_ENRICHMENT_NOT_NEEDED_REASON,
        )
        if not _should_resolve_objective(
            current_message=original,
            history=history,
            task_intent=task_intent,
            task_context_decision=task_context_decision,
            active_task=active_task,
            work_state_summary=work_state_summary,
        ):
            return deterministic
        if is_unconfigured_llm(provider, model):
            return _unresolved_llm_objective(original, llm_unavailable_reason(TASK_OBJECTIVE_RESOLUTION_PURPOSE))

        try:
            llm_decision = await self._resolve_with_llm(
                current_message=original,
                history=history or [],
                task_intent=task_intent,
                task_context_decision=task_context_decision,
                active_task=active_task or "",
                work_state_summary=work_state_summary or "",
                provider=provider,
                model=model,
            )
        except Exception as exc:
            logger.warning("Task objective LLM resolution failed: {}", exc)
            return _unresolved_llm_objective(original, llm_failed_reason(TASK_OBJECTIVE_RESOLUTION_PURPOSE))

        if llm_decision.confidence < _OBJECTIVE_MIN_CONFIDENCE:
            return _unresolved_llm_objective(
                original,
                llm_low_confidence_reason(llm_decision.confidence, TASK_OBJECTIVE_RESOLUTION_PURPOSE),
            )
        if llm_decision.should_use_resolved_objective and not _is_useful_objective(
            llm_decision.resolved_objective,
            original,
        ):
            return _unresolved_llm_objective(original, LLM_OBJECTIVE_NOT_MORE_SPECIFIC_REASON)
        return llm_decision

    async def _resolve_with_llm(
        self,
        *,
        current_message: str,
        history: list[dict[str, Any]],
        task_intent: _TaskIntent | None,
        task_context_decision: TaskContextDecision | None,
        active_task: str,
        work_state_summary: str,
        provider: Any,
        model: str | None,
    ) -> TaskObjectiveDecision:
        prompt = _build_task_objective_llm_prompt(
            current_message=current_message,
            history=history,
            task_intent=task_intent,
            task_context_decision=task_context_decision,
            active_task=active_task,
            work_state_summary=work_state_summary,
        )
        response = await _chat_json_planning_llm(
            provider=provider,
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "You resolve a concise task objective for ACTIVE_TASK. "
                        "Return only one JSON object. Do not answer the user."
                    ),
                ),
                ChatMessage(role="user", content=prompt),
            ],
            model=model,
            llm_config=self.llm_config,
        )
        payload = _resolver_parse_json_object(_llm_response_text(response))
        return _task_objective_decision_from_payload(payload, current_message=current_message)


def _should_consult_llm(
    current_message: str,
    decision: TaskContextDecision,
    active_task: str | None,
    task_intent: _TaskIntent | None = None,
    history: list[dict[str, Any]] | None = None,
    work_state_summary: str | None = None,
) -> bool:
    current = _resolver_compact(current_message)
    if not current or (task_intent is not None and task_intent.kind == _CONVERSATION_INTENT_KIND):
        return False
    if decision.is_follow_up and decision.inherited_task_type:
        return True
    if decision.confidence >= 0.7:
        return False
    if has_current_active_task(active_task):
        return True
    if len(current) > 80:
        return False
    if decision.is_follow_up:
        return True
    if not has_current_active_task(active_task):
        return _has_recent_task_context(history, work_state_summary) and _is_context_dependent_short_turn(current)
    if decision.should_inherit_active_task:
        return True
    return True


def _build_task_context_llm_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    task_intent: _TaskIntent | None,
    active_task: str,
    work_state_summary: str,
    deterministic: TaskContextDecision,
) -> str:
    context = {
        "current_message": _resolver_truncate_middle(current_message, 600),
        "task_intent": task_intent.to_metadata() if task_intent is not None else None,
        "recent_history": _resolver_recent_history(history),
        "active_task": _resolver_truncate(active_task, 1800),
        "work_state_summary": _resolver_truncate(work_state_summary, 1200),
        DETERMINISTIC_DECISION_CONTEXT_FIELD: deterministic.to_metadata(),
    }
    return (
        "Decide whether the latest user message is a follow-up, continuation, or task switch.\n"
        "Handle multilingual, typo-heavy, shorthand, and code-mixed user turns.\n"
        "Use recent history and ACTIVE_TASK only as context.\n"
        "If evidence should be inherited, choose one inherited_task_type from the allowed task types.\n"
        "Do not mark a turn as no-tool if it likely asks for external web, media, or workspace evidence.\n"
        f"Do not remove evidence or active-task inheritance from {DETERMINISTIC_DECISION_CONTEXT_FIELD}; "
        "only add stricter context.\n"
        "If an active task exists and the latest turn might be either a new task or a continuation, use "
        f"continuation_type={AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE} instead of replacing the task.\n"
        "Return only JSON with these keys: continuation_type, is_follow_up, should_inherit_active_task, "
        "should_seed_active_task, should_replace_active_task, inherited_task_type, "
        "confidence, reason. Use null when no task type is inherited.\n"
        "continuation_type must be one of: "
        f"{', '.join(sorted(ALLOWED_CONTINUATION_TYPES))}.\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _build_task_objective_llm_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    task_intent: _TaskIntent | None,
    task_context_decision: TaskContextDecision | None,
    active_task: str,
    work_state_summary: str,
) -> str:
    context = {
        "current_message": _resolver_truncate_middle(current_message, 600),
        "task_intent": task_intent.to_metadata() if task_intent is not None else None,
        "task_context_decision": task_context_decision.to_metadata() if task_context_decision is not None else None,
        "recent_history": _resolver_recent_history(history),
        "active_task": _resolver_truncate(active_task, 1800),
        "work_state_summary": _resolver_truncate(work_state_summary, 1200),
    }
    return (
        "Rewrite the latest short or context-dependent user message into a clear task objective.\n"
        "Use only recent_history, active_task, work_state_summary, and task_context_decision as evidence.\n"
        "Preserve the user's intent and entities exactly; do not invent new requirements.\n"
        "If the context is insufficient or the message should simply continue the current active task, set "
        "should_use_resolved_objective to false.\n"
        "Do not relax or remove evidence, verification, or completion requirements.\n"
        "Return only JSON with these keys: resolved_objective, should_use_resolved_objective, confidence, reason.\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _task_context_decision_from_payload(payload: dict[str, Any], *, has_active_task: bool = False) -> TaskContextDecision:
    inherited_task_type = _resolver_allowed_string(payload.get("inherited_task_type"), _ALLOWED_TASK_TYPES)
    should_inherit_active_task = _resolver_coerce_bool(payload.get("should_inherit_active_task"))
    should_replace_active_task = _resolver_coerce_bool(payload.get("should_replace_active_task"))
    should_seed_active_task = _resolver_coerce_bool(payload.get("should_seed_active_task"))
    is_follow_up = _resolver_coerce_bool(payload.get("is_follow_up"))
    continuation_type = _continuation_type_from_payload(
        payload,
        is_follow_up=is_follow_up,
        should_inherit_active_task=should_inherit_active_task,
        should_seed_active_task=should_seed_active_task,
        should_replace_active_task=should_replace_active_task,
    )
    if is_follow_up_continuation_type(continuation_type):
        should_replace_active_task = False
        is_follow_up = True
    if continuation_type == CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE or (
        has_active_task and is_follow_up_continuation_type(continuation_type)
    ):
        should_inherit_active_task = True
        is_follow_up = True
    if is_new_task_continuation_type(continuation_type):
        should_inherit_active_task = False
        should_seed_active_task = True
    if is_ambiguous_boundary_continuation_type(continuation_type):
        should_inherit_active_task = False
        should_seed_active_task = False
        should_replace_active_task = False
        is_follow_up = False
        inherited_task_type = None
    return TaskContextDecision(
        is_follow_up=is_follow_up,
        should_inherit_active_task=should_inherit_active_task,
        should_seed_active_task=should_seed_active_task,
        should_replace_active_task=should_replace_active_task,
        inherited_task_type=inherited_task_type,
        continuation_type=continuation_type,
        confidence=_resolver_coerce_confidence(payload.get("confidence")),
        method="llm",
        reason=_resolver_truncate(str(payload.get("reason") or LLM_RESOLVED_TASK_CONTEXT_REASON), 240),
    )


def _merge_with_deterministic(
    deterministic: TaskContextDecision,
    llm_decision: TaskContextDecision,
    *,
    has_active_task: bool = False,
) -> TaskContextDecision:
    """Keep deterministic safety signals when accepting an LLM classification."""
    if deterministic.continuation_type == ACK_CONTINUATION_TYPE:
        return replace(
            llm_decision,
            continuation_type=ACK_CONTINUATION_TYPE,
            is_follow_up=False,
            should_inherit_active_task=False,
        )

    inherited_task_type = llm_decision.inherited_task_type

    continuation_type = llm_decision.continuation_type
    if (
        has_active_task
        and is_new_task_continuation_type(continuation_type)
        and llm_decision.confidence < _LLM_TASK_SWITCH_CONFIDENCE
    ):
        return replace(
            llm_decision,
            is_follow_up=False,
            should_inherit_active_task=False,
            should_seed_active_task=False,
            should_replace_active_task=False,
            inherited_task_type=None,
            continuation_type=AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE,
            reason=task_boundary_confidence_too_low_reason(llm_decision.confidence),
        )
    if (
        deterministic.should_inherit_active_task
        and not is_new_task_continuation_type(continuation_type)
        and not is_ambiguous_boundary_continuation_type(continuation_type)
    ):
        continuation_type = CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE
    elif inherited_task_type and continuation_type == NONE_CONTINUATION_TYPE:
        continuation_type = FOLLOW_UP_CONTINUATION_TYPE

    should_replace_active_task = llm_decision.should_replace_active_task and is_new_task_continuation_type(continuation_type)
    should_seed_active_task = llm_decision.should_seed_active_task or should_replace_active_task
    should_inherit_active_task = llm_decision.should_inherit_active_task
    if deterministic.should_inherit_active_task and not should_replace_active_task:
        should_inherit_active_task = True

    is_follow_up = llm_decision.is_follow_up
    if inherited_task_type or is_follow_up_continuation_type(continuation_type):
        is_follow_up = True
    if should_replace_active_task:
        is_follow_up = False

    return replace(
        llm_decision,
        is_follow_up=is_follow_up,
        should_inherit_active_task=should_inherit_active_task,
        should_seed_active_task=should_seed_active_task,
        should_replace_active_task=should_replace_active_task,
        inherited_task_type=inherited_task_type,
        continuation_type=continuation_type,
    )


def task_boundary_confidence_too_low_reason(confidence: float) -> str:
    return f"{TASK_BOUNDARY_CONFIDENCE_TOO_LOW_REASON_PREFIX} ({confidence:.2f}); ask for confirmation"


def _unresolved_llm_decision(reason: str) -> TaskContextDecision:
    return TaskContextDecision(
        continuation_type=NONE_CONTINUATION_TYPE,
        confidence=0.0,
        method="llm_unresolved",
        reason=reason,
    )


def _should_resolve_objective(
    *,
    current_message: str,
    history: list[dict[str, Any]] | None,
    task_intent: _TaskIntent | None,
    task_context_decision: TaskContextDecision | None,
    active_task: str | None,
    work_state_summary: str | None,
) -> bool:
    current = _resolver_compact(current_message)
    if not current:
        return False
    if task_context_decision and is_objective_resolution_skip_type(task_context_decision.continuation_type):
        return False
    if task_intent and task_intent.kind == _CONVERSATION_INTENT_KIND and not bool(
        task_context_decision
        and (
            task_context_decision.is_follow_up
            or is_objective_resolution_enrichable_type(task_context_decision.continuation_type)
        )
    ):
        return False
    if task_context_decision and is_new_task_continuation_type(task_context_decision.continuation_type):
        return _is_short_objective(current)
    if not _has_recent_objective_context(history, active_task, work_state_summary):
        return False
    if task_context_decision and is_objective_resolution_enrichable_type(task_context_decision.continuation_type):
        return True
    if task_context_decision and task_context_decision.is_follow_up:
        return True
    return _is_short_objective(current)


def _task_objective_decision_from_payload(payload: dict[str, Any], *, current_message: str) -> TaskObjectiveDecision:
    resolved = _resolver_truncate(_resolver_compact(str(payload.get("resolved_objective") or current_message)), 220)
    should_use = _resolver_coerce_bool(payload.get("should_use_resolved_objective"))
    return TaskObjectiveDecision(
        original_message=current_message,
        resolved_objective=resolved,
        should_use_resolved_objective=should_use,
        confidence=_resolver_coerce_confidence(payload.get("confidence")),
        method="llm",
        reason=_resolver_truncate(str(payload.get("reason") or LLM_RESOLVED_TASK_OBJECTIVE_REASON), 240),
    )


def _unresolved_llm_objective(original_message: str, reason: str) -> TaskObjectiveDecision:
    return TaskObjectiveDecision(
        original_message=original_message,
        resolved_objective=original_message,
        should_use_resolved_objective=False,
        confidence=0.0,
        method="llm_unresolved",
        reason=reason,
    )


def _resolver_parse_json_object(text: str) -> dict[str, Any]:
    raw = _json_object_text(text)
    if raw is None:
        raise ValueError("LLM did not return a JSON object")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON payload was not an object")
    return payload


async def _chat_json_planning_llm(
    *,
    provider: Any,
    messages: list[ChatMessage],
    model: str | None,
    llm_config: DocumentLlmConfig,
    request_mode: LLMRequestMode | str = LLMRequestMode.JSON_PLANNING,
) -> Any:
    """Call a provider for strict JSON routing/planning without reasoning output."""
    kwargs = _json_planning_request_kwargs(llm_config, request_mode=request_mode)
    return await provider.chat(messages=messages, model=model, **kwargs)


def _json_planning_request_kwargs(
    llm_config: DocumentLlmConfig,
    *,
    request_mode: LLMRequestMode | str = LLMRequestMode.JSON_PLANNING,
) -> dict[str, Any]:
    return request_kwargs_for_mode(llm_config.request_kwargs(), request_mode)

def _llm_response_text(response: Any) -> str:
    return str(getattr(response, "content", "") or "")


def _llm_response_preview(response: Any, *, text: str | None = None, max_chars: int = 240) -> str:
    response_text = _llm_response_text(response) if text is None else str(text or "")
    if response_text.strip():
        return _truncate_text(response_text, max_chars=max_chars)
    details = getattr(response, "reasoning_details", None) or []
    usage = getattr(response, "usage", None) or {}
    parts = ["<empty content>"]
    if details:
        parts.append(f"reasoning_details={len(details)}")
    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason:
        parts.append(f"finish_reason={finish_reason}")
    if isinstance(usage, dict) and usage:
        for key in ("completion_tokens", "output_tokens", "total_tokens"):
            if key in usage:
                parts.append(f"{key}={usage[key]}")
    return _truncate_text("; ".join(parts), max_chars=max_chars)


def _resolver_recent_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for message in history[-8:]:
        role = str(message.get("role") or "").strip() or "unknown"
        content = _resolver_truncate(str(message.get("content") or ""), 700)
        if content:
            items.append({"role": role, "content": content})
    return items


def _resolver_allowed_string(value: Any, allowed: frozenset[str]) -> str | None:
    normalized = llm_string_or_none(value)
    return _allowed_policy_value(normalized, allowed)


def _continuation_type_from_payload(
    payload: dict[str, Any],
    *,
    is_follow_up: bool,
    should_inherit_active_task: bool,
    should_seed_active_task: bool,
    should_replace_active_task: bool,
) -> str:
    normalized = str(payload.get("continuation_type") or "").strip()
    if is_allowed_continuation_type(normalized):
        return normalized
    if should_replace_active_task:
        return TASK_SWITCH_CONTINUATION_TYPE
    if should_seed_active_task:
        return NEW_TASK_CONTINUATION_TYPE
    if should_inherit_active_task:
        return CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE
    if is_follow_up:
        return FOLLOW_UP_CONTINUATION_TYPE
    return NONE_CONTINUATION_TYPE


def _has_recent_task_context(history: list[dict[str, Any]] | None, work_state_summary: str | None) -> bool:
    if has_current_active_task(work_state_summary):
        return True
    return any(
        _resolver_compact(str(message.get("content") or ""))
        for message in (history or [])[-6:]
        if str(message.get("role") or "").strip().lower() != "user"
    )


def _is_context_dependent_short_turn(current: str) -> bool:
    return len(task_text_tokens(current)) <= 8


def _has_recent_objective_context(
    history: list[dict[str, Any]] | None,
    active_task: str | None,
    work_state_summary: str | None,
) -> bool:
    if has_current_active_task(active_task) or _resolver_compact(work_state_summary):
        return True
    return any(_resolver_compact(str(message.get("content") or "")) for message in (history or [])[-6:])


def _is_short_objective(current_message: str) -> bool:
    current = _resolver_compact(current_message)
    if len(current) <= 40:
        return True
    return len(task_text_tokens(current)) <= 4


def _is_useful_objective(resolved_objective: str, original_message: str) -> bool:
    resolved = _resolver_compact(resolved_objective)
    original = _resolver_compact(original_message)
    if len(resolved) < 8:
        return False
    if resolved.lower() == original.lower():
        return False
    return True


def _resolver_coerce_bool(value: Any) -> bool:
    return _coerce_policy_bool(value)


def _resolver_coerce_confidence(value: Any) -> float:
    return _coerce_policy_confidence(value)


def _resolver_compact(value: str | None) -> str:
    return _compact_text(value)


def _resolver_truncate(value: str | None, max_chars: int) -> str:
    return _truncate_text(value, max_chars=max_chars)


def _resolver_truncate_middle(value: str | None, max_chars: int) -> str:
    return _truncate_middle_text(value, max_chars=max_chars)

def _json_object_text(text: str) -> str | None:
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    raw = fenced.group(1) if fenced else text
    for start, char in enumerate(raw):
        if char != "{":
            continue
        candidate = _balanced_json_object_text(raw, start=start)
        if candidate is not None:
            return candidate
    return None


def _balanced_json_object_text(text: str, *, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None

