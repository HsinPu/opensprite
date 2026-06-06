"""Deterministic turn-shape classification for agent turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .harness_profile import (
    ANALYSIS_TASK_TYPE,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    MEDIA_EXTRACTION_TASK_TYPE,
    OPERATIONS_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    VERIFICATION_REQUIREMENT_KIND,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP,
)
from .tool_groups import OPERATION_TOOL_GROUPS
from .web_source_policy import is_web_research_task_type


ANALYSIS_INTENT_KIND = "analysis"
GENERIC_TASK_INTENT_KIND = "task"
REVIEW_INTENT_KIND = "review"
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
WORKFLOW_COMPLETION_INTENT_KINDS = frozenset({ANALYSIS_INTENT_KIND, REVIEW_INTENT_KIND})
PLANNING_ERROR_TASK_TYPE = "planning_error"
COMMAND_PREFIXES = ("/",)
LIST_ITEM_RE = re.compile(r"(?:^|\s)(?:\d+\.|[-*])\s+")
TASK_INTENT_SCHEMA_VERSION = 1
OBJECTIVE_MAX_CHARS = 220
LONG_RUNNING_TEXT_MIN_CHARS = 180
LONG_RUNNING_LIST_ITEM_MIN_COUNT = 2

TASK_INTENT_SCHEMA_VERSION_FIELD = "schema_version"
TASK_INTENT_KIND_FIELD = "kind"
TASK_INTENT_OBJECTIVE_FIELD = "objective"
TASK_INTENT_CONSTRAINTS_FIELD = "constraints"
TASK_INTENT_DONE_CRITERIA_FIELD = "done_criteria"
TASK_INTENT_NEEDS_CLARIFICATION_FIELD = "needs_clarification"
TASK_INTENT_LONG_RUNNING_FIELD = "long_running"
TASK_INTENT_EXPECTS_CODE_CHANGE_FIELD = "expects_code_change"
TASK_INTENT_EXPECTS_VERIFICATION_FIELD = "expects_verification"
TASK_INTENT_VERIFICATION_HINT_FIELD = "verification_hint"

MEDIA_UPLOAD_OBJECTIVE = "Save attached media for later use"
EMPTY_TEXT_OBJECTIVE = "No user text was provided"

DONE_CRITERION_MEDIA_PERSISTED = "attached media is persisted or referenced for follow-up"
DONE_CRITERION_NO_ACTION_REQUIRED = "no action is required unless context indicates otherwise"
DONE_CRITERION_COMMAND_HANDLED = "the command is handled or rejected with a clear reason"
DONE_CRITERION_DIRECT_RESPONSE = "the user request is addressed directly"
DONE_CRITERION_EXPLICIT_RESULT_OR_BLOCKER = "the result or blocker is explicit"
DONE_CRITERION_VERIFICATION_REPORTED = "relevant tests or checks pass, or the verification gap is stated"
DONE_CRITERION_EVIDENCE_TIED_FINDINGS = "findings are tied to concrete evidence"
DONE_CRITERION_RELEVANT_MEDIA_CONSIDERED = "attached media is considered only when relevant to the request"
DONE_CRITERION_NATURAL_RESPONSE = "respond naturally and match the user's tone"
NO_FALLBACK_ACTIVE_TASK_UPDATE_TYPES = frozenset({PURE_ANSWER_TASK_TYPE, PLANNING_ERROR_TASK_TYPE})
READ_ONLY_TASK_TYPES = frozenset(
    {ANALYSIS_TASK_TYPE, OPERATIONS_TASK_TYPE, WORKSPACE_READ_TASK_TYPE, HISTORY_RETRIEVAL_TASK_TYPE}
)
FINAL_RESPONSE_ACCEPTED_TASK_TYPES = frozenset({ANALYSIS_TASK_TYPE, PLANNING_TASK_TYPE, GENERIC_TASK_TYPE})
READ_ONLY_BLOCKING_REQUIREMENT_KINDS = frozenset({FILE_CHANGE_REQUIREMENT_KIND, VERIFICATION_REQUIREMENT_KIND})
READ_ONLY_BLOCKING_TOOL_GROUPS = frozenset(
    {WORKSPACE_WRITE_TOOL_GROUP, VERIFICATION_TOOL_GROUP, *OPERATION_TOOL_GROUPS}
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
    expects_code_change: bool = False
    expects_verification: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe event payload for durable run telemetry."""
        payload: dict[str, Any] = {
            TASK_INTENT_SCHEMA_VERSION_FIELD: TASK_INTENT_SCHEMA_VERSION,
            TASK_INTENT_KIND_FIELD: self.kind,
            TASK_INTENT_OBJECTIVE_FIELD: self.objective,
            TASK_INTENT_CONSTRAINTS_FIELD: list(self.constraints),
            TASK_INTENT_DONE_CRITERIA_FIELD: list(self.done_criteria),
            TASK_INTENT_NEEDS_CLARIFICATION_FIELD: self.needs_clarification,
            TASK_INTENT_LONG_RUNNING_FIELD: self.long_running,
            TASK_INTENT_EXPECTS_CODE_CHANGE_FIELD: self.expects_code_change,
            TASK_INTENT_EXPECTS_VERIFICATION_FIELD: self.expects_verification,
        }
        if self.verification_hint:
            payload[TASK_INTENT_VERIFICATION_HINT_FIELD] = self.verification_hint
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
                    objective=MEDIA_UPLOAD_OBJECTIVE,
                    done_criteria=_done_criteria(MEDIA_UPLOAD_INTENT_KIND, long_running=False, has_media=True),
                    long_running=False,
                )
            return TaskIntent(
                kind=CONVERSATION_INTENT_KIND,
                objective=EMPTY_TEXT_OBJECTIVE,
                done_criteria=(DONE_CRITERION_NO_ACTION_REQUIRED,),
                long_running=False,
            )

        if _is_command_text(compact):
            return TaskIntent(
                kind=COMMAND_INTENT_KIND,
                objective=_truncate(compact),
                done_criteria=_done_criteria(COMMAND_INTENT_KIND, long_running=False, has_media=False),
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


def intent_supports_fallback_active_task_update(task_intent: Any, task_contract: Any) -> bool:
    if getattr(task_intent, "needs_clarification", False):
        return False
    task_type = str(getattr(task_contract, "task_type", "") or "").strip()
    if not task_type:
        return False
    return task_type not in NO_FALLBACK_ACTIVE_TASK_UPDATE_TYPES


def intent_supports_default_work_plan(task_intent: Any) -> bool:
    return str(getattr(task_intent, "kind", "") or "").strip() in {
        ANALYSIS_INTENT_KIND,
        GENERIC_TASK_INTENT_KIND,
    } and not bool(getattr(task_intent, "needs_clarification", False))


def is_read_only_task_type(task_type: str | None) -> bool:
    normalized = str(task_type or "").strip()
    return is_web_research_task_type(normalized) or normalized in READ_ONLY_TASK_TYPES


def is_media_extraction_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == MEDIA_EXTRACTION_TASK_TYPE


def is_history_retrieval_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == HISTORY_RETRIEVAL_TASK_TYPE


def is_workspace_read_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == WORKSPACE_READ_TASK_TYPE


def is_plain_answer_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == PURE_ANSWER_TASK_TYPE


def is_one_turn_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in ONE_TURN_INTENT_KINDS


def is_analysis_response_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == ANALYSIS_INTENT_KIND


def is_generic_task_response_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == GENERIC_TASK_INTENT_KIND


def is_read_only_blocking_requirement_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in READ_ONLY_BLOCKING_REQUIREMENT_KINDS


def is_read_only_blocking_tool_group(tool_group: str | None) -> bool:
    return str(tool_group or "").strip() in READ_ONLY_BLOCKING_TOOL_GROUPS


def accepts_final_response_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() in FINAL_RESPONSE_ACCEPTED_TASK_TYPES


def _truncate(text: str, max_chars: int = OBJECTIVE_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return text[: max_chars - 3].rstrip() + "..."
    marker = " ... [middle omitted] ... "
    remaining = max_chars - len(marker)
    head_chars = max(1, remaining // 2)
    tail_chars = max(1, remaining - head_chars)
    return f"{text[:head_chars].rstrip()}{marker}{text[-tail_chars:].lstrip()}"


def _classify_kind(text: str, *, media_count: int) -> str:
    if media_count:
        return ANALYSIS_INTENT_KIND
    return GENERIC_TASK_INTENT_KIND


def _is_command_text(text: str) -> bool:
    compact = str(text or "").strip()
    return any(compact.startswith(prefix) for prefix in COMMAND_PREFIXES)


def _has_multiple_list_items(text: str) -> bool:
    return len(LIST_ITEM_RE.findall(text)) >= LONG_RUNNING_LIST_ITEM_MIN_COUNT


def _is_long_running(text: str, kind: str) -> bool:
    if kind not in TASK_INTENT_KINDS:
        return False
    if len(text) > LONG_RUNNING_TEXT_MIN_CHARS:
        return True
    if _has_multiple_list_items(text):
        return True
    return False


def _done_criteria(kind: str, *, long_running: bool, has_media: bool) -> tuple[str, ...]:
    if kind == CONVERSATION_INTENT_KIND:
        return (DONE_CRITERION_NATURAL_RESPONSE,)
    if kind == COMMAND_INTENT_KIND:
        return (DONE_CRITERION_COMMAND_HANDLED,)
    if kind == MEDIA_UPLOAD_INTENT_KIND:
        return (DONE_CRITERION_MEDIA_PERSISTED,)

    criteria = [DONE_CRITERION_DIRECT_RESPONSE, DONE_CRITERION_EXPLICIT_RESULT_OR_BLOCKER]
    if long_running:
        criteria.append(DONE_CRITERION_VERIFICATION_REPORTED)
    if kind == ANALYSIS_INTENT_KIND:
        criteria.append(DONE_CRITERION_EVIDENCE_TIED_FINDINGS)
    if has_media:
        criteria.append(DONE_CRITERION_RELEVANT_MEDIA_CONSIDERED)
    return tuple(dict.fromkeys(criteria))
