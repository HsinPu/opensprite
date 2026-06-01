"""LLM-backed completion judge primitives."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import DocumentLlmConfig
from ..llms import ChatMessage


COMPLETION_JUDGE_STATUSES = frozenset(
    {
        "complete",
        "incomplete",
        "blocked",
        "waiting_user",
        "needs_verification",
        "needs_review",
    }
)

COMPLETION_JUDGE_SYSTEM_PROMPT = """You are OpenSprite's completion judge.
You receive structured facts about one agent turn. Decide whether the assistant
completed the user's task. Return only one JSON object matching the requested
schema. Do not include markdown or explanations outside JSON."""


class CompletionJudgeError(RuntimeError):
    """Raised when the completion judge cannot produce a valid verdict."""


@dataclass(frozen=True)
class CompletionJudgeVerdict:
    """Normalized completion verdict returned by the LLM judge."""

    status: str
    reason: str
    active_task_status: str | None = None
    active_task_detail: str | None = None
    follow_up_workflow: str | None = None
    follow_up_step_id: str | None = None
    follow_up_step_label: str | None = None
    follow_up_prompt_type: str | None = None
    verification_action: str | None = None
    verification_path: str | None = None
    verification_pytest_args: tuple[str, ...] = ()
    verification_required: bool = False
    verification_attempted: bool = False
    verification_passed: bool = False
    review_required: bool = False
    review_attempted: bool = False
    review_passed: bool = False
    review_summary: str = ""
    review_prompt_types: tuple[str, ...] = ()
    review_finding_count: int = 0
    missing_evidence: tuple[str, ...] = ()
    raw_response_preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class CompletionJudgeService:
    """Ask the active LLM to produce a structured completion verdict."""

    def __init__(self, llm_config: DocumentLlmConfig):
        self.llm_config = llm_config

    async def judge(
        self,
        *,
        provider: Any,
        model: str | None,
        facts: dict[str, Any],
    ) -> CompletionJudgeVerdict:
        if provider is None or str(model or "").strip().lower() == "unconfigured":
            raise CompletionJudgeError("completion judge unavailable: llm not configured")
        prompt = _build_judge_prompt(facts)
        response = await provider.chat(
            [
                ChatMessage(role="system", content=COMPLETION_JUDGE_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ],
            model=model,
            **self.llm_config.decoding_kwargs(),
        )
        response_text = str(getattr(response, "content", "") or "")
        payload = parse_completion_judge_json(response_text)
        return normalize_completion_judge_payload(payload, raw_response=response_text)


def parse_completion_judge_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from a judge response."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL)
    raw = fenced.group(1) if fenced else str(text or "")
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end >= start:
        raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise CompletionJudgeError("completion judge returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise CompletionJudgeError("completion judge JSON must be an object")
    return parsed


def normalize_completion_judge_payload(
    payload: dict[str, Any],
    *,
    raw_response: str = "",
) -> CompletionJudgeVerdict:
    """Validate and normalize the judge JSON object."""
    status = str(payload.get("status") or "").strip().lower()
    if status not in COMPLETION_JUDGE_STATUSES:
        raise CompletionJudgeError(f"completion judge returned unsupported status: {status or '<empty>'}")
    reason = _coerce_text(payload.get("reason"), max_chars=500)
    if not reason:
        raise CompletionJudgeError("completion judge response is missing reason")
    return CompletionJudgeVerdict(
        status=status,
        reason=reason,
        active_task_status=_optional_text(payload.get("active_task_status"), max_chars=120),
        active_task_detail=_optional_text(payload.get("active_task_detail"), max_chars=1000),
        follow_up_workflow=_optional_text(payload.get("follow_up_workflow"), max_chars=80),
        follow_up_step_id=_optional_text(payload.get("follow_up_step_id"), max_chars=120),
        follow_up_step_label=_optional_text(payload.get("follow_up_step_label"), max_chars=160),
        follow_up_prompt_type=_optional_text(payload.get("follow_up_prompt_type"), max_chars=80),
        verification_action=_optional_text(payload.get("verification_action"), max_chars=80),
        verification_path=_optional_text(payload.get("verification_path"), max_chars=500),
        verification_pytest_args=tuple(_string_list(payload.get("verification_pytest_args"), max_items=20, max_chars=200)),
        verification_required=_coerce_bool(payload.get("verification_required")),
        verification_attempted=_coerce_bool(payload.get("verification_attempted")),
        verification_passed=_coerce_bool(payload.get("verification_passed")),
        review_required=_coerce_bool(payload.get("review_required")),
        review_attempted=_coerce_bool(payload.get("review_attempted")),
        review_passed=_coerce_bool(payload.get("review_passed")),
        review_summary=_coerce_text(payload.get("review_summary"), max_chars=1000),
        review_prompt_types=tuple(_string_list(payload.get("review_prompt_types"), max_items=10, max_chars=80)),
        review_finding_count=_coerce_non_negative_int(payload.get("review_finding_count")),
        missing_evidence=tuple(_string_list(payload.get("missing_evidence"), max_items=20, max_chars=240)),
        raw_response_preview=_truncate(raw_response, max_chars=600),
        metadata={"method": "llm"},
    )


def _build_judge_prompt(facts: dict[str, Any]) -> str:
    schema = {
        "status": "complete|incomplete|blocked|waiting_user|needs_verification|needs_review",
        "reason": "short reason",
        "active_task_status": "done|in_progress|blocked|null",
        "active_task_detail": "optional detail",
        "missing_evidence": ["optional missing items"],
        "verification_required": False,
        "verification_attempted": False,
        "verification_passed": False,
        "review_required": False,
        "review_attempted": False,
        "review_passed": False,
        "review_summary": "",
        "review_prompt_types": [],
        "review_finding_count": 0,
    }
    return (
        "Judge this agent turn using only the structured facts below. "
        "Return only JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Facts:\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2, default=str)}"
    )


def _optional_text(value: Any, *, max_chars: int) -> str | None:
    text = _coerce_text(value, max_chars=max_chars)
    return text or None


def _coerce_text(value: Any, *, max_chars: int) -> str:
    return _truncate(str(value or "").strip(), max_chars=max_chars)


def _truncate(text: str, *, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _coerce_non_negative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _string_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in values:
        text = _coerce_text(item, max_chars=max_chars)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out
