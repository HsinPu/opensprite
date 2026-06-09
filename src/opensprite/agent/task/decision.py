"""Pre-work task planning for one inbound user turn."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ...bus.message import UserMessage
from ...llms import ChatMessage, is_unconfigured_llm
from ...storage import StoredWorkState
from .contract import (
    ANALYSIS_INTENT_KIND,
    COMMAND_INTENT_KIND,
    CONVERSATION_INTENT_KIND,
    GENERIC_TASK_INTENT_KIND,
    MEDIA_UPLOAD_INTENT_KIND,
    QUESTION_INTENT_KIND,
    REVIEW_INTENT_KIND,
    TaskContextDecision,
    TaskIntent,
    _chat_json_planning_llm,
    _compact_text,
    _llm_response_preview,
    _llm_response_text,
    _resolver_coerce_bool,
    _resolver_coerce_confidence,
    _resolver_parse_json_object,
    _resolver_truncate,
    _task_context_decision_from_payload,
    _truncate_intent_objective,
)


LLM_TASK_INTENT_METHOD = "llm"
LLM_INITIAL_TASK_PLANNING_REASON = "llm resolved initial task planning"
TASK_INITIAL_PLANNING_PURPOSE = "initial task planning was not inferred"
TASK_INITIAL_PLANNING_MIN_CONFIDENCE = 0.55
_INITIAL_TASK_PLANNING_REPAIR_SYSTEM_PROMPT = (
    "You repair OpenSprite initial task planning output. Return exactly one valid JSON object "
    "matching the requested schema. Do not answer the user. Do not include markdown."
)
_INITIAL_TASK_INTENT_KINDS = frozenset(
    {
        ANALYSIS_INTENT_KIND,
        COMMAND_INTENT_KIND,
        CONVERSATION_INTENT_KIND,
        GENERIC_TASK_INTENT_KIND,
        MEDIA_UPLOAD_INTENT_KIND,
        QUESTION_INTENT_KIND,
        REVIEW_INTENT_KIND,
    }
)
_INITIAL_CONTINUATION_TYPES = (
    "ack",
    "follow_up",
    "continue_active_task",
    "continue_last_answer",
    "continue_tool_work",
    "advance_current_step",
    "task_switch",
    "new_task",
    "ambiguous_boundary",
    "none",
)
_INITIAL_DONE_CRITERIA_KEYS = (
    "done_criteria",
    "success_criteria",
    "acceptance_criteria",
    "definition_of_done",
)


class InitialTaskPlanningError(RuntimeError):
    """Raised when the initial LLM task planning decision is unavailable or invalid."""


@dataclass(frozen=True)
class TurnTaskPlanningResult:
    """Task intent and initial work state prepared before execution branches."""

    task_intent: TaskIntent
    task_context_decision: TaskContextDecision
    existing_work_state: StoredWorkState | None
    work_plan: Any | None
    current_work_state: StoredWorkState | None
    task_intent_method: str = LLM_TASK_INTENT_METHOD
    task_intent_confidence: float = 1.0
    task_intent_reason: str = ""


@dataclass(frozen=True)
class _InitialTaskDecision:
    task_intent: TaskIntent
    task_context_decision: TaskContextDecision
    method: str
    confidence: float
    reason: str


class TurnTaskPlanningService:
    """Resolve initial task shape before the normal execution path starts."""

    def __init__(
        self,
        *,
        work_progress: Any,
        read_active_task_snapshot: Callable[[str], str],
        build_runtime_message: Callable[[str, dict[str, Any] | None], str],
        llm_config: Any | None = None,
    ) -> None:
        self.work_progress = work_progress
        self._read_active_task_snapshot = read_active_task_snapshot
        self._build_runtime_message = build_runtime_message
        self.llm_config = llm_config

    async def plan(
        self,
        *,
        user_message: UserMessage,
        session_id: str,
        user_metadata: dict[str, Any] | None,
        existing_work_state: StoredWorkState | None,
        provider: Any | None = None,
        model: str | None = None,
    ) -> TurnTaskPlanningResult:
        """Return task intent, pre-work context, and initial work state for one turn."""
        runtime_message = self._build_runtime_message(user_message.text, user_metadata)
        active_task = self._read_active_task_snapshot(session_id)
        work_state_summary = self.work_progress.render_state_summary(existing_work_state)
        decision = await self._resolve_with_llm(
            user_message=user_message,
            runtime_message=runtime_message,
            active_task=active_task,
            work_state_summary=work_state_summary,
            provider=provider,
            model=model,
        )
        return self._build_result(
            session_id=session_id,
            task_intent=decision.task_intent,
            task_context_decision=decision.task_context_decision,
            existing_work_state=existing_work_state,
            task_intent_method=decision.method,
            task_intent_confidence=decision.confidence,
            task_intent_reason=decision.reason,
        )

    async def _resolve_with_llm(
        self,
        *,
        user_message: UserMessage,
        runtime_message: str,
        active_task: str,
        work_state_summary: str,
        provider: Any | None,
        model: str | None,
    ) -> _InitialTaskDecision:
        """Return one LLM-backed initial task decision."""
        if self.llm_config is None:
            raise InitialTaskPlanningError("initial task planning requires an LLM config")
        if is_unconfigured_llm(provider, model):
            raise InitialTaskPlanningError("initial task planning requires a configured LLM provider and model")
        prompt = _build_initial_task_planning_prompt(
            user_message=user_message,
            runtime_message=runtime_message,
            active_task=active_task,
            work_state_summary=work_state_summary,
        )
        try:
            response = await _chat_json_planning_llm(
                provider=provider,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You classify the initial task shape for routing only. "
                            "Return one JSON object. Do not answer the user."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                model=model,
                llm_config=self.llm_config,
            )
        except Exception as exc:
            raise InitialTaskPlanningError(f"initial task planning LLM call failed: {exc}") from exc

        response_text = _llm_response_text(response)
        try:
            payload = _resolver_parse_json_object(response_text)
        except ValueError as exc:
            raw_response_preview = _llm_response_preview(response, text=response_text)
            try:
                repair_response = await _chat_json_planning_llm(
                    provider=provider,
                    messages=[
                        ChatMessage(role="system", content=_INITIAL_TASK_PLANNING_REPAIR_SYSTEM_PROMPT),
                        ChatMessage(
                            role="user",
                            content=(
                                "Original initial planning prompt:\n"
                                f"{prompt}\n\n"
                                "Invalid initial planning response:\n"
                                f"{raw_response_preview}\n\n"
                                "Return only the corrected JSON object."
                            ),
                        ),
                    ],
                    model=model,
                    llm_config=self.llm_config,
                )
            except Exception as repair_exc:
                raise InitialTaskPlanningError(
                    f"initial task planning repair LLM call failed: {repair_exc}"
                ) from repair_exc
            repair_text = _llm_response_text(repair_response)
            try:
                payload = _resolver_parse_json_object(repair_text)
            except ValueError as repair_parse_exc:
                repair_preview = _llm_response_preview(repair_response, text=repair_text)
                raise InitialTaskPlanningError(
                    f"initial task planning response was not valid JSON; raw_response_preview={repair_preview}"
                ) from repair_parse_exc
        if not payload:
            raise InitialTaskPlanningError("initial task planning response was empty")
        confidence = _resolver_coerce_confidence(payload.get("confidence"))
        if confidence < TASK_INITIAL_PLANNING_MIN_CONFIDENCE:
            raise InitialTaskPlanningError(f"initial task planning confidence too low: {confidence:.2f}")
        intent = _task_intent_from_initial_payload(
            payload.get("task_intent"),
        )
        context_payload = payload.get("task_context")
        if not isinstance(context_payload, dict):
            raise InitialTaskPlanningError("initial task planning response missing task_context")
        _validate_initial_task_context_payload(context_payload)
        context = _task_context_decision_from_payload(
            context_payload,
            has_active_task=bool(_compact_text(active_task)),
        )
        reason = _resolver_truncate(
            str(payload.get("reason") or context.reason or LLM_INITIAL_TASK_PLANNING_REASON),
            240,
        )
        return _InitialTaskDecision(
            task_intent=intent,
            task_context_decision=context,
            method=LLM_TASK_INTENT_METHOD,
            confidence=confidence,
            reason=reason,
        )

    def _build_result(
        self,
        *,
        session_id: str,
        task_intent: TaskIntent,
        task_context_decision: TaskContextDecision,
        existing_work_state: StoredWorkState | None,
        task_intent_method: str,
        task_intent_confidence: float,
        task_intent_reason: str,
    ) -> TurnTaskPlanningResult:
        """Build work plan and state from the selected initial decision."""
        task_intent = self.work_progress.resolve_intent(
            task_intent,
            existing_work_state,
            task_context_decision=task_context_decision,
        )
        work_plan = self.work_progress.create_plan(task_intent)
        current_work_state = self.work_progress.build_initial_state(
            session_id=session_id,
            task_intent=task_intent,
            work_plan=work_plan,
            existing_state=existing_work_state,
            task_context_decision=task_context_decision,
        )
        return TurnTaskPlanningResult(
            task_intent=task_intent,
            task_context_decision=task_context_decision,
            existing_work_state=existing_work_state,
            work_plan=work_plan,
            current_work_state=current_work_state,
            task_intent_method=task_intent_method,
            task_intent_confidence=task_intent_confidence,
            task_intent_reason=task_intent_reason,
        )


def _task_intent_from_initial_payload(
    payload: Any,
) -> TaskIntent:
    if not isinstance(payload, dict):
        raise InitialTaskPlanningError("initial task planning response missing task_intent")
    kind = _compact_text(payload.get("kind"))
    if kind not in _INITIAL_TASK_INTENT_KINDS:
        raise InitialTaskPlanningError(f"initial task planning returned invalid task_intent.kind: {kind or '<empty>'}")
    objective = _truncate_intent_objective(_compact_text(payload.get("objective")))
    if not objective:
        raise InitialTaskPlanningError("initial task planning response missing task_intent.objective")
    done_criteria = _initial_done_criteria(payload)
    if not done_criteria:
        raise InitialTaskPlanningError("initial task planning response missing task_intent.done_criteria")
    return TaskIntent(
        kind=kind,
        objective=objective,
        constraints=_string_tuple(payload.get("constraints")),
        done_criteria=done_criteria,
        needs_clarification=_resolver_coerce_bool(payload.get("needs_clarification")),
        verification_hint=_optional_string(payload.get("verification_hint")),
        long_running=_resolver_coerce_bool(payload.get("long_running")),
        expects_code_change=_resolver_coerce_bool(payload.get("expects_code_change")),
        expects_verification=_resolver_coerce_bool(payload.get("expects_verification")),
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for item in value:
        text = _compact_text(item)
        if text:
            items.append(text)
    return tuple(dict.fromkeys(items))


def _initial_done_criteria(payload: dict[str, Any]) -> tuple[str, ...]:
    for key in _INITIAL_DONE_CRITERIA_KEYS:
        criteria = _string_tuple(payload.get(key))
        if criteria:
            return criteria
    return ()


def _optional_string(value: Any) -> str | None:
    text = _compact_text(value)
    return text or None


def _validate_initial_task_context_payload(payload: dict[str, Any]) -> None:
    continuation_type = _compact_text(payload.get("continuation_type"))
    if not continuation_type:
        raise InitialTaskPlanningError("initial task planning response missing task_context.continuation_type")
    if continuation_type not in _INITIAL_CONTINUATION_TYPES:
        raise InitialTaskPlanningError(
            f"initial task planning returned invalid task_context.continuation_type: {continuation_type}"
        )
    if "confidence" not in payload:
        raise InitialTaskPlanningError("initial task planning response missing task_context.confidence")


def _build_initial_task_planning_prompt(
    *,
    user_message: UserMessage,
    runtime_message: str,
    active_task: str,
    work_state_summary: str,
) -> str:
    context = {
        "schema_version": 1,
        "current_message": runtime_message,
        "has_images": bool(user_message.images),
        "has_audios": bool(user_message.audios),
        "has_videos": bool(user_message.videos),
        "metadata": dict(user_message.metadata or {}),
        "active_task": active_task or "",
        "work_state_summary": work_state_summary or "",
        "allowed_intent_kinds": sorted(_INITIAL_TASK_INTENT_KINDS),
        "allowed_continuation_types": list(_INITIAL_CONTINUATION_TYPES),
    }
    output_shape = {
        "task_intent": {
            "kind": "<one allowed_intent_kind>",
            "objective": "<specific objective>",
            "constraints": [],
            "done_criteria": ["<one or more concrete completion checks>"],
            "needs_clarification": False,
            "long_running": False,
            "expects_code_change": False,
            "expects_verification": False,
            "verification_hint": None,
        },
        "task_context": {
            "is_follow_up": False,
            "should_inherit_active_task": False,
            "should_seed_active_task": True,
            "should_replace_active_task": False,
            "inherited_task_type": None,
            "continuation_type": "<one allowed_continuation_type>",
            "confidence": 0.0,
            "reason": "<short reason>",
        },
        "confidence": 0.0,
        "reason": "<short reason>",
    }
    return (
        "Classify the latest user turn before the main assistant response.\n"
        "This is a routing decision, not the user-visible answer.\n"
        "Decide both the broad task intent and whether the turn inherits current task context.\n"
        "Preserve exact user entities, paths, symbols, and requested constraints.\n"
        "When the turn continues or resumes existing work, set task_intent.objective to the active/work-state objective, "
        "not the literal short message such as continue.\n"
        "Use conservative values when evidence is unclear.\n"
        "Return only one JSON object. Copy these key names exactly:\n"
        f"{json.dumps(output_shape, ensure_ascii=False, indent=2)}\n"
        "Required fields:\n"
        "- task_intent.done_criteria must be a non-empty array of strings and must stay under task_intent.\n"
        "- task_context.continuation_type must be one allowed_continuation_type.\n"
        "- task_context.confidence and confidence must be numbers from 0 to 1.\n"
        "Output contract:\n"
        "- task_intent: object with kind, objective, constraints, done_criteria, needs_clarification, "
        "long_running, expects_code_change, expects_verification, verification_hint\n"
        "- task_context: object with is_follow_up, should_inherit_active_task, should_seed_active_task, "
        "should_replace_active_task, inherited_task_type, continuation_type, confidence, reason\n"
        "- confidence: number from 0 to 1 for the whole initial decision\n"
        "- reason: short reason for the whole initial decision\n"
        "Do not classify a normal slash command as a long-running task.\n"
        "Do not invent code-change or verification expectations unless the user explicitly asks to modify, commit, run tests, "
        "verify, review, deploy, operate, or inspect local execution state.\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )
