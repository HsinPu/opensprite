"""Hybrid task-context resolution for follow-ups and active-task handoff."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..llms import ChatMessage
from ..utils.log import logger
from .task_intent import TaskIntent


_ACK_RE = re.compile(
    r"^(?:ok|okay|thanks|thank you|thx|好|好的|了解|知道了|謝謝|謝啦|感謝|不用|先不用)[。.!！?？]*$",
    re.IGNORECASE,
)
_CONTINUATION_RE = re.compile(
    r"^(?:continue|keep going|go on|proceed|繼續|接著|繼續做|繼續處理|繼續吧|往下做)[。.!！?？]*$",
    re.IGNORECASE,
)
_BOUNDARY_SWITCH_CONFIRMATION_RE = re.compile(
    r"^(?:switch|switch task|switch tasks|switch to the new request|switch to new request|"
    r"do the new request|use the new request|new request|replace it|change task|"
    r"切換|切換到新任務|切到新任務|換|換任務|換成新的|換新任務|改做新的|改做新任務|做新的|做新任務)[。.!！?？]*$",
    re.IGNORECASE,
)
_BOUNDARY_CONTINUE_CONFIRMATION_RE = re.compile(
    r"^(?:continue the active task|continue active task|continue current task|continue the current task|"
    r"keep current task|keep the current task|keep the active task|stick with current task|"
    r"繼續原本|繼續原本的|繼續原本任務|繼續目前|繼續目前任務|維持原本|維持目前|不要切換|別切換)[。.!！?？]*$",
    re.IGNORECASE,
)
_BOUNDARY_REQUEST_PATTERNS = (
    re.compile(
        r"Reply `switch` to replace(?: the active task \(.+?\)| it) with the new request \((?P<request>.+?)\),? "
        r"or `continue` to keep the active task\.",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
    r"Confirm whether to switch(?: from the active task \(.+?\))? to the new request \((?P<request>.+?)\),? "
    r"or continue the active task\.",
    re.IGNORECASE | re.DOTALL,
    ),
)
_ACTIVE_STATUS_RE = re.compile(r"^- Status:\s*(?P<status>.+)$", re.MULTILINE)
_ALLOWED_TASK_TYPES = frozenset(
    {
        "analysis",
        "code_change",
        "debug",
        "history_retrieval",
        "media_extraction",
        "planning",
        "pure_answer",
        "review",
        "task",
        "web_research",
        "workspace_read",
        "writing",
    }
)
_ALLOWED_TOOL_GROUPS = frozenset(
    {
        "audio_text",
        "history_retrieval",
        "image_text",
        "verification",
        "video_understanding",
        "web_research",
        "workspace_read",
        "workspace_write",
    }
)
_ALLOWED_CONTINUATION_TYPES = frozenset(
    {
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
    }
)
_FOLLOW_UP_CONTINUATION_TYPES = frozenset(
    {
        "follow_up",
        "continue_active_task",
        "continue_last_answer",
        "continue_tool_work",
        "advance_current_step",
    }
)
_NEW_TASK_CONTINUATION_TYPES = frozenset({"task_switch", "new_task"})
_AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE = "ambiguous_boundary"
_LLM_TASK_SWITCH_CONFIDENCE = 0.80
_TASK_TYPE_BY_TOOL_GROUP = {
    "audio_text": "media_extraction",
    "image_text": "media_extraction",
    "video_understanding": "media_extraction",
    "history_retrieval": "history_retrieval",
    "web_research": "web_research",
    "workspace_read": "workspace_read",
    "workspace_write": "code_change",
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
    continuation_type: str = "none"
    confidence: float = 0.0
    method: str = "deterministic"
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
            history=history,
            work_state_summary=work_state_summary,
        ):
            return deterministic
        if provider is None or str(model or "").strip().lower() == "unconfigured":
            return _unresolved_llm_decision("llm unavailable; task context was not inferred")

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
            return _unresolved_llm_decision("llm failed; task context was not inferred")

        llm_decision = _merge_with_deterministic(
            deterministic,
            llm_decision,
            has_active_task=_has_active_task(active_task),
        )
        if llm_decision.confidence < 0.55:
            return _unresolved_llm_decision(
                f"llm confidence too low ({llm_decision.confidence:.2f}); task context was not inferred"
            )
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
        del history, task_intent, work_state_summary
        current = _compact(current_message)
        has_active_task = _has_active_task(active_task)
        if not current or _ACK_RE.match(current):
            return TaskContextDecision(
                continuation_type="ack",
                confidence=0.9,
                reason="current message is an acknowledgement",
            )

        pending_boundary_request = extract_pending_boundary_request(active_task)
        if pending_boundary_request and _is_boundary_switch_confirmation(current):
            return TaskContextDecision(
                should_seed_active_task=True,
                should_replace_active_task=True,
                continuation_type="task_switch",
                confidence=0.9,
                reason="user confirmed switching to the pending task-boundary request",
            )
        if pending_boundary_request and _is_boundary_continue_confirmation(current):
            return TaskContextDecision(
                is_follow_up=True,
                should_inherit_active_task=True,
                continuation_type="continue_active_task",
                confidence=0.9,
                reason="user confirmed continuing the active task after task-boundary prompt",
            )

        if has_active_task and _CONTINUATION_RE.match(current):
            return TaskContextDecision(
                is_follow_up=True,
                should_inherit_active_task=True,
                continuation_type="continue_active_task",
                confidence=0.75,
                reason="current message is a continuation of the active task",
            )

        if _CONTINUATION_RE.match(current):
            return TaskContextDecision(
                is_follow_up=True,
                continuation_type="continue_last_answer",
                confidence=0.45,
                reason="continuation phrase without an active task",
            )

        return TaskContextDecision(
            continuation_type="none",
            confidence=0.45,
            reason="task context requires LLM classification",
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
    history: list[dict[str, Any]] | None = None,
    work_state_summary: str | None = None,
) -> bool:
    current = _compact(current_message)
    if not current or len(current) > 80 or _ACK_RE.match(current):
        return False
    if decision.is_follow_up and decision.inherited_tool_group:
        return True
    if decision.confidence >= 0.7:
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
        "current_message": _truncate(current_message, 600),
        "task_intent": task_intent.to_metadata() if task_intent is not None else None,
        "recent_history": _recent_history(history),
        "active_task": _truncate(active_task, 1800),
        "work_state_summary": _truncate(work_state_summary, 1200),
        "deterministic_decision": deterministic.to_metadata(),
    }
    return (
        "Decide whether the latest user message is a follow-up, continuation, or task switch.\n"
        "Handle multilingual, typo-heavy, shorthand, and code-mixed user turns.\n"
        "Use recent history and ACTIVE_TASK only as context.\n"
        "If evidence should be inherited, choose one inherited_tool_group from: "
        f"{', '.join(sorted(_ALLOWED_TOOL_GROUPS))}.\n"
        "Do not mark a turn as no-tool if it likely asks for external web, media, or workspace evidence.\n"
        "Do not remove evidence or active-task inheritance from deterministic_decision; only add stricter context.\n"
        "If an active task exists and the latest turn might be either a new task or a continuation, use "
        "continuation_type=ambiguous_boundary instead of replacing the task.\n"
        "Return only JSON with these keys: continuation_type, is_follow_up, should_inherit_active_task, "
        "should_seed_active_task, should_replace_active_task, inherited_task_type, inherited_tool_group, "
        "confidence, reason. Use null when no task/tool is inherited.\n"
        "continuation_type must be one of: "
        f"{', '.join(sorted(_ALLOWED_CONTINUATION_TYPES))}.\n\n"
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
    if continuation_type in _FOLLOW_UP_CONTINUATION_TYPES:
        should_replace_active_task = False
        is_follow_up = True
    if continuation_type == "continue_active_task" or (
        has_active_task and continuation_type in _FOLLOW_UP_CONTINUATION_TYPES
    ):
        should_inherit_active_task = True
        is_follow_up = True
    if continuation_type in _NEW_TASK_CONTINUATION_TYPES:
        should_inherit_active_task = False
        should_seed_active_task = True
    if continuation_type == _AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE:
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
        reason=_truncate(str(payload.get("reason") or "llm resolved task context"), 240),
    )


def _merge_with_deterministic(
    deterministic: TaskContextDecision,
    llm_decision: TaskContextDecision,
    *,
    has_active_task: bool = False,
) -> TaskContextDecision:
    """Keep deterministic safety signals when accepting an LLM classification."""
    if deterministic.continuation_type == "ack":
        return replace(llm_decision, continuation_type="ack", is_follow_up=False, should_inherit_active_task=False)

    inherited_tool_group = llm_decision.inherited_tool_group
    inherited_task_type = llm_decision.inherited_task_type
    if inherited_tool_group and inherited_task_type is None:
        inherited_task_type = _TASK_TYPE_BY_TOOL_GROUP.get(inherited_tool_group)

    continuation_type = llm_decision.continuation_type
    if (
        has_active_task
        and continuation_type in _NEW_TASK_CONTINUATION_TYPES
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
            continuation_type=_AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE,
            reason=f"task boundary confidence too low ({llm_decision.confidence:.2f}); ask for confirmation",
        )
    if (
        deterministic.should_inherit_active_task
        and continuation_type not in _NEW_TASK_CONTINUATION_TYPES
        and continuation_type != _AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE
    ):
        continuation_type = "continue_active_task"
    elif inherited_tool_group and continuation_type == "none":
        continuation_type = "follow_up"

    should_replace_active_task = llm_decision.should_replace_active_task and continuation_type in _NEW_TASK_CONTINUATION_TYPES
    should_seed_active_task = llm_decision.should_seed_active_task or should_replace_active_task
    should_inherit_active_task = llm_decision.should_inherit_active_task
    if deterministic.should_inherit_active_task and not should_replace_active_task:
        should_inherit_active_task = True

    is_follow_up = llm_decision.is_follow_up
    if inherited_tool_group or continuation_type in _FOLLOW_UP_CONTINUATION_TYPES:
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


def _unresolved_llm_decision(reason: str) -> TaskContextDecision:
    return TaskContextDecision(
        continuation_type="none",
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
    normalized = str(value or "").strip()
    if not normalized or normalized.lower() in {"none", "null", "n/a"}:
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
    if normalized in _ALLOWED_CONTINUATION_TYPES:
        return normalized
    if should_replace_active_task:
        return "task_switch"
    if should_seed_active_task:
        return "new_task"
    if should_inherit_active_task:
        return "continue_active_task"
    if is_follow_up:
        return "follow_up"
    return "none"


def _has_recent_context(history: list[dict[str, Any]] | None, work_state_summary: str | None) -> bool:
    if _compact(work_state_summary):
        return True
    return any(_compact(str(message.get("content") or "")) for message in (history or [])[-6:])


def _is_context_dependent_short_turn(current: str) -> bool:
    if _CONTINUATION_RE.match(current):
        return True
    words = re.findall(r"[\w\u4e00-\u9fff]+", current)
    if len(words) <= 4:
        return True
    return current.endswith(("?", "\uff1f")) and len(words) <= 8


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
    status = _active_task_status(active_task)
    return status in {"active", "blocked", "waiting_user"}


def _active_task_status(active_task: str | None) -> str:
    match = _ACTIVE_STATUS_RE.search(str(active_task or ""))
    if not match:
        return "inactive"
    return match.group("status").strip().lower() or "inactive"


def extract_pending_boundary_request(active_task: str | None) -> str | None:
    """Return the pending new request from a boundary-confirmation ACTIVE_TASK prompt."""
    if _active_task_status(active_task) != "waiting_user":
        return None
    compact = _compact(active_task)
    for pattern in _BOUNDARY_REQUEST_PATTERNS:
        match = pattern.search(compact)
        if match:
            return _compact(match.group("request")) or None
    return None


def _is_boundary_switch_confirmation(current_message: str) -> bool:
    return bool(_BOUNDARY_SWITCH_CONFIRMATION_RE.match(_compact(current_message)))


def _is_boundary_continue_confirmation(current_message: str) -> bool:
    current = _compact(current_message)
    return bool(_CONTINUATION_RE.match(current) or _BOUNDARY_CONTINUE_CONFIRMATION_RE.match(current))


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: str | None, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


__all__ = ["TaskContextDecision", "TaskContextResolver", "extract_pending_boundary_request"]
