"""Deterministic turn-shape classification for agent turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


ANALYSIS_INTENT_KIND = "analysis"
GENERIC_TASK_INTENT_KIND = "task"
CONVERSATION_INTENT_KIND = "conversation"
COMMAND_INTENT_KIND = "command"
MEDIA_UPLOAD_INTENT_KIND = "media_upload"
QUESTION_INTENT_KIND = "question"
ONE_TURN_INTENT_KINDS = frozenset(
    {
        CONVERSATION_INTENT_KIND,
        QUESTION_INTENT_KIND,
        COMMAND_INTENT_KIND,
        MEDIA_UPLOAD_INTENT_KIND,
    }
)
TASK_INTENT_KINDS = frozenset({ANALYSIS_INTENT_KIND, GENERIC_TASK_INTENT_KIND})


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
    expects_code_change: bool = False
    expects_verification: bool = False

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
            "expects_code_change": self.expects_code_change,
            "expects_verification": self.expects_verification,
        }
        if self.verification_hint:
            payload["verification_hint"] = self.verification_hint
        return payload


class TaskIntentService:
    """Classify stable turn shape without inferring semantic task type."""

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
                    kind=MEDIA_UPLOAD_INTENT_KIND,
                    objective="Save attached media for later use",
                    done_criteria=("attached media is persisted or referenced for follow-up",),
                    long_running=False,
                )
            return TaskIntent(
                kind=CONVERSATION_INTENT_KIND,
                objective="No user text was provided",
                done_criteria=("no action is required unless context indicates otherwise",),
                long_running=False,
            )

        if compact.startswith("/"):
            return TaskIntent(
                kind=COMMAND_INTENT_KIND,
                objective=_truncate(compact),
                done_criteria=("the command is handled or rejected with a clear reason",),
                long_running=False,
            )

        kind = _classify_kind(compact, media_count=media_count)
        long_running = _is_long_running(compact, kind)
        done_criteria = _done_criteria(kind, long_running=long_running, has_media=media_count > 0)

        return TaskIntent(
            kind=kind,
            objective=_truncate(compact),
            constraints=(),
            done_criteria=done_criteria,
            verification_hint=None,
            long_running=long_running,
            expects_code_change=False,
            expects_verification=False,
        )


def _compact_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate(text: str, max_chars: int = 220) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _classify_kind(text: str, *, media_count: int) -> str:
    if media_count:
        return ANALYSIS_INTENT_KIND
    return GENERIC_TASK_INTENT_KIND


def _is_long_running(text: str, kind: str) -> bool:
    if kind not in TASK_INTENT_KINDS:
        return False
    if len(text) > 180:
        return True
    if len(re.findall(r"(?:^|\s)(?:\d+\.|[-*])\s+", text)) >= 2:
        return True
    return False


def _done_criteria(kind: str, *, long_running: bool, has_media: bool) -> tuple[str, ...]:
    if kind == CONVERSATION_INTENT_KIND:
        return ("respond naturally and match the user's tone",)
    if kind == COMMAND_INTENT_KIND:
        return ("the command is handled or rejected with a clear reason",)
    if kind == MEDIA_UPLOAD_INTENT_KIND:
        return ("attached media is persisted or referenced for follow-up",)

    criteria = ["the user request is addressed directly", "the result or blocker is explicit"]
    if long_running:
        criteria.append("relevant tests or checks pass, or the verification gap is stated")
    if kind == ANALYSIS_INTENT_KIND:
        criteria.append("findings are tied to concrete evidence")
    if has_media:
        criteria.append("attached media is considered only when relevant to the request")
    return tuple(dict.fromkeys(criteria))
