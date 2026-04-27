"""Deterministic user intent classification for agent turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_TASK_KINDS = {
    "analysis",
    "debug",
    "implementation",
    "planning",
    "refactor",
    "review",
    "task",
    "writing",
}
_NON_TASK_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "got it",
    "cool",
    "nice",
}
_REQUEST_MARKERS = (
    "help me",
    "can you",
    "could you",
    "please",
    "need you to",
    "i want you to",
    "let's",
    "幫我",
    "請",
    "麻煩",
)
_QUESTION_MARKERS = (
    "what",
    "why",
    "how",
    "which",
    "where",
    "when",
    "who",
    "什麼",
    "為什麼",
    "如何",
    "怎麼",
    "哪個",
    "是否",
    "可以嗎",
)
_VAGUE_TASK_MESSAGES = {
    "continue",
    "keep going",
    "do it",
    "fix it",
    "handle it",
    "make it better",
    "處理一下",
    "幫我處理",
    "繼續",
    "修一下",
    "搞定",
}
_LONG_RUNNING_MARKERS = (
    "complete",
    "end-to-end",
    "full",
    "long running",
    "long-running",
    "multi-step",
    "continue",
    "keep going",
    "tests",
    "verify",
    "build",
    "implement",
    "refactor",
    "debug",
    "investigate",
    "處理完整",
    "完整",
    "長時間",
    "繼續",
    "驗證",
    "測試",
    "實作",
    "重構",
    "除錯",
    "調查",
)
_CONSTRAINT_MARKERS = (
    "do not",
    "don't",
    "without",
    "only",
    "keep",
    "must",
    "不要",
    "別",
    "只能",
    "只要",
    "必須",
    "保持",
)
_KIND_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("refactor", ("refactor", "cleanup", "clean up", "重構", "整理程式", "整理代碼")),
    ("debug", ("debug", "bug", "error", "exception", "traceback", "failed", "failure", "fix", "修正", "修復", "除錯", "錯誤", "失敗")),
    ("review", ("review", "audit", "code review", "檢視", "審查")),
    ("analysis", ("analyze", "analyse", "investigate", "inspect", "check", "look into", "分析", "調查", "檢查", "看一下")),
    ("implementation", ("implement", "add", "build", "create", "update", "change", "write code", "實作", "新增", "建立", "更新", "修改")),
    ("writing", ("write", "draft", "summarize", "summary", "rewrite", "撰寫", "草擬", "摘要", "總結", "重寫")),
    ("planning", ("plan", "organize", "design", "規劃", "設計")),
)


@dataclass(frozen=True)
class TaskIntent:
    """A compact, durable description of what the user appears to want."""

    kind: str
    objective: str
    constraints: tuple[str, ...] = ()
    done_criteria: tuple[str, ...] = ()
    needs_clarification: bool = False
    verification_hint: str | None = None
    long_running: bool = False

    @property
    def should_seed_active_task(self) -> bool:
        """Return whether this intent is specific enough to start ACTIVE_TASK."""
        return self.kind in _TASK_KINDS and not self.needs_clarification

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe event payload for durable run telemetry."""
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": self.kind,
            "objective": self.objective,
            "constraints": list(self.constraints),
            "done_criteria": list(self.done_criteria),
            "needs_clarification": self.needs_clarification,
            "long_running": self.long_running,
        }
        if self.verification_hint:
            payload["verification_hint"] = self.verification_hint
        return payload


