"""Hybrid task-context resolution for follow-ups and active-task handoff."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..llms import ChatMessage, is_unconfigured_llm
from ..utils.log import logger
from .active_task_status import has_current_active_task
from .harness_profile import (
    ANALYSIS_TASK_TYPE,
    CODE_CHANGE_TASK_TYPE,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP,
    MEDIA_EXTRACTION_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP,
    WORKSPACE_WRITE_TOOL_GROUP,
)
from .task_context_policy import (
    ACK_CONTINUATION_TYPE,
    ALLOWED_CONTINUATION_TYPES,
    AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE,
    CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE,
    FOLLOW_UP_CONTINUATION_TYPE,
    NEW_TASK_CONTINUATION_TYPE,
    NONE_CONTINUATION_TYPE,
    TASK_SWITCH_CONTINUATION_TYPE,
    is_allowed_continuation_type,
    is_ambiguous_boundary_continuation_type,
    is_follow_up_continuation_type,
    is_new_task_continuation_type,
    llm_string_or_none,
)
from .task_intent import CONVERSATION_INTENT_KIND, TaskIntent
from .task_context_policy import task_text_tokens
from .task_context_policy import (
    TASK_CONTEXT_RESOLUTION_PURPOSE,
    llm_failed_reason,
    llm_low_confidence_reason,
    llm_unavailable_reason,
)
from .web_source_policy import WEB_RESEARCH_TASK_TYPE, WEB_RESEARCH_TOOL_GROUP


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
_ALLOWED_TOOL_GROUPS = frozenset(
    {
        "audio_text",
        HISTORY_RETRIEVAL_TOOL_GROUP,
        "image_text",
        VERIFICATION_TOOL_GROUP,
        "video_understanding",
        WEB_RESEARCH_TOOL_GROUP,
        WORKSPACE_READ_TOOL_GROUP,
        WORKSPACE_WRITE_TOOL_GROUP,
    }
)
_LLM_TASK_SWITCH_CONFIDENCE = 0.80
DETERMINISTIC_CONTEXT_METHOD = "deterministic"
DETERMINISTIC_DECISION_CONTEXT_FIELD = "deterministic_decision"
CURRENT_MESSAGE_ACKNOWLEDGEMENT_REASON = "current message is an acknowledgement"
TASK_CONTEXT_REQUIRES_LLM_CLASSIFICATION_REASON = "task context requires LLM classification"
LLM_RESOLVED_TASK_CONTEXT_REASON = "llm resolved task context"
TASK_BOUNDARY_CONFIDENCE_TOO_LOW_REASON_PREFIX = "task boundary confidence too low"
_TASK_TYPE_BY_TOOL_GROUP = {
    "audio_text": MEDIA_EXTRACTION_TASK_TYPE,
    "image_text": MEDIA_EXTRACTION_TASK_TYPE,
    "video_understanding": MEDIA_EXTRACTION_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP: HISTORY_RETRIEVAL_TASK_TYPE,
    WEB_RESEARCH_TOOL_GROUP: WEB_RESEARCH_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP: WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP: CODE_CHANGE_TASK_TYPE,
}


@dataclass(frozen=True)
class TaskContextDecision:
    """Resolved context for one user turn before task contracts are built."""

    is_follow_up: bool = False
    should_inherit_active_task: bool = False
    should_seed_active_task: bool = False
    should_replace_active_task: bool = False
    inherited_task_type: str | None = None
    inherited_tool_group: str | None = None
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
            "inherited_tool_group": self.inherited_tool_group,
            "continuation_type": self.continuation_type,
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
        task_intent: TaskIntent | None = None,
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
            has_active_task=_has_active_task(active_task),
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
        task_intent: TaskIntent | None = None,
        active_task: str | None = None,
        work_state_summary: str | None = None,
    ) -> TaskContextDecision:
        del history
        current = _compact(current_message)
        if not current or (task_intent is not None and task_intent.kind == CONVERSATION_INTENT_KIND):
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
        task_intent: TaskIntent | None,
        active_task: str,
        work_state_summary: str,
        deterministic: TaskContextDecision,
        provider: Any,
        model: str | None,
    ) -> TaskContextDecision:
        prompt = _build_llm_prompt(
            current_message=current_message,
            history=history,
            task_intent=task_intent,
            active_task=active_task,
            work_state_summary=work_state_summary,
            deterministic=deterministic,
        )
        response = await provider.chat(
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
            **self.llm_config.decoding_kwargs(),
        )
        payload = _parse_json_object(str(getattr(response, "content", "") or ""))
        return _decision_from_payload(payload, has_active_task=_has_active_task(active_task))


def _should_consult_llm(
    current_message: str,
    decision: TaskContextDecision,
    active_task: str | None,
    task_intent: TaskIntent | None = None,
    history: list[dict[str, Any]] | None = None,
    work_state_summary: str | None = None,
) -> bool:
    current = _compact(current_message)
    if not current or (task_intent is not None and task_intent.kind == CONVERSATION_INTENT_KIND):
        return False
    if decision.is_follow_up and decision.inherited_tool_group:
        return True
    if decision.confidence >= 0.7:
        return False
    if _has_active_task(active_task):
        return True
    if len(current) > 80:
        return False
    if decision.is_follow_up:
        return True
    if not _has_active_task(active_task):
        return _has_recent_context(history, work_state_summary) and _is_context_dependent_short_turn(current)
    if decision.should_inherit_active_task:
        return True
    return True


def _build_llm_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    task_intent: TaskIntent | None,
    active_task: str,
    work_state_summary: str,
    deterministic: TaskContextDecision,
) -> str:
    context = {
        "current_message": _truncate_middle(current_message, 600),
        "task_intent": task_intent.to_metadata() if task_intent is not None else None,
        "recent_history": _recent_history(history),
        "active_task": _truncate(active_task, 1800),
        "work_state_summary": _truncate(work_state_summary, 1200),
        DETERMINISTIC_DECISION_CONTEXT_FIELD: deterministic.to_metadata(),
    }
    return (
        "Decide whether the latest user message is a follow-up, continuation, or task switch.\n"
        "Handle multilingual, typo-heavy, shorthand, and code-mixed user turns.\n"
        "Use recent history and ACTIVE_TASK only as context.\n"
        "If evidence should be inherited, choose one inherited_tool_group from: "
        f"{', '.join(sorted(_ALLOWED_TOOL_GROUPS))}.\n"
        "Do not mark a turn as no-tool if it likely asks for external web, media, or workspace evidence.\n"
        f"Do not remove evidence or active-task inheritance from {DETERMINISTIC_DECISION_CONTEXT_FIELD}; "
        "only add stricter context.\n"
        "If an active task exists and the latest turn might be either a new task or a continuation, use "
        f"continuation_type={AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE} instead of replacing the task.\n"
        "Return only JSON with these keys: continuation_type, is_follow_up, should_inherit_active_task, "
        "should_seed_active_task, should_replace_active_task, inherited_task_type, inherited_tool_group, "
        "confidence, reason. Use null when no task/tool is inherited.\n"
        "continuation_type must be one of: "
        f"{', '.join(sorted(ALLOWED_CONTINUATION_TYPES))}.\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _decision_from_payload(payload: dict[str, Any], *, has_active_task: bool = False) -> TaskContextDecision:
    inherited_tool_group = _allowed_string(payload.get("inherited_tool_group"), _ALLOWED_TOOL_GROUPS)
    inherited_task_type = _allowed_string(payload.get("inherited_task_type"), _ALLOWED_TASK_TYPES)
    if inherited_tool_group and inherited_task_type is None:
        inherited_task_type = _TASK_TYPE_BY_TOOL_GROUP.get(inherited_tool_group)
    should_inherit_active_task = _coerce_bool(payload.get("should_inherit_active_task"))
    should_replace_active_task = _coerce_bool(payload.get("should_replace_active_task"))
    should_seed_active_task = _coerce_bool(payload.get("should_seed_active_task"))
    is_follow_up = _coerce_bool(payload.get("is_follow_up"))
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
        inherited_tool_group = None
    return TaskContextDecision(
        is_follow_up=is_follow_up,
        should_inherit_active_task=should_inherit_active_task,
        should_seed_active_task=should_seed_active_task,
        should_replace_active_task=should_replace_active_task,
        inherited_task_type=inherited_task_type,
        inherited_tool_group=inherited_tool_group,
        continuation_type=continuation_type,
        confidence=_coerce_confidence(payload.get("confidence")),
        method="llm",
        reason=_truncate(str(payload.get("reason") or LLM_RESOLVED_TASK_CONTEXT_REASON), 240),
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

    inherited_tool_group = llm_decision.inherited_tool_group
    inherited_task_type = llm_decision.inherited_task_type
    if inherited_tool_group and inherited_task_type is None:
        inherited_task_type = _TASK_TYPE_BY_TOOL_GROUP.get(inherited_tool_group)

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
            inherited_tool_group=None,
            continuation_type=AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE,
            reason=task_boundary_confidence_too_low_reason(llm_decision.confidence),
        )
    if (
        deterministic.should_inherit_active_task
        and not is_new_task_continuation_type(continuation_type)
        and not is_ambiguous_boundary_continuation_type(continuation_type)
    ):
        continuation_type = CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE
    elif inherited_tool_group and continuation_type == NONE_CONTINUATION_TYPE:
        continuation_type = FOLLOW_UP_CONTINUATION_TYPE

    should_replace_active_task = llm_decision.should_replace_active_task and is_new_task_continuation_type(continuation_type)
    should_seed_active_task = llm_decision.should_seed_active_task or should_replace_active_task
    should_inherit_active_task = llm_decision.should_inherit_active_task
    if deterministic.should_inherit_active_task and not should_replace_active_task:
        should_inherit_active_task = True

    is_follow_up = llm_decision.is_follow_up
    if inherited_tool_group or is_follow_up_continuation_type(continuation_type):
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
        inherited_tool_group=inherited_tool_group,
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


def _parse_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("LLM did not return a JSON object")
        text = text[start : end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON payload was not an object")
    return payload


def _recent_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for message in history[-8:]:
        role = str(message.get("role") or "").strip() or "unknown"
        content = _truncate(str(message.get("content") or ""), 700)
        if content:
            items.append({"role": role, "content": content})
    return items


def _allowed_string(value: Any, allowed: frozenset[str]) -> str | None:
    normalized = llm_string_or_none(value)
    if normalized is None:
        return None
    return normalized if normalized in allowed else None


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


def _has_recent_context(history: list[dict[str, Any]] | None, work_state_summary: str | None) -> bool:
    if _has_active_task(work_state_summary):
        return True
    return any(
        _compact(str(message.get("content") or ""))
        for message in (history or [])[-6:]
        if str(message.get("role") or "").strip().lower() != "user"
    )


def _is_context_dependent_short_turn(current: str) -> bool:
    return len(task_text_tokens(current)) <= 8


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, confidence))


def _has_active_task(active_task: str | None) -> bool:
    return has_current_active_task(active_task)


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: str | None, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _truncate_middle(value: str | None, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return _truncate(text, max_chars)
    marker = "\n... [middle omitted] ...\n"
    remaining = max_chars - len(marker)
    head_chars = max(1, remaining // 2)
    tail_chars = max(1, remaining - head_chars)
    return f"{text[:head_chars].rstrip()}{marker}{text[-tail_chars:].lstrip()}"


__all__ = ["TaskContextDecision", "TaskContextResolver"]
