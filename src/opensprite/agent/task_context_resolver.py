"""Hybrid task-context resolution for follow-ups and active-task handoff."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from ..documents.active_task import should_replace_active_task
from ..llms import ChatMessage
from ..utils.log import logger
from .follow_up_intent import FollowUpIntentResolver
from .task_intent import TaskIntent


_ACK_RE = re.compile(
    r"^(?:ok|okay|thanks|thank you|thx|好|好的|了解|知道了|謝謝|謝啦|感謝|不用|先不用)[。.!！?？]*$",
    re.IGNORECASE,
)
_CONTINUATION_RE = re.compile(
    r"^(?:continue|keep going|go on|proceed|繼續|接著|繼續做|繼續處理|繼續吧|往下做)[。.!！?？]*$",
    re.IGNORECASE,
)
_ACTIVE_STATUS_RE = re.compile(r"^- Status:\s*(?P<status>.+)$", re.MULTILINE)
_ALLOWED_TASK_TYPES = frozenset(
    {
        "analysis",
        "code_change",
        "debug",
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
_TASK_TYPE_BY_TOOL_GROUP = {
    "audio_text": "media_extraction",
    "image_text": "media_extraction",
    "video_understanding": "media_extraction",
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
            "confidence": self.confidence,
            "method": self.method,
            "reason": self.reason,
        }


class TaskContextResolver:
    """Resolve whether a turn should inherit recent task context."""

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
        if not _should_consult_llm(current_message, deterministic, active_task, task_intent):
            return deterministic
        if provider is None or str(model or "").strip().lower() == "unconfigured":
            return replace(deterministic, method="fallback", reason=f"llm unavailable; {deterministic.reason}")

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
            return replace(deterministic, method="fallback", reason=f"llm failed; {deterministic.reason}")

        if llm_decision.confidence < 0.55:
            return replace(
                deterministic,
                method="fallback",
                reason=f"llm confidence too low ({llm_decision.confidence:.2f}); {deterministic.reason}",
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
        del task_intent, work_state_summary
        current = _compact(current_message)
        has_active_task = _has_active_task(active_task)
        if not current or _ACK_RE.match(current):
            return TaskContextDecision(confidence=0.9, reason="current message is an acknowledgement")

        if has_active_task and should_replace_active_task(active_task or "", current):
            return TaskContextDecision(
                should_seed_active_task=True,
                should_replace_active_task=True,
                confidence=0.85,
                reason="current message explicitly switches the active task",
            )

        if has_active_task and _CONTINUATION_RE.match(current):
            return TaskContextDecision(
                is_follow_up=True,
                should_inherit_active_task=True,
                confidence=0.75,
                reason="current message is a continuation of the active task",
            )

        follow_up = FollowUpIntentResolver.resolve(current_message=current, history=history)
        if not follow_up.is_follow_up:
            return TaskContextDecision(confidence=0.65, reason=follow_up.reason)

        inherited_task_type = follow_up.inherited_task_type
        inherited_tool_group = follow_up.inherited_tool_group
        should_inherit_active_task = has_active_task and not should_replace_active_task(active_task or "", current)
        return TaskContextDecision(
            is_follow_up=True,
            should_inherit_active_task=should_inherit_active_task,
            inherited_task_type=inherited_task_type,
            inherited_tool_group=inherited_tool_group,
            confidence=follow_up.confidence,
            reason=follow_up.reason,
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
            temperature=0.0,
            max_tokens=500,
        )
        payload = _parse_json_object(str(getattr(response, "content", "") or ""))
        return _decision_from_payload(payload)


def _should_consult_llm(
    current_message: str,
    decision: TaskContextDecision,
    active_task: str | None,
    task_intent: TaskIntent | None,
) -> bool:
    current = _compact(current_message)
    if not current or len(current) > 80 or _ACK_RE.match(current):
        return False
    if decision.confidence >= 0.7:
        return False
    if decision.is_follow_up:
        return True
    if not _has_active_task(active_task):
        return False
    if decision.should_inherit_active_task:
        return True
    return bool(task_intent and task_intent.should_seed_active_task)


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
        "Use recent history and ACTIVE_TASK only as context.\n"
        "If evidence should be inherited, choose one inherited_tool_group from: "
        f"{', '.join(sorted(_ALLOWED_TOOL_GROUPS))}.\n"
        "Do not mark a turn as no-tool if it likely asks for external web, media, or workspace evidence.\n"
        "Return only JSON with these keys: is_follow_up, should_inherit_active_task, "
        "should_seed_active_task, should_replace_active_task, inherited_task_type, inherited_tool_group, "
        "confidence, reason. Use null when no task/tool is inherited.\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _decision_from_payload(payload: dict[str, Any]) -> TaskContextDecision:
    inherited_tool_group = _allowed_string(payload.get("inherited_tool_group"), _ALLOWED_TOOL_GROUPS)
    inherited_task_type = _allowed_string(payload.get("inherited_task_type"), _ALLOWED_TASK_TYPES)
    if inherited_tool_group and inherited_task_type is None:
        inherited_task_type = _TASK_TYPE_BY_TOOL_GROUP.get(inherited_tool_group)
    return TaskContextDecision(
        is_follow_up=_coerce_bool(payload.get("is_follow_up")),
        should_inherit_active_task=_coerce_bool(payload.get("should_inherit_active_task")),
        should_seed_active_task=_coerce_bool(payload.get("should_seed_active_task")),
        should_replace_active_task=_coerce_bool(payload.get("should_replace_active_task")),
        inherited_task_type=inherited_task_type,
        inherited_tool_group=inherited_tool_group,
        confidence=_coerce_confidence(payload.get("confidence")),
        method="llm",
        reason=_truncate(str(payload.get("reason") or "llm resolved task context"), 240),
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


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: str | None, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


__all__ = ["TaskContextDecision", "TaskContextResolver"]