class TaskIntentService:
    """Classify a user turn without calling the LLM."""

    def classify(
        self,
        text: str | None,
        *,
        images: list[str] | None = None,
        audios: list[str] | None = None,
        videos: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskIntent:
        """Infer the user's intent from text, attachments, and channel metadata."""
        del metadata
        compact = _compact_text(text)
        media_count = len(images or []) + len(audios or []) + len(videos or [])
        if not compact:
            if media_count:
                return TaskIntent(
                    kind="media_upload",
                    objective="Save attached media for later use",
                    done_criteria=("attached media is persisted or referenced for follow-up",),
                    long_running=False,
                )
            return TaskIntent(
                kind="conversation",
                objective="No user text was provided",
                done_criteria=("no action is required unless context indicates otherwise",),
                long_running=False,
            )

        lowered = compact.lower()
        if lowered in _NON_TASK_MESSAGES:
            return TaskIntent(
                kind="conversation",
                objective=compact,
                done_criteria=("respond naturally and briefly",),
                long_running=False,
            )
        if compact.startswith("/"):
            return TaskIntent(
                kind="command",
                objective=_truncate(compact),
                done_criteria=("the command is handled or rejected with a clear reason",),
                long_running=False,
            )

        kind = _classify_kind(compact, media_count=media_count)
        needs_clarification = _needs_clarification(compact, kind)
        long_running = _is_long_running(compact, kind)
        constraints = _extract_constraints(compact)
        done_criteria = _done_criteria(kind, long_running=long_running, has_media=media_count > 0)
        verification_hint = _verification_hint(kind, compact)

        return TaskIntent(
            kind=kind,
            objective=_truncate(compact),
            constraints=constraints,
            done_criteria=done_criteria,
            needs_clarification=needs_clarification,
            verification_hint=verification_hint,
            long_running=long_running,
        )


def _compact_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate(text: str, max_chars: int = 220) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _looks_like_question(text: str) -> bool:
    lowered = text.lower()
    return text.endswith(("?", "？")) or any(marker in lowered for marker in _QUESTION_MARKERS)


def _classify_kind(text: str, *, media_count: int) -> str:
    lowered = text.lower()
    has_request_marker = _has_marker(text, _REQUEST_MARKERS)
    matched_kind = "conversation"
    for kind, markers in _KIND_MARKERS:
        if any(marker in lowered for marker in markers):
            matched_kind = kind
            break

    if matched_kind != "conversation":
        return matched_kind
    if media_count and (_looks_like_question(text) or has_request_marker):
        return "analysis"
    if _looks_like_question(text):
        return "question"
    if has_request_marker:
        return "task"
    return "conversation"


def _needs_clarification(text: str, kind: str) -> bool:
    lowered = text.lower().strip(" .!?？")
    if kind not in _TASK_KINDS:
        return False
    if lowered in _VAGUE_TASK_MESSAGES:
        return True
    words = re.findall(r"[\w\u4e00-\u9fff]+", lowered)
    if len(words) <= 2 and any(marker in lowered for _, markers in _KIND_MARKERS for marker in markers):
        return True
    return False


def _is_long_running(text: str, kind: str) -> bool:
    if kind not in _TASK_KINDS:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _LONG_RUNNING_MARKERS):
        return True
    if len(text) > 180:
        return True
    if len(re.findall(r"(?:^|\s)(?:\d+\.|[-*])\s+", text)) >= 2:
        return True
    return kind in {"debug", "implementation", "refactor"}


def _extract_constraints(text: str) -> tuple[str, ...]:
    chunks = re.split(r"(?<=[.!?。！？])\s+", text)
    constraints: list[str] = []
    for chunk in chunks:
        compact = _compact_text(chunk)
        if not compact:
            continue
        if any(marker in compact.lower() for marker in _CONSTRAINT_MARKERS):
            constraints.append(_truncate(compact, max_chars=160))
    return tuple(dict.fromkeys(constraints[:4]))


def _done_criteria(kind: str, *, long_running: bool, has_media: bool) -> tuple[str, ...]:
    if kind == "question":
        return ("the answer is clear and directly addresses the question",)
    if kind == "conversation":
        return ("respond naturally and match the user's tone",)
    if kind == "command":
        return ("the command is handled or rejected with a clear reason",)
    if kind == "media_upload":
        return ("attached media is persisted or referenced for follow-up",)

    criteria = ["the user request is addressed directly", "the result or blocker is explicit"]
    if kind in {"debug", "implementation", "refactor"} or long_running:
        criteria.append("relevant tests or checks pass, or the verification gap is stated")
    if kind in {"analysis", "review"}:
        criteria.append("findings are tied to concrete evidence")
    if kind == "writing":
        criteria.append("the requested draft or revision is provided")
    if kind == "planning":
        criteria.append("next steps are concrete and ordered")
    if has_media:
        criteria.append("attached media is considered only when relevant to the request")
    return tuple(dict.fromkeys(criteria))


def _verification_hint(kind: str, text: str) -> str | None:
    lowered = text.lower()
    if any(marker in lowered for marker in ("test", "verify", "build", "測試", "驗證")):
        return "Run the requested verification and report pass or fail."
    if kind in {"debug", "implementation", "refactor"}:
        return "Run relevant tests or checks before marking the task complete."
    if kind in {"analysis", "review"}:
        return "Validate findings against the referenced files, data, or conversation evidence."
    return None
