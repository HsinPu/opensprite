"""Task contracts and evidence requirements for completion checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..documents.active_task import has_current_active_task
from ..llms import ChatMessage, is_unconfigured_llm
from ..utils.log import logger
from ..tool_names import (
    ANALYZE_IMAGE_TOOL_NAME,
    ANALYZE_VIDEO_TOOL_NAME,
    BATCH_TOOL_NAME,
    GLOB_FILES_TOOL_NAME,
    GREP_FILES_TOOL_NAME,
    LIST_DIR_TOOL_NAME,
    LIST_RUN_FILE_CHANGES_TOOL_NAME,
    OCR_IMAGE_TOOL_NAME,
    PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
    TRANSCRIBE_AUDIO_TOOL_NAME,
)
from ..harness import (
    ANALYSIS_TASK_TYPE,
    CODE_CHANGE_TASK_TYPE,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP,
    MEDIA_EXTRACTION_TASK_TYPE,
    MEDIA_TOOL_GROUP,
    OPERATIONS_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    OPERATION_TOOL_GROUPS,
    TASK_TYPE_BY_TOOL_GROUP,
    TOOL_GROUPS,
    VERIFICATION_REQUIREMENT_KIND,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_CHANGE_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP,
    WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP,
    is_planning_task_type,
)
from ..media import MEDIA_ONLY_HISTORY_MARKER
from ..context.message_history import HISTORY_SEARCH_TOOL_NAME
from ..tools.evidence import (
    SOURCE_ARTIFACT_CRITERION_KIND,
    SOURCE_DETAIL_CRITERION_KIND,
    SOURCE_REFERENCE_CRITERION_KIND,
    WEB_RESEARCH_TASK_TYPE,
    WEB_RESEARCH_TOOL_GROUP,
    WEB_SOURCE_EVIDENCE_TOOLS,
    is_web_research_task_type,
    is_web_research_tool_group,
)
from ..tools.evidence import ToolEvidence
from ..tools.registry import ToolRegistry

# Task intent, context, and objective resolution.

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
                objective=_truncate_intent_objective(compact),
                done_criteria=_done_criteria(COMMAND_INTENT_KIND, long_running=False, has_media=False),
                long_running=False,
            )

        kind = _classify_kind(compact, media_count=media_count)
        long_running = _is_long_running(compact, kind)
        done_criteria = _done_criteria(kind, long_running=long_running, has_media=media_count > 0)

        return TaskIntent(
            kind=kind,
            objective=_truncate_intent_objective(compact),
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


def _truncate_intent_objective(text: str, max_chars: int = OBJECTIVE_MAX_CHARS) -> str:
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
    return str(value or "").strip() in ALLOWED_CONTINUATION_TYPES


def llm_string_or_none(value: object) -> str | None:
    normalized = str(value or "").strip()
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
    return str(value or "").strip() in FOLLOW_UP_CONTINUATION_TYPES


def is_new_task_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() in NEW_TASK_CONTINUATION_TYPES


def is_current_task_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() in CURRENT_TASK_CONTINUATION_TYPES


def is_current_task_replacement_type(value: str | None) -> bool:
    return str(value or "").strip() in CURRENT_TASK_REPLACEMENT_TYPES


def is_objective_resolution_skip_type(value: str | None) -> bool:
    return str(value or "").strip() in OBJECTIVE_RESOLUTION_SKIP_CONTINUATION_TYPES


def is_objective_resolution_enrichable_type(value: str | None) -> bool:
    return str(value or "").strip() in OBJECTIVE_RESOLUTION_ENRICHABLE_CONTINUATION_TYPES


def is_ambiguous_boundary_continuation_type(value: str | None) -> bool:
    return str(value or "").strip() == AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE


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
        task_intent: TaskIntent | None = None,
        active_task: str | None = None,
        work_state_summary: str | None = None,
    ) -> TaskContextDecision:
        del history
        current = _resolver_compact(current_message)
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
        prompt = _build_task_context_llm_prompt(
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
        payload = _resolver_parse_json_object(str(getattr(response, "content", "") or ""))
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
        task_intent: TaskIntent | None = None,
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
        task_intent: TaskIntent | None,
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
        payload = _resolver_parse_json_object(str(getattr(response, "content", "") or ""))
        return _task_objective_decision_from_payload(payload, current_message=current_message)


def _should_consult_llm(
    current_message: str,
    decision: TaskContextDecision,
    active_task: str | None,
    task_intent: TaskIntent | None = None,
    history: list[dict[str, Any]] | None = None,
    work_state_summary: str | None = None,
) -> bool:
    current = _resolver_compact(current_message)
    if not current or (task_intent is not None and task_intent.kind == CONVERSATION_INTENT_KIND):
        return False
    if decision.is_follow_up and decision.inherited_tool_group:
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
    task_intent: TaskIntent | None,
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


def _build_task_objective_llm_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    task_intent: TaskIntent | None,
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
    inherited_tool_group = _resolver_allowed_string(payload.get("inherited_tool_group"), _ALLOWED_TOOL_GROUPS)
    inherited_task_type = _resolver_allowed_string(payload.get("inherited_task_type"), _ALLOWED_TASK_TYPES)
    if inherited_tool_group and inherited_task_type is None:
        inherited_task_type = _TASK_TYPE_BY_TOOL_GROUP.get(inherited_tool_group)
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
        inherited_tool_group = None
    return TaskContextDecision(
        is_follow_up=is_follow_up,
        should_inherit_active_task=should_inherit_active_task,
        should_seed_active_task=should_seed_active_task,
        should_replace_active_task=should_replace_active_task,
        inherited_task_type=inherited_task_type,
        inherited_tool_group=inherited_tool_group,
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


def _should_resolve_objective(
    *,
    current_message: str,
    history: list[dict[str, Any]] | None,
    task_intent: TaskIntent | None,
    task_context_decision: TaskContextDecision | None,
    active_task: str | None,
    work_state_summary: str | None,
) -> bool:
    current = _resolver_compact(current_message)
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
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _resolver_coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, confidence))


def _resolver_compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _resolver_truncate(value: str | None, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _resolver_truncate_middle(value: str | None, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return _resolver_truncate(text, max_chars)
    marker = "\n... [middle omitted] ...\n"
    remaining = max_chars - len(marker)
    head_chars = max(1, remaining // 2)
    tail_chars = max(1, remaining - head_chars)
    return f"{text[:head_chars].rstrip()}{marker}{text[-tail_chars:].lstrip()}"

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_MEDIA_HISTORY_RE = re.compile(r"^(Images|Audios|Videos):\s*(?P<paths>.+)$", re.IGNORECASE | re.MULTILINE)
_CURRENT_IMAGE_RE = re.compile(r"User attached (?P<count>\d+) image", re.IGNORECASE)
_CURRENT_AUDIO_RE = re.compile(r"User attached (?P<count>\d+) audio", re.IGNORECASE)
_CURRENT_VIDEO_RE = re.compile(r"User attached (?P<count>\d+) video", re.IGNORECASE)
_RESOURCE_INDEX_PREFIX = {"image": "image_index", "audio": "audio_index", "video": "video_index"}
PLANNER_VALIDATED_STATUS = "validated"
PLANNER_BLOCKED_STATUS = "blocked"
PLANNER_INVALID_STATUS = "invalid"
PLANNER_MISSING_STATUS = "missing"
PLANNER_METADATA_STATUS_FIELD = "planner_status"
PLANNER_METADATA_REASON_FIELD = "reason"
PLANNER_METADATA_RAW_RESPONSE_PREVIEW_FIELD = "raw_response_preview"
DETERMINISTIC_CONTRACT_SOURCE = "deterministic"
DETERMINISTIC_CONTRACT_SOURCES = (DETERMINISTIC_CONTRACT_SOURCE,)
LLM_PLANNER_CONTRACT_SOURCE = "llm_planner"
LLM_PLANNER_CONTRACT_SOURCES = (LLM_PLANNER_CONTRACT_SOURCE,)
MISSING_RUNTIME_CONTRACT_SOURCE = "missing_runtime_contract"
MISSING_RUNTIME_CONTRACT_SOURCES = (MISSING_RUNTIME_CONTRACT_SOURCE,)
MISSING_RUNTIME_CONTRACT_REASON = "execution result did not include a task contract"
PLANNER_UNAVAILABLE_REASON = "task planner unavailable: llm not configured"
PLANNER_INVALID_JSON_REASON = "task planner returned invalid JSON"
PLANNER_UNSUPPORTED_TASK_TYPE_REASON = "task planner returned an unsupported or missing task_type"
PLANNER_VALIDATED_REASON = "llm planner returned a task contract"
PLANNER_MEDIA_ANALYSIS_TASK_TYPE = "media_analysis"
PLANNER_OPS_TASK_TYPE = "ops"
TOOL_GROUP_REQUIREMENT_KIND = "tool_group"
RESOURCE_COVERAGE_REQUIREMENT_KIND = "resource_coverage"
ALL_RESOURCE_COVERAGE = "all"
ITEMIZED_OUTPUT_CRITERION_KIND = "itemized_output"
SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND = "substantive_final_answer"
WORKSPACE_LOCATION_CRITERION_KIND = "workspace_location"
MEDIA_ARTIFACT_CRITERION_KIND = "media_artifact"
VERIFICATION_OR_GAP_CRITERION_KIND = "verification_or_gap"
OPERATION_REPORT_CRITERION_KIND = "operation_report"
COMMAND_VERSION_QUALITY_CHECK = "command_version"
REPOSITORY_STATUS_QUALITY_CHECK = "repository_status"
WORKSPACE_LOCATION_QUALITY_CHECK = "workspace_location"
_ALLOWED_PLANNER_TOOL_GROUPS = frozenset(TOOL_GROUPS.keys())
_ALLOWED_PLANNER_QUALITY_CHECKS = frozenset(
    {
        COMMAND_VERSION_QUALITY_CHECK,
        REPOSITORY_STATUS_QUALITY_CHECK,
        WORKSPACE_LOCATION_QUALITY_CHECK,
    }
)
_ALLOWED_PLANNER_TASK_TYPES = frozenset(
    {
        PURE_ANSWER_TASK_TYPE,
        WEB_RESEARCH_TASK_TYPE,
        WORKSPACE_READ_TASK_TYPE,
        WORKSPACE_CHANGE_TASK_TYPE,
        CODE_CHANGE_TASK_TYPE,
        PLANNER_MEDIA_ANALYSIS_TASK_TYPE,
        MEDIA_EXTRACTION_TASK_TYPE,
        PLANNING_TASK_TYPE,
        HISTORY_RETRIEVAL_TASK_TYPE,
        PLANNER_OPS_TASK_TYPE,
        OPERATIONS_TASK_TYPE,
        GENERIC_TASK_TYPE,
        ANALYSIS_TASK_TYPE,
    }
)
_PLANNER_TASK_TYPE_ALIASES = {
    WORKSPACE_CHANGE_TASK_TYPE: CODE_CHANGE_TASK_TYPE,
    PLANNER_MEDIA_ANALYSIS_TASK_TYPE: MEDIA_EXTRACTION_TASK_TYPE,
    PLANNER_OPS_TASK_TYPE: OPERATIONS_TASK_TYPE,
}
_PLANNER_TOOL_GROUP_ALIASES = {
    WORKSPACE_CHANGE_TASK_TYPE: WORKSPACE_WRITE_TOOL_GROUP,
    PLANNER_MEDIA_ANALYSIS_TASK_TYPE: MEDIA_TOOL_GROUP,
    PLANNER_OPS_TASK_TYPE: VERIFICATION_TOOL_GROUP,
}
LEGACY_FILE_CHANGE_TASK_TYPE_ALIASES = frozenset({"implementation", "refactor"})
FILE_CHANGE_TASK_TYPES = frozenset({CODE_CHANGE_TASK_TYPE, *LEGACY_FILE_CHANGE_TASK_TYPE_ALIASES})
_TASK_TYPE_REQUIRED_TOOL_GROUPS = {
    WEB_RESEARCH_TASK_TYPE: (WEB_RESEARCH_TOOL_GROUP,),
    WORKSPACE_READ_TASK_TYPE: (WORKSPACE_READ_TOOL_GROUP,),
    CODE_CHANGE_TASK_TYPE: (WORKSPACE_READ_TOOL_GROUP, WORKSPACE_WRITE_TOOL_GROUP),
    MEDIA_EXTRACTION_TASK_TYPE: (MEDIA_TOOL_GROUP,),
    HISTORY_RETRIEVAL_TASK_TYPE: (HISTORY_RETRIEVAL_TOOL_GROUP,),
}
_TASK_PLANNER_SYSTEM_PROMPT = (
    "You are the OpenSprite task planner. Decide what tool evidence the latest user turn needs "
    "before the main assistant sees tools. Return only one JSON object. Do not include markdown. "
    "Choose the smallest necessary set from the available runtime capabilities supplied in the user prompt. "
    "If no tool-backed evidence is needed, use pure_answer and an empty required_tool_groups array. "
    "The JSON keys are: objective, task_type, required_tool_groups, final_answer_required, allow_no_tool_final, reason."
)
_PLANNER_REPAIR_SYSTEM_PROMPT = (
    "You repair OpenSprite task planner output. Convert the invalid planner response into exactly one "
    "valid JSON object for the same schema. Return JSON only, no markdown, no explanation."
)
_UNGROUPED_TOOL_PREFIX = "tool:"
_MAX_TOOL_DESCRIPTION_CHARS = 220
PLANNING_ALLOWED_TOOLS = frozenset(
    {
        READ_FILE_TOOL_NAME,
        LIST_DIR_TOOL_NAME,
        GLOB_FILES_TOOL_NAME,
        GREP_FILES_TOOL_NAME,
        BATCH_TOOL_NAME,
        READ_SKILL_TOOL_NAME,
        HISTORY_SEARCH_TOOL_NAME,
        LIST_RUN_FILE_CHANGES_TOOL_NAME,
        PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
        *WEB_SOURCE_EVIDENCE_TOOLS,
        ANALYZE_IMAGE_TOOL_NAME,
        OCR_IMAGE_TOOL_NAME,
        TRANSCRIBE_AUDIO_TOOL_NAME,
        ANALYZE_VIDEO_TOOL_NAME,
    }
)


@dataclass(frozen=True)
class PlannerCapability:
    """One planner-visible capability derived from runtime tools."""

    id: str
    task_type: str
    tools: tuple[str, ...]
    tool_summaries: tuple[dict[str, str], ...] = ()
    risk_levels: tuple[str, ...] = ()

    def to_prompt_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "tools": list(self.tool_summaries) or [{"name": name} for name in self.tools],
            "risk_levels": list(self.risk_levels),
        }


@dataclass(frozen=True)
class PlannerCapabilityCatalog:
    """Planner-facing view of capabilities available in the current runtime."""

    capabilities: tuple[PlannerCapability, ...]

    @property
    def tool_group_ids(self) -> tuple[str, ...]:
        return tuple(capability.id for capability in self.capabilities)

    @property
    def task_types(self) -> tuple[str, ...]:
        values = [PURE_ANSWER_TASK_TYPE, PLANNING_TASK_TYPE, GENERIC_TASK_TYPE, ANALYSIS_TASK_TYPE]
        for capability in self.capabilities:
            if capability.task_type not in values:
                values.append(capability.task_type)
        return tuple(values)

    @property
    def capability_tools(self) -> dict[str, tuple[str, ...]]:
        return {capability.id: capability.tools for capability in self.capabilities}

    def tools_for_group(self, tool_group: str) -> tuple[str, ...]:
        return self.capability_tools.get(str(tool_group or "").strip(), ())

    def to_prompt_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "available_task_types": list(self.task_types),
            "available_capabilities": [capability.to_prompt_metadata() for capability in self.capabilities],
        }


@dataclass(frozen=True)
class PlanningModeState:
    """Resolved planning-mode state for one user turn."""

    enabled: bool = False
    overlay: str = ""
    tool_registry: ToolRegistry | None = None


def build_planner_capability_catalog(tool_registry: ToolRegistry | None = None) -> PlannerCapabilityCatalog:
    """Build a planner capability catalog from current runtime tools."""
    if tool_registry is None:
        return _catalog_from_static_tool_groups()
    available_tools = _available_tools(tool_registry)
    group_to_tools: dict[str, list[Any]] = {group: [] for group in TOOL_GROUPS}
    dynamic_group_order: list[str] = []
    for tool in available_tools:
        groups = set(_known_groups_for_tool(tool.name))
        groups.update(_declared_capability_groups(tool))
        if not groups:
            groups.add(f"{_UNGROUPED_TOOL_PREFIX}{tool.name}")
        for group in groups:
            if group not in group_to_tools:
                group_to_tools[group] = []
                dynamic_group_order.append(group)
            group_to_tools[group].append(tool)

    capabilities: list[PlannerCapability] = []
    for group in (*TOOL_GROUPS.keys(), *dynamic_group_order):
        tools = group_to_tools.get(group) or []
        if not tools:
            continue
        capabilities.append(_capability_from_tools(group, tools))
    return PlannerCapabilityCatalog(tuple(capabilities))


def _catalog_from_static_tool_groups() -> PlannerCapabilityCatalog:
    capabilities = [
        PlannerCapability(
            id=group,
            task_type=TASK_TYPE_BY_TOOL_GROUP.get(group, GENERIC_TASK_TYPE),
            tools=tuple(sorted(tools)),
            tool_summaries=tuple({"name": name} for name in sorted(tools)),
        )
        for group, tools in TOOL_GROUPS.items()
    ]
    return PlannerCapabilityCatalog(tuple(capabilities))


def _available_tools(tool_registry: ToolRegistry) -> list[Any]:
    exposed_names = set(tool_registry.tool_names)
    return [
        tool
        for tool in tool_registry.registered_tools()
        if tool.name in exposed_names
    ]


def _known_groups_for_tool(tool_name: str) -> tuple[str, ...]:
    return tuple(
        group
        for group, tool_names in TOOL_GROUPS.items()
        if tool_name in tool_names
    )


def _declared_capability_groups(tool: Any) -> tuple[str, ...]:
    raw = getattr(tool, "capability_groups", None)
    if callable(raw):
        raw = raw()
    if raw is None:
        return ()
    groups: list[str] = []
    for item in raw:
        group = str(item or "").strip()
        if group and group not in groups:
            groups.append(group)
    return tuple(groups)


def _capability_from_tools(group: str, tools: list[Any]) -> PlannerCapability:
    tool_names = tuple(dict.fromkeys(tool.name for tool in tools))
    risk_levels = sorted({
        risk
        for tool in tools
        for risk in (tool.risk_levels or ())
    })
    return PlannerCapability(
        id=group,
        task_type=TASK_TYPE_BY_TOOL_GROUP.get(group, GENERIC_TASK_TYPE),
        tools=tool_names,
        tool_summaries=tuple(_tool_summary(tool) for tool in tools),
        risk_levels=tuple(risk_levels),
    )


def _tool_summary(tool: Any) -> dict[str, str]:
    description = str(getattr(tool, "description", "") or "").strip()
    summary = {
        "name": str(getattr(tool, "name", "") or ""),
    }
    if description:
        summary["description"] = _truncate_tool_description(description, _MAX_TOOL_DESCRIPTION_CHARS)
    return summary


def _truncate_tool_description(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def build_planning_mode_overlay() -> str:
    """Return the temporary system overlay for contract-selected planning turns."""
    return """# Planning Mode

