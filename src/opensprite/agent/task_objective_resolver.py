"""Resolve concise task objectives for short follow-up turns."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..llms import ChatMessage, is_unconfigured_llm
from ..utils.log import logger
from .active_task_status import has_current_active_task
from .task_context_policy import (
    is_new_task_continuation_type,
    is_objective_resolution_enrichable_type,
    is_objective_resolution_skip_type,
)
from .task_context_resolver import TaskContextDecision
from .task_intent import CONVERSATION_INTENT_KIND, TaskIntent
from .task_context_policy import task_text_tokens
from .task_context_policy import (
    TASK_OBJECTIVE_RESOLUTION_PURPOSE,
    llm_failed_reason,
    llm_low_confidence_reason,
    llm_unavailable_reason,
)


_MIN_CONFIDENCE = 0.65
DETERMINISTIC_OBJECTIVE_METHOD = "deterministic"
OBJECTIVE_ENRICHMENT_NOT_NEEDED_REASON = "objective enrichment not needed"
LLM_RESOLVED_TASK_OBJECTIVE_REASON = "llm resolved task objective"
LLM_OBJECTIVE_NOT_MORE_SPECIFIC_REASON = "llm objective was not more specific"


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


class TaskObjectiveResolver:
    """Infer a clear ACTIVE_TASK objective when the user turn is too short."""

    def __init__(self, llm_config: DocumentLlmConfig):
        self.llm_config = llm_config

    async def resolve(
        self,
        *,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        task_intent: TaskIntent | None = None,
        task_context_decision: TaskContextDecision | None = None,
        active_task: str | None = None,
        work_state_summary: str | None = None,
        provider: Any | None = None,
        model: str | None = None,
    ) -> TaskObjectiveDecision:
        original = _compact(current_message)
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

        if llm_decision.confidence < _MIN_CONFIDENCE:
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
        task_intent: TaskIntent | None,
        task_context_decision: TaskContextDecision | None,
        active_task: str,
        work_state_summary: str,
        provider: Any,
        model: str | None,
    ) -> TaskObjectiveDecision:
        prompt = _build_llm_prompt(
            current_message=current_message,
            history=history,
            task_intent=task_intent,
            task_context_decision=task_context_decision,
            active_task=active_task,
            work_state_summary=work_state_summary,
        )
        response = await provider.chat(
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
            **self.llm_config.decoding_kwargs(),
        )
        payload = _parse_json_object(str(getattr(response, "content", "") or ""))
        return _decision_from_payload(payload, current_message=current_message)


def _should_resolve_objective(
    *,
    current_message: str,
    history: list[dict[str, Any]] | None,
    task_intent: TaskIntent | None,
    task_context_decision: TaskContextDecision | None,
    active_task: str | None,
    work_state_summary: str | None,
) -> bool:
    current = _compact(current_message)
    if not current:
        return False
    if task_context_decision and is_objective_resolution_skip_type(task_context_decision.continuation_type):
        return False
    if task_intent and task_intent.kind == CONVERSATION_INTENT_KIND and not bool(
        task_context_decision
        and (
            task_context_decision.is_follow_up
            or is_objective_resolution_enrichable_type(task_context_decision.continuation_type)
        )
    ):
        return False
    if task_context_decision and is_new_task_continuation_type(task_context_decision.continuation_type):
        return _is_short_objective(current)
    if not _has_recent_context(history, active_task, work_state_summary):
        return False
    if task_context_decision and is_objective_resolution_enrichable_type(task_context_decision.continuation_type):
        return True
    if task_context_decision and task_context_decision.is_follow_up:
        return True
    return _is_short_objective(current)


def _build_llm_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    task_intent: TaskIntent | None,
    task_context_decision: TaskContextDecision | None,
    active_task: str,
    work_state_summary: str,
) -> str:
    context = {
        "current_message": _truncate_middle(current_message, 600),
        "task_intent": task_intent.to_metadata() if task_intent is not None else None,
        "task_context_decision": task_context_decision.to_metadata() if task_context_decision is not None else None,
        "recent_history": _recent_history(history),
        "active_task": _truncate(active_task, 1800),
        "work_state_summary": _truncate(work_state_summary, 1200),
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


def _decision_from_payload(payload: dict[str, Any], *, current_message: str) -> TaskObjectiveDecision:
    resolved = _truncate(_compact(str(payload.get("resolved_objective") or current_message)), 220)
    should_use = _coerce_bool(payload.get("should_use_resolved_objective"))
    return TaskObjectiveDecision(
        original_message=current_message,
        resolved_objective=resolved,
        should_use_resolved_objective=should_use,
        confidence=_coerce_confidence(payload.get("confidence")),
        method="llm",
        reason=_truncate(str(payload.get("reason") or LLM_RESOLVED_TASK_OBJECTIVE_REASON), 240),
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


def _has_recent_context(
    history: list[dict[str, Any]] | None,
    active_task: str | None,
    work_state_summary: str | None,
) -> bool:
    if _has_active_task(active_task) or _compact(work_state_summary):
        return True
    return any(_compact(str(message.get("content") or "")) for message in (history or [])[-6:])


def _has_active_task(active_task: str | None) -> bool:
    return has_current_active_task(active_task)


def _is_short_objective(current_message: str) -> bool:
    current = _compact(current_message)
    if len(current) <= 40:
        return True
    return len(task_text_tokens(current)) <= 4


def _is_useful_objective(resolved_objective: str, original_message: str) -> bool:
    resolved = _compact(resolved_objective)
    original = _compact(original_message)
    if len(resolved) < 8:
        return False
    if resolved.lower() == original.lower():
        return False
    return True


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


__all__ = ["TaskObjectiveDecision", "TaskObjectiveResolver"]