The task contract selected read-only planning mode for this turn.

- You MUST NOT edit files, apply patches, write files, run exec/process/verify, change configuration, save memory, schedule jobs, delegate subagents, or cause external side effects.
- Use only inspection, retrieval, and research actions to understand the current state.
- Focus on clarifying scope, identifying risks, and producing a concrete implementation plan grounded in real workspace evidence.
- Ask at most one short blocking question only when a missing decision prevents a useful plan.
- Your response should end with either a concise implementation plan or one concise blocker question.

This planning-mode restriction overrides normal workspace autonomy for this turn.
"""


def resolve_planning_mode(
    *,
    base_registry: ToolRegistry | None = None,
    task_contract: "TaskContract | None" = None,
) -> PlanningModeState:
    """Resolve the full planning-mode state for one user turn."""
    if not _contract_requests_planning_mode(task_contract):
        return PlanningModeState()
    return PlanningModeState(
        enabled=True,
        overlay=build_planning_mode_overlay(),
        tool_registry=(
            build_planning_mode_tool_registry(base_registry)
            if base_registry is not None
            else None
        ),
    )


def build_planning_mode_tool_registry(base_registry: ToolRegistry) -> ToolRegistry:
    """Return a read-only registry used for plan-only turns."""
    from ..tools.access import ToolAccessResolver, planning_mode_permission_policy

    resolution = ToolAccessResolver().resolve_overlay(
        base_registry,
        overlay_policy=planning_mode_permission_policy(PLANNING_ALLOWED_TOOLS),
        include_names=PLANNING_ALLOWED_TOOLS,
        metadata_kind="planning_mode",
    )
    return resolution.registry


def _contract_requests_planning_mode(task_contract: "TaskContract | None") -> bool:
    if task_contract is None:
        return False
    return is_planning_task_type(getattr(task_contract, "task_type", None))


@dataclass(frozen=True)
class ResourceRef:
    """A resource that the task may need to cover."""

    id: str
    kind: str
    path: str = ""
    source: str = "history"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "source": self.source,
        }


class ResourceIndex:
    """Normalized resource view for current-turn and recent-history media."""

    def __init__(self, resources: list[ResourceRef] | tuple[ResourceRef, ...]):
        self.resources = tuple(_dedupe_resources(list(resources)))

    @classmethod
    def from_turn_and_history(
        cls,
        *,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
    ) -> "ResourceIndex":
        resources = cls._resources_from_turn(
            current_message=current_message,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
        )
        resources.extend(cls._recent_media_resources(history or []))
        return cls(resources)

    def by_kind(self, kind: str) -> list[ResourceRef]:
        return [item for item in self.resources if item.kind == kind]

    @staticmethod
    def aliases_for(resources: tuple[ResourceRef, ...] | list[ResourceRef]) -> dict[str, set[str]]:
        """Return equivalent path/index IDs for current-turn media resources."""
        aliases: dict[str, set[str]] = {}
        current_by_kind: dict[str, list[ResourceRef]] = {"image": [], "audio": [], "video": []}
        for resource in resources:
            if resource.source == "current_turn" and resource.kind in current_by_kind:
                current_by_kind[resource.kind].append(resource)

        for kind, kind_resources in current_by_kind.items():
            for index, resource in enumerate(kind_resources):
                equivalent_ids = {resource.id, f"{_RESOURCE_INDEX_PREFIX[kind]}:{index}"}
                if resource.path:
                    equivalent_ids.add(f"{kind}:{resource.path}")
                for resource_id in equivalent_ids:
                    aliases.setdefault(resource_id, set()).update(equivalent_ids)
        return aliases

    @staticmethod
    def _resources_from_turn(
        *,
        current_message: str,
        current_image_files: list[str] | None,
        current_audio_files: list[str] | None,
        current_video_files: list[str] | None,
    ) -> list[ResourceRef]:
        resources: list[ResourceRef] = []
        for index, path in enumerate(current_image_files or []):
            normalized = str(path or "").strip().replace("\\", "/")
            resources.append(ResourceRef(id=f"image:{normalized}" if normalized else f"image_index:{index}", kind="image", path=normalized, source="current_turn"))
        for index, path in enumerate(current_audio_files or []):
            normalized = str(path or "").strip().replace("\\", "/")
            resources.append(ResourceRef(id=f"audio:{normalized}" if normalized else f"audio_index:{index}", kind="audio", path=normalized, source="current_turn"))
        for index, path in enumerate(current_video_files or []):
            normalized = str(path or "").strip().replace("\\", "/")
            resources.append(ResourceRef(id=f"video:{normalized}" if normalized else f"video_index:{index}", kind="video", path=normalized, source="current_turn"))

        if not resources:
            resources.extend(_current_index_resources(current_message, _CURRENT_IMAGE_RE, "image"))
            resources.extend(_current_index_resources(current_message, _CURRENT_AUDIO_RE, "audio"))
            resources.extend(_current_index_resources(current_message, _CURRENT_VIDEO_RE, "video"))
        return resources

    @staticmethod
    def _recent_media_resources(history: list[dict[str, Any]]) -> list[ResourceRef]:
        resources: list[ResourceRef] = []
        found_recent_batch = False
        for message in reversed(history[-20:]):
            role = str(message.get("role") or "")
            if role != "user":
                continue
            content = str(message.get("content") or "")
            if MEDIA_ONLY_HISTORY_MARKER not in content:
                if found_recent_batch:
                    break
                continue
            found_recent_batch = True
            for match in _MEDIA_HISTORY_RE.finditer(content):
                label = match.group(1).lower()
                kind = {"images": "image", "audios": "audio", "videos": "video"}.get(label, "")
                for raw_path in match.group("paths").split(","):
                    path = raw_path.strip().replace("\\", "/")
                    if path:
                        resources.append(ResourceRef(id=f"{kind}:{path}", kind=kind, path=path, source="recent_media"))
        return resources


@dataclass(frozen=True)
class EvidenceRequirement:
    """Evidence needed before the task can be treated as complete."""

    kind: str
    tool_group: str = ""
    resource_ids: tuple[str, ...] = ()
    coverage: str = "any"
    min_count: int = 1
    description: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tool_group": self.tool_group,
            "resource_ids": list(self.resource_ids),
            "coverage": self.coverage,
            "min_count": self.min_count,
            "description": self.description,
        }


@dataclass(frozen=True)
class AcceptanceCriterion:
    """Answer-shape expectations needed for a high-quality final response."""

    kind: str
    min_count: int = 1
    min_response_chars: int = 0
    max_response_chars: int = 0
    description: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "min_count": self.min_count,
            "min_response_chars": self.min_response_chars,
            "max_response_chars": self.max_response_chars,
            "description": self.description,
        }


@dataclass(frozen=True)
class TaskContract:
    """Language-independent completion contract for one turn."""

    objective: str
    task_type: str
    requirements: tuple[EvidenceRequirement, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    selected_resources: tuple[ResourceRef, ...] = ()
    final_answer_required: bool = True
    allow_no_tool_final: bool = True
    contract_sources: tuple[str, ...] = DETERMINISTIC_CONTRACT_SOURCES
    harness_profile: dict[str, Any] | None = None
    planner_metadata: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "objective": self.objective,
            "task_type": self.task_type,
            "requirements": [item.to_metadata() for item in self.requirements],
            "acceptance_criteria": [item.to_metadata() for item in self.acceptance_criteria],
            "selected_resources": [item.to_metadata() for item in self.selected_resources],
            "final_answer_required": self.final_answer_required,
            "allow_no_tool_final": self.allow_no_tool_final,
            "contract_sources": list(self.contract_sources),
        }
        if self.planner_metadata:
            payload["planner_metadata"] = dict(self.planner_metadata)
        if self.harness_profile:
            payload["harness_profile"] = dict(self.harness_profile)
        return payload


def task_planner_status(task_contract: Any) -> str:
    """Return the normalized planner status from a task contract."""
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get(PLANNER_METADATA_STATUS_FIELD) or "").strip()
    return ""


def task_planner_reason(task_contract: Any) -> str:
    """Return the normalized planner reason from a task contract."""
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get(PLANNER_METADATA_REASON_FIELD) or "").strip()
    return ""


def neutral_task_contract(task_intent: TaskIntent, *, current_message: str | None = None) -> TaskContract:
    """Return a no-tool fallback when a caller bypasses the planner path."""
    objective = str(getattr(task_intent, "objective", "") or current_message or "").strip()
    return TaskContract(
        objective=objective,
        task_type=PURE_ANSWER_TASK_TYPE,
        final_answer_required=True,
        allow_no_tool_final=True,
        contract_sources=MISSING_RUNTIME_CONTRACT_SOURCES,
        planner_metadata={
            PLANNER_METADATA_STATUS_FIELD: PLANNER_MISSING_STATUS,
            PLANNER_METADATA_REASON_FIELD: MISSING_RUNTIME_CONTRACT_REASON,
        },
    )


class TaskPlanner:
    """LLM-backed planner that produces the authoritative per-turn task contract."""

    def __init__(self, llm_config: DocumentLlmConfig):
        self.llm_config = llm_config

    async def plan(
        self,
        *,
        provider: Any,
        model: str | None,
        tool_registry: Any | None = None,
        fallback_objective: str = "",
        current_message: str,
        history: list[dict[str, Any]] | None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
        task_context_decision: TaskContextDecision | None = None,
    ) -> TaskContract:
        if is_unconfigured_llm(provider, model):
            return _planner_blocked_contract(
                objective=_fallback_objective(fallback_objective, current_message),
                reason=PLANNER_UNAVAILABLE_REASON,
            )
        capability_catalog = build_planner_capability_catalog(tool_registry)
        planner_prompt = _build_task_planner_prompt(
            current_message=current_message,
            history=history or [],
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
            capability_catalog=capability_catalog,
        )
        try:
            response = await provider.chat(
                [
                    ChatMessage(role="system", content=_TASK_PLANNER_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=planner_prompt),
                ],
                model=model,
                **self.llm_config.decoding_kwargs(),
            )
        except Exception as exc:
            return _planner_blocked_contract(
                objective=_fallback_objective(fallback_objective, current_message),
                reason=_planner_exception_reason(exc),
            )
        response_text = str(getattr(response, "content", "") or "")
        payload = _parse_json_object(response_text)
        if not payload and response_text.strip():
            try:
                repair_response = await provider.chat(
                    [
                        ChatMessage(role="system", content=_PLANNER_REPAIR_SYSTEM_PROMPT),
                        ChatMessage(
                            role="user",
                            content=(
                                "Original planner prompt:\n"
                                f"{planner_prompt}\n\n"
                                "Invalid planner response:\n"
                                f"{response_text}\n\n"
                                "Return only the corrected JSON object."
                            ),
                        ),
                    ],
                    model=model,
                    **self.llm_config.decoding_kwargs(),
                )
            except Exception as exc:
                return _planner_blocked_contract(
                    objective=_fallback_objective(fallback_objective, current_message),
                    reason=_planner_exception_reason(exc),
                    raw_response_preview=_truncate(response_text, max_chars=400),
                )
            repair_text = str(getattr(repair_response, "content", "") or "")
            payload = _parse_json_object(repair_text)
            if not payload:
                response_text = repair_text or response_text
        if not payload:
            return _planner_blocked_contract(
                objective=_fallback_objective(fallback_objective, current_message),
                status=PLANNER_INVALID_STATUS,
                reason=PLANNER_INVALID_JSON_REASON,
                raw_response_preview=_truncate(response_text, max_chars=240),
            )
        return _contract_from_task_planner_payload(
            payload,
            fallback_objective=fallback_objective,
            current_message=current_message,
            history=history,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
            capability_catalog=capability_catalog,
        )





def _has_requirement(
    requirements: list[EvidenceRequirement],
    *,
    kind: str,
    tool_group: str = "",
) -> bool:
    return any(
        item.kind == kind and (not tool_group or item.tool_group == tool_group)
        for item in requirements
    )


def is_tool_group_requirement(requirement: Any) -> bool:
    return str(getattr(requirement, "kind", "") or "") == TOOL_GROUP_REQUIREMENT_KIND


def contract_has_acceptance_criterion(task_contract: Any, *kinds: str) -> bool:
    """Return whether a task contract carries any of the requested acceptance criteria."""
    expected = {str(kind or "").strip() for kind in kinds if str(kind or "").strip()}
    if not expected:
        return False
    return any(
        str(getattr(criterion, "kind", "") or "") in expected
        for criterion in getattr(task_contract, "acceptance_criteria", ()) or ()
    )


def contract_requests_itemized_output(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(task_contract, ITEMIZED_OUTPUT_CRITERION_KIND)


def contract_requests_source_reference(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(task_contract, SOURCE_REFERENCE_CRITERION_KIND)


def contract_requests_source_material(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(
        task_contract,
        SOURCE_ARTIFACT_CRITERION_KIND,
        SOURCE_DETAIL_CRITERION_KIND,
    )


def contract_requests_substantive_final_answer(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(task_contract, SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND)


def is_itemized_output_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == ITEMIZED_OUTPUT_CRITERION_KIND


def is_substantive_final_answer_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND


def is_source_artifact_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SOURCE_ARTIFACT_CRITERION_KIND


def is_source_detail_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SOURCE_DETAIL_CRITERION_KIND


def is_source_reference_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SOURCE_REFERENCE_CRITERION_KIND


def is_workspace_location_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == WORKSPACE_LOCATION_CRITERION_KIND


def is_media_artifact_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == MEDIA_ARTIFACT_CRITERION_KIND


def is_verification_or_gap_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == VERIFICATION_OR_GAP_CRITERION_KIND


def is_operation_report_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == OPERATION_REPORT_CRITERION_KIND


def _criterion_kind(criterion: Any) -> str:
    return str(getattr(criterion, "kind", "") or "")


def _is_resource_coverage_requirement(requirement: EvidenceRequirement) -> bool:
    return requirement.kind == RESOURCE_COVERAGE_REQUIREMENT_KIND


def _is_all_resource_coverage(requirement: EvidenceRequirement) -> bool:
    return requirement.coverage == ALL_RESOURCE_COVERAGE


def _is_file_change_requirement(requirement: Any) -> bool:
    return str(getattr(requirement, "kind", "") or "") == FILE_CHANGE_REQUIREMENT_KIND


def _is_verification_requirement(requirement: EvidenceRequirement) -> bool:
    return requirement.kind == VERIFICATION_REQUIREMENT_KIND


def _is_workspace_write_requirement(requirement: Any) -> bool:
    return str(getattr(requirement, "tool_group", "") or "") == WORKSPACE_WRITE_TOOL_GROUP


def missing_evidence(contract: TaskContract | None, evidence: tuple[ToolEvidence, ...], *, file_change_count: int, verification_passed: bool) -> tuple[str, ...]:
    """Return human-readable missing evidence items for a contract."""
    if contract is None:
        return ()
    missing: list[str] = []
    ok_evidence = [item for item in evidence if item.ok]
    aliases = ResourceIndex.aliases_for(contract.selected_resources)
    for requirement in contract.requirements:
        if is_tool_group_requirement(requirement):
            tools = _contract_tool_group_tools(contract, requirement.tool_group)
            count = sum(1 for item in ok_evidence if item.name in tools)
            if count < max(1, requirement.min_count):
                missing.append(requirement.description or f"Use one of: {', '.join(sorted(tools))}")
        elif _is_resource_coverage_requirement(requirement):
            tools = _contract_tool_group_tools(contract, requirement.tool_group)
            covered = {
                alias
                for item in ok_evidence
                if item.name in tools
                for resource_id in item.resource_ids
                for alias in aliases.get(resource_id, {resource_id})
            }
            required = set(requirement.resource_ids)
            if _is_all_resource_coverage(requirement):
                uncovered = tuple(resource_id for resource_id in requirement.resource_ids if resource_id not in covered)
                if uncovered:
                    missing.append(
                        f"Missing {requirement.tool_group} coverage for: {', '.join(uncovered)}"
                    )
            elif len(covered & required) < max(1, requirement.min_count):
                missing.append(requirement.description or f"Missing {requirement.tool_group} coverage")
        elif _is_file_change_requirement(requirement) and file_change_count < max(1, requirement.min_count):
            missing.append(requirement.description or "Record a workspace file change.")
        elif _is_verification_requirement(requirement) and not verification_passed:
            missing.append(requirement.description or "Record passing verification evidence.")
    return tuple(missing)


def _contract_tool_group_tools(contract: TaskContract, tool_group: str) -> frozenset[str]:
    metadata = getattr(contract, "planner_metadata", None) or {}
    capability_tools = metadata.get("capability_tools") if isinstance(metadata, dict) else None
    if isinstance(capability_tools, dict):
        tools = capability_tools.get(tool_group)
        if isinstance(tools, (list, tuple, set, frozenset)):
            return frozenset(str(tool or "").strip() for tool in tools if str(tool or "").strip())
    return TOOL_GROUPS.get(tool_group, frozenset())


def contract_expects_file_change(task_contract: Any) -> bool:
    """Return whether a task contract requires workspace file changes."""
    task_type = str(getattr(task_contract, "task_type", "") or "")
    if task_type in FILE_CHANGE_TASK_TYPES:
        return True
    for requirement in getattr(task_contract, "requirements", ()) or ():
        if _is_file_change_requirement(requirement):
            return True
        if _is_workspace_write_requirement(requirement):
            return True
    return False


def _tool_group_requirement(tool_group: str) -> EvidenceRequirement:
    if is_web_research_tool_group(tool_group):
        return EvidenceRequirement(
            kind="tool_group",
            tool_group=WEB_RESEARCH_TOOL_GROUP,
            coverage="any",
            min_count=1,
            description="Use web research tools before answering this external information request.",
        )
    return EvidenceRequirement(
        kind="tool_group",
        tool_group=tool_group,
        coverage="any",
        min_count=1,
        description=f"Use {tool_group} tools before finalizing the answer.",
    )


def _append_acceptance_criteria(
    existing: list[AcceptanceCriterion],
    additions: tuple[AcceptanceCriterion, ...],
) -> list[AcceptanceCriterion]:
    seen = {_criterion_kind(item) for item in existing}
    for criterion in additions:
        criterion_kind = _criterion_kind(criterion)
        if criterion_kind not in seen:
            existing.append(criterion)
            seen.add(criterion_kind)
    return existing


def _build_task_planner_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
    capability_catalog: PlannerCapabilityCatalog | None = None,
) -> str:
    catalog = capability_catalog or build_planner_capability_catalog()
    context = {
        "current_message": _truncate_middle(current_message, max_chars=1200),
        "recent_history": _recent_history(history),
        "attachments": {
            "image_files": list(current_image_files or []),
            "audio_files": list(current_audio_files or []),
            "video_files": list(current_video_files or []),
        },
        "task_context": task_context_decision.to_metadata() if task_context_decision is not None else None,
        "capability_catalog": catalog.to_prompt_metadata(),
        "quality_checks": _quality_check_catalog(),
    }
    return (
        "Create the task contract for the latest user turn. The contract controls which tools the main assistant can see.\n"
        "Use semantic judgment from the message and recent history, not string matching. Select the smallest set of "
        "required_tool_groups from capability_catalog.available_capabilities that is necessary to finish the task with "
        "evidence. Do not invent unavailable tool groups. If no tool-backed evidence is needed, choose pure_answer with "
        "an empty required_tool_groups array. Use quality_checks only when the final answer needs extra verification "
        "beyond the selected capabilities.\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "objective": "short task objective in the user language",\n'
        f'  "task_type": "{_schema_union(catalog.task_types)}",\n'
        f'  "required_tool_groups": ["{_schema_union(catalog.tool_group_ids)}"],\n'
        f'  "quality_checks": ["{_schema_union(_ALLOWED_PLANNER_QUALITY_CHECKS)}"],\n'
        '  "final_answer_required": true,\n'
        '  "allow_no_tool_final": true,\n'
        '  "reason": "short explanation for trace only"\n'
        "}\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _quality_check_catalog() -> list[dict[str, str]]:
    return [
        {
            "id": COMMAND_VERSION_QUALITY_CHECK,
            "description": "Use when the final answer must report an installed command version.",
        },
        {
            "id": REPOSITORY_STATUS_QUALITY_CHECK,
            "description": "Use when the final answer must report repository or worktree status.",
        },
        {
            "id": WORKSPACE_LOCATION_QUALITY_CHECK,
            "description": "Use when the final answer must identify a workspace file path, symbol, or config location.",
        },
    ]


def _schema_union(values: tuple[str, ...] | frozenset[str]) -> str:
    ordered = list(values) if isinstance(values, tuple) else sorted(values)
    return " | ".join(ordered) if ordered else "<none>"


def _planner_blocked_contract(
    *,
    objective: str,
    reason: str,
    status: str = PLANNER_BLOCKED_STATUS,
    raw_response_preview: str = "",
) -> TaskContract:
    metadata: dict[str, Any] = {
        PLANNER_METADATA_STATUS_FIELD: status,
        PLANNER_METADATA_REASON_FIELD: reason,
    }
    if raw_response_preview:
        metadata[PLANNER_METADATA_RAW_RESPONSE_PREVIEW_FIELD] = raw_response_preview
    return TaskContract(
        objective=objective,
        task_type=PLANNING_ERROR_TASK_TYPE,
        final_answer_required=True,
        allow_no_tool_final=False,
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        acceptance_criteria=(
            AcceptanceCriterion(
                kind="planner_error_report",
                description="Explain that task contract planning failed and a reliable tool profile could not be selected.",
            ),
        ),
        planner_metadata=metadata,
    )


def _planner_exception_reason(exc: Exception) -> str:
    error_type = exc.__class__.__name__
    message = str(exc).strip()
    if message:
        return f"task planner LLM call failed: {error_type}: {message}"
    return f"task planner LLM call failed: {error_type}"


def _fallback_objective(fallback_objective: str | None, current_message: str | None) -> str:
    return str(fallback_objective or current_message or "").strip()


def _current_index_resources(current_message: str, pattern: re.Pattern[str], kind: str) -> list[ResourceRef]:
    match = pattern.search(current_message or "")
    if not match:
        return []
    count = int(match.group("count") or 0)
    index_prefix = _RESOURCE_INDEX_PREFIX[kind]
    return [ResourceRef(id=f"{index_prefix}:{index}", kind=kind, source="current_turn") for index in range(max(0, count))]


def _dedupe_resources(resources: list[ResourceRef]) -> list[ResourceRef]:
    by_id: dict[str, ResourceRef] = {}
    order: list[str] = []
    for item in resources:
        if not item.id or item.id in by_id:
            continue
        by_id[item.id] = item
        order.append(item.id)
    return [by_id[item_id] for item_id in order]


def _planner_objective(
    payload: dict[str, Any],
    fallback_objective: str | None,
    current_message: str | None,
) -> str:
    objective = _compact(payload.get("objective"))
    return objective or _fallback_objective(fallback_objective, current_message)


def _contract_from_task_planner_payload(
    payload: dict[str, Any],
    *,
    fallback_objective: str = "",
    current_message: str,
    history: list[dict[str, Any]] | None,
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
    capability_catalog: PlannerCapabilityCatalog | None = None,
) -> TaskContract:
    catalog = capability_catalog or build_planner_capability_catalog()
    objective = _planner_objective(payload, fallback_objective, current_message)
    resource_index = ResourceIndex.from_turn_and_history(
        current_message=current_message,
        history=history,
        current_image_files=current_image_files,
        current_audio_files=current_audio_files,
        current_video_files=current_video_files,
    )
    raw_task_type = _allowed_string(payload.get("task_type"), _ALLOWED_PLANNER_TASK_TYPES)
    if not raw_task_type:
        return _planner_blocked_contract(
            objective=objective,
            status=PLANNER_INVALID_STATUS,
            reason=PLANNER_UNSUPPORTED_TASK_TYPE_REASON,
            raw_response_preview=_truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), max_chars=240),
        )
    raw_tool_groups = _normalize_planner_tool_groups(
        payload.get("required_tool_groups"),
        allowed_tool_groups=catalog.tool_group_ids,
    )
    quality_checks = _normalize_planner_quality_checks(payload.get("quality_checks"))
    task_type = _PLANNER_TASK_TYPE_ALIASES.get(raw_task_type, raw_task_type)
    tool_groups = raw_tool_groups
    if task_type == HISTORY_RETRIEVAL_TASK_TYPE:
        tool_groups = [tool_group for tool_group in tool_groups if tool_group == HISTORY_RETRIEVAL_TOOL_GROUP]
    inherited_tool_group = getattr(task_context_decision, "inherited_tool_group", "") or ""
    if (
        inherited_tool_group in catalog.tool_group_ids
        and inherited_tool_group not in tool_groups
    ):
        tool_groups.append(inherited_tool_group)
    _ensure_task_type_tool_groups(task_type, tool_groups)

    requirements: list[EvidenceRequirement] = []
    acceptance_criteria: list[AcceptanceCriterion] = []
    selected: list[ResourceRef] = []

    for tool_group in tool_groups:
        acceptance_criteria = _append_tool_group_contract(
            tool_group,
            requirements=requirements,
            acceptance_criteria=acceptance_criteria,
            resource_index=resource_index,
            selected=selected,
        )

    if WORKSPACE_LOCATION_QUALITY_CHECK in quality_checks:
        acceptance_criteria = _append_acceptance_criteria(acceptance_criteria, (_workspace_location_criterion(),))

    planner_reason = _truncate(str(payload.get("reason") or PLANNER_VALIDATED_REASON), max_chars=240)
    metadata = {
        PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS,
        "raw_task_type": raw_task_type,
        "required_tool_groups": list(tool_groups),
        "quality_checks": list(quality_checks),
        "capability_tools": {key: list(value) for key, value in catalog.capability_tools.items()},
        PLANNER_METADATA_REASON_FIELD: planner_reason,
    }
    return TaskContract(
        objective=objective,
        task_type=task_type,
        requirements=tuple(requirements),
        acceptance_criteria=tuple(acceptance_criteria),
        selected_resources=tuple(dict.fromkeys(selected)),
        final_answer_required=_coerce_bool(payload.get("final_answer_required", True)),
        allow_no_tool_final=_coerce_bool(payload.get("allow_no_tool_final", not requirements)) and not requirements,
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata=metadata,
    )


def _normalize_planner_tool_groups(
    value: Any,
    *,
    allowed_tool_groups: tuple[str, ...] | frozenset[str] | None = None,
) -> list[str]:
    allowed = set(allowed_tool_groups or _ALLOWED_PLANNER_TOOL_GROUPS)
    raw_values = value if isinstance(value, list) else []
    groups: list[str] = []
    for item in raw_values:
        text = str(item or "").strip()
        text = _PLANNER_TOOL_GROUP_ALIASES.get(text, text)
        if text in allowed and text not in groups:
            groups.append(text)
    return groups


def _normalize_planner_quality_checks(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else []
    checks: list[str] = []
    for item in raw_values:
        text = str(item or "").strip().lower()
        if text in _ALLOWED_PLANNER_QUALITY_CHECKS and text not in checks:
            checks.append(text)
    return checks


def _ensure_task_type_tool_groups(task_type: str, tool_groups: list[str]) -> None:
    for tool_group in _TASK_TYPE_REQUIRED_TOOL_GROUPS.get(task_type, ()):
        if tool_group not in tool_groups:
            tool_groups.append(tool_group)


def _append_tool_group_contract(
    tool_group: str,
    *,
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    resource_index: ResourceIndex,
    selected: list[ResourceRef],
) -> list[AcceptanceCriterion]:
    if is_web_research_tool_group(tool_group):
        _append_web_contract(requirements, acceptance_criteria, min_source_count=2)
        return acceptance_criteria
    if tool_group == WORKSPACE_READ_TOOL_GROUP:
        _append_workspace_contract(requirements, acceptance_criteria)
        return acceptance_criteria
    if tool_group == WORKSPACE_WRITE_TOOL_GROUP:
        _append_workspace_contract(requirements, acceptance_criteria)
        if not _has_requirement(requirements, kind=FILE_CHANGE_REQUIREMENT_KIND):
            requirements.append(
                EvidenceRequirement(
                    kind=FILE_CHANGE_REQUIREMENT_KIND,
                    min_count=1,
                    description="Record at least one workspace file change.",
                )
            )
        return _append_acceptance_criteria(acceptance_criteria, (_verification_or_gap_criterion(),))
    if tool_group == MEDIA_TOOL_GROUP:
        return _append_media_contract(
            requirements,
            acceptance_criteria,
            resource_index=resource_index,
            selected=selected,
        )
    if tool_group == HISTORY_RETRIEVAL_TOOL_GROUP:
        requirements.append(_tool_group_requirement(HISTORY_RETRIEVAL_TOOL_GROUP))
        return _append_acceptance_criteria(acceptance_criteria, (_history_final_answer_criterion(),))
    if tool_group in OPERATION_TOOL_GROUPS:
        requirements.append(_tool_group_requirement(tool_group))
        return _append_acceptance_criteria(acceptance_criteria, (_operation_report_criterion(),))
    if tool_group == VERIFICATION_TOOL_GROUP:
        requirements.append(
            EvidenceRequirement(
                kind=VERIFICATION_REQUIREMENT_KIND,
                tool_group=VERIFICATION_TOOL_GROUP,
                min_count=1,
                description="Record verification evidence before finalizing.",
            )
        )
        return acceptance_criteria
    requirements.append(_tool_group_requirement(tool_group))
    return _append_acceptance_criteria(acceptance_criteria, (_tool_backed_final_answer_criterion(),))


def _append_media_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    resource_index: ResourceIndex,
    selected: list[ResourceRef],
) -> list[AcceptanceCriterion]:
    image_resources = resource_index.by_kind("image")
    audio_resources = resource_index.by_kind("audio")
    video_resources = resource_index.by_kind("video")
    selected.extend(image_resources + audio_resources + video_resources)
    if image_resources:
        requirements.append(
            EvidenceRequirement(
                kind="resource_coverage",
                tool_group="image_text",
                resource_ids=tuple(item.id for item in image_resources),
                coverage="all",
                min_count=len(image_resources),
                description="Inspect each referenced image before finalizing the answer.",
            )
        )
    if audio_resources:
        requirements.append(
            EvidenceRequirement(
                kind="resource_coverage",
                tool_group="audio_text",
                resource_ids=tuple(item.id for item in audio_resources),
                coverage="all",
                min_count=len(audio_resources),
                description="Transcribe each referenced audio clip before finalizing the answer.",
            )
        )
    if video_resources:
        requirements.append(
            EvidenceRequirement(
                kind="resource_coverage",
                tool_group="video_understanding",
                resource_ids=tuple(item.id for item in video_resources),
                coverage="all",
                min_count=len(video_resources),
                description="Analyze each referenced video before finalizing the answer.",
            )
        )
    if not (image_resources or audio_resources or video_resources):
        requirements.append(_tool_group_requirement(MEDIA_TOOL_GROUP))
    return _append_acceptance_criteria(
        acceptance_criteria,
        (_media_artifact_criterion(), _media_final_answer_criterion()),
    )


def _append_web_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    min_source_count: int,
) -> None:
    if not _has_requirement(requirements, kind="tool_group", tool_group=WEB_RESEARCH_TOOL_GROUP):
        requirements.append(
            EvidenceRequirement(
                kind="tool_group",
                tool_group=WEB_RESEARCH_TOOL_GROUP,
                coverage="any",
                min_count=1,
                description="Use web research tools before answering this external information request.",
            )
        )
    acceptance_criteria[:] = _append_acceptance_criteria(
        acceptance_criteria,
        (
            AcceptanceCriterion(
                kind=SOURCE_ARTIFACT_CRITERION_KIND,
                min_count=min_source_count,
                description="Produce enough traceable web sources before finalizing the answer.",
            ),
            AcceptanceCriterion(
                kind=SOURCE_DETAIL_CRITERION_KIND,
                min_count=1,
                description="Fetch or inspect at least one source page before finalizing; search snippets alone are not enough.",
            ),
            _web_final_answer_criterion(),
            _web_source_reference_criterion(),
        ),
    )


def _append_workspace_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
) -> None:
    if not _has_requirement(requirements, kind="tool_group", tool_group=WORKSPACE_READ_TOOL_GROUP):
        requirements.append(
            EvidenceRequirement(
                kind="tool_group",
                tool_group=WORKSPACE_READ_TOOL_GROUP,
                coverage="any",
                min_count=1,
                description="Inspect the relevant workspace files or code context before answering.",
            )
        )
    acceptance_criteria[:] = _append_acceptance_criteria(acceptance_criteria, (_workspace_final_answer_criterion(),))


def _parse_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL)
    raw = fenced.group(1) if fenced else text
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end >= start:
        raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _recent_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in (history or [])[-6:]:
        role = str(item.get("role") or "").strip()
        content = _truncate(str(item.get("content") or ""), max_chars=500)
        if role and content:
            entries.append({"role": role, "content": content})
    return entries


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate(text: str, *, max_chars: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _truncate_middle(text: str, *, max_chars: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    if max_chars <= 20:
        return _truncate(compact, max_chars=max_chars)
    marker = "\n... [middle omitted] ...\n"
    remaining = max_chars - len(marker)
    head_chars = max(1, remaining // 2)
    tail_chars = max(1, remaining - head_chars)
    return f"{compact[:head_chars].rstrip()}{marker}{compact[-tail_chars:].lstrip()}"


def _allowed_string(value: Any, allowed: frozenset[str]) -> str | None:
    text = str(value or "").strip()
    return text if text in allowed else None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _media_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the inspected media results.",
    )


def _media_artifact_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="media_artifact",
        min_count=1,
        description="Produce a media artifact for the selected image, audio, or video before finalizing.",
    )


def _web_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=100,
        description="Provide a substantive final answer that uses the gathered web source results.",
    )


def _web_source_reference_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind=SOURCE_REFERENCE_CRITERION_KIND,
        min_count=1,
        description="Reference at least one gathered web source by URL, domain, or title.",
    )


def _workspace_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the inspected workspace context.",
    )


def _tool_backed_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind=SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
        min_response_chars=80,
        description="Provide a substantive final answer that uses the gathered tool evidence.",
    )


def _workspace_location_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="workspace_location",
        min_count=1,
        description="Identify the relevant workspace file path, symbol, or configuration location in the final answer.",
    )


def _verification_or_gap_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="verification_or_gap",
        description="After code changes, either record a focused verification attempt or state the verification gap clearly.",
    )


def _operation_report_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="operation_report",
        description="Report approval, validation, rollback, blocker, or residual risk for the operation.",
    )


def _history_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the retrieved prior context.",
    )
