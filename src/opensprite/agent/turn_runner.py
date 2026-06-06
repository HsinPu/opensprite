"""User turn orchestration for AgentLoop.process."""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator
from uuid import uuid4

from ..bus.message import AssistantMessage, UserMessage
from ..config import LogConfig, TaskMessagesConfig
from ..runs.events import (
    AUTO_CONTINUE_COMPLETED_EVENT,
    AUTO_CONTINUE_SCHEDULED_EVENT,
    AUTO_CONTINUE_SKIPPED_EVENT,
    ACTIVE_TASK_SEEDED_EVENT,
    AUDIO_INPUT_TRANSCRIBED_EVENT,
    COMPLETION_GATE_EVALUATED_EVENT,
    DIRECT_VERIFICATION_STARTED_EVENT,
    DIRECT_WORKFLOW_RESUME_STARTED_EVENT,
    EXECUTION_STOPPED_EVENT,
    HARNESS_CHECKPOINT_RECORDED_EVENT,
    HARNESS_SCORECARD_RECORDED_EVENT,
    INBOUND_MEDIA_EVENT_PREFIX,
    INBOUND_MEDIA_PERSISTED_EVENT,
    LLM_STATUS_EVENT,
    TASK_ARTIFACTS_RECORDED_EVENT,
    TASK_CHECKLIST_UPDATED_EVENT,
    TASK_INTENT_DETECTED_EVENT,
    WORK_PLAN_CREATED_EVENT,
    WORK_PROGRESS_UPDATED_EVENT,
)
from ..documents.active_task import (
    ACTIVE_ACTIVE_TASK_STATUS,
    BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION,
    BLOCKED_ACTIVE_TASK_STATUS,
    DONE_ACTIVE_TASK_STATUS,
    TASK_BOUNDARY_CONFIRMATION_EVENT,
    TASK_BOUNDARY_CONFIRMATION_RESOLVED_EVENT,
    WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION,
    WAITING_USER_ACTIVE_TASK_STATUS,
    _extract_task_field,
    build_task_block_from_intent_fields,
    build_task_block_from_text,
    clear_open_questions,
    clears_active_task_open_questions,
    create_active_task_store,
    is_current_active_task_status,
    is_current_or_done_active_task_status,
    is_inactive_active_task_status,
    is_terminal_active_task_status,
)
from ..harness import (
    ANALYSIS_TASK_TYPE,
    CODE_CHANGE_TASK_TYPE,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    VERIFICATION_REQUIREMENT_KIND,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_CHANGE_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP,
    HarnessProfile,
    HarnessProfileService,
    HarnessScorecard,
    HarnessSensorResult,
    evaluate_harness_sensors,
    is_chat_profile_name,
    is_coding_profile_name,
    is_media_profile_name,
    is_ops_profile_name,
    is_research_profile_name,
    normalize_profile_name,
)
from ..utils.log import logger
from ..utils.url import join_url_path
from .completion_gate import AutoContinueService, format_web_source_context
from .completion_gate import (
    COMPLETION_RESULT_ACTIVE_TASK_DETAIL_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_PROMPT_TYPE_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_STEP_ID_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_STEP_LABEL_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_WORKFLOW_FIELD,
    COMPLETION_RESULT_VERIFICATION_ACTION_FIELD,
    COMPLETION_RESULT_VERIFICATION_PATH_FIELD,
    COMPLETION_RESULT_VERIFICATION_PYTEST_ARGS_FIELD,
    CompletionBlockerMessages,
    CompletionGateResult,
    CompletionGateService,
    completion_blocker_response,
)
from .completion_gate import (
    BLOCKED_COMPLETION_STATUS,
    INCOMPLETE_COMPLETION_STATUS,
    allows_nonfinal_response_replacement,
    is_blocking_completion_status,
    is_complete_completion_status,
    is_incomplete_completion_status,
    needs_review_completion_status,
    is_terminal_completion_status,
    needs_verification_completion_status,
    requires_evidence_follow_up,
    normalize_completion_status,
)
from .execution import ExecutionResult, MAX_TOOL_ITERATIONS_STOP_REASON, is_max_tool_iterations_stop_reason
from ..media import (
    AgentMediaService,
    AudioInputPreprocessor,
    INBOUND_AUDIO_EXTENSIONS,
    INBOUND_IMAGE_EXTENSIONS,
    INBOUND_VIDEO_EXTENSIONS,
)
from ..runs.trace import AgentRunStateService
from ..runs.trace import RunTraceRecorder, WorktreeSandboxInspector
from ..storage import StorageProvider, StoredDelegatedTask, StoredWorkState
from ..storage.base import (
    coerce_stored_delegated_tasks,
    get_storage_message_count,
    legacy_delegated_tasks,
    selected_delegated_task,
)
from .task_contract import (
    BOUNDARY_CONTINUE_REPLY_COMMAND,
    BOUNDARY_SWITCH_REPLY_COMMAND,
    CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE,
    PLANNER_METADATA_STATUS_FIELD,
    PLANNER_VALIDATED_STATUS,
    PLANNING_ERROR_TASK_TYPE,
    PRESERVE_STATE_RESET_CONTINUATION_TYPES,
    is_ambiguous_boundary_continuation_type,
    is_current_task_continuation_type,
    is_current_task_replacement_type,
    intent_supports_default_work_plan,
    is_tool_group_requirement,
)
from .task_contract import TaskContextDecision, TaskContextResolver, TaskIntent, TaskIntentService, TaskObjectiveDecision
from ..tools.evidence import (
    is_source_acceptance_criterion_kind,
    is_web_fetch_source_record_tool,
    is_web_research_task_type,
    is_web_research_tool_group,
    is_web_source_artifact_kind,
)
from .execution import is_workflow_failed_status


TURN_METADATA_AUTO_CONTINUE_ATTEMPTS_FIELD = "auto_continue_attempts"
TURN_METADATA_COMPLETION_GATE_FIELD = "completion_gate"
TURN_METADATA_COMPLETION_STATUS_FIELD = "completion_status"
TURN_METADATA_COMPLETION_REASON_FIELD = "completion_reason"
TURN_METADATA_WORK_PROGRESS_FIELD = "work_progress"
TURN_METADATA_TASK_CONTRACT_FIELD = "task_contract"
TURN_METADATA_TOOL_EVIDENCE_FIELD = "tool_evidence"
TURN_METADATA_TASK_ARTIFACTS_FIELD = "task_artifacts"
TURN_METADATA_DELEGATED_TASKS_FIELD = "delegated_tasks"
TURN_METADATA_ACTIVE_DELEGATE_TASK_ID_FIELD = "active_delegate_task_id"
TURN_METADATA_ACTIVE_DELEGATE_PROMPT_TYPE_FIELD = "active_delegate_prompt_type"
MEDIA_ONLY_TURN_REASON = "media_only"
LLM_NOT_CONFIGURED_TURN_REASON = "llm_not_configured"
LLM_NOT_CONFIGURED_LOG_REASON = "llm-not-configured"
OBJECTIVE_KEYWORD_RE = re.compile(r"[a-z0-9.:-]{3,}")
OBJECTIVE_CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
OBJECTIVE_BRAND_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b")
OBJECTIVE_KEYWORD_STOP_WORDS = frozenset(
    {
        "please",
        "current",
        "latest",
        "\u5e6b\u6211",
        "\u76ee\u524d",
        "\u6700\u65b0",
        "\u8acb\u5217\u51fa",
        "\u4f86\u6e90\u7db2\u5740",
    }
)


def source_finalization_allowed(completion_result: CompletionGateResult, execution_result: ExecutionResult) -> bool:
    if not (
        is_incomplete_completion_status(completion_result.status)
        or normalize_completion_status(completion_result.status) == BLOCKED_COMPLETION_STATUS
        or needs_review_completion_status(completion_result.status)
    ):
        return False
    return task_contract_requires_web_sources(execution_result.task_contract)


def task_contract_requires_web_sources(contract: Any) -> bool:
    if contract is None:
        return False
    if is_web_research_task_type(getattr(contract, "task_type", None)):
        return True
    for requirement in getattr(contract, "requirements", ()) or ():
        if is_tool_group_requirement(requirement) and is_web_research_tool_group(getattr(requirement, "tool_group", None)):
            return True
    for criterion in getattr(contract, "acceptance_criteria", ()) or ():
        if is_source_acceptance_criterion_kind(getattr(criterion, "kind", None)):
            return True
    return False


def rank_web_sources_for_objective(sources: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    if not objective:
        return sources
    return sorted(
        sources,
        key=lambda source: web_source_relevance_score(source, objective),
        reverse=True,
    )


def web_source_relevance_score(source: dict[str, Any], objective: str) -> int:
    keywords = _objective_keywords(objective)
    if not keywords:
        return 0
    score = 0
    domain = str(source.get("domain") or "").lower()
    if not domain:
        url = str(source.get("url") or "").lower()
        domain = re.sub(r"^https?://", "", url).split("/", 1)[0]
    domain_label = _domain_brand_label(domain)
    if domain_label and domain_label in _objective_brand_tokens(objective):
        score += 10
    haystack = " ".join(
        str(source.get(key) or "")
        for key in ("title", "url", "snippet", "content", "domain")
    ).lower()
    score += sum(1 for keyword in keywords if keyword in haystack)
    return score


def _objective_keywords(objective: str) -> set[str]:
    text = str(objective or "").lower()
    keywords: set[str] = set()
    keywords.update(OBJECTIVE_KEYWORD_RE.findall(text))
    for cjk_text in OBJECTIVE_CJK_SEQUENCE_RE.findall(text):
        keywords.add(cjk_text)
        for size in (2, 3, 4):
            for index in range(0, max(len(cjk_text) - size + 1, 0)):
                keywords.add(cjk_text[index : index + size])
    return {keyword for keyword in keywords if keyword not in OBJECTIVE_KEYWORD_STOP_WORDS}


def _objective_brand_tokens(objective: str) -> set[str]:
    return {
        token.lower()
        for token in OBJECTIVE_BRAND_TOKEN_RE.findall(str(objective or ""))
    }


def _domain_brand_label(domain: str) -> str:
    labels = str(domain or "").lower().removeprefix("www.").split(".")
    labels = [label for label in labels if label]
    if len(labels) < 2:
        return ""
    return labels[-2].replace("-", "")


class TurnContextService:
    """Activates task-local context for one user message turn."""

    def __init__(
        self,
        *,
        current_session_id: ContextVar[str | None],
        current_channel: ContextVar[str | None],
        current_external_chat_id: ContextVar[str | None],
        current_images: ContextVar[list[str] | None],
        current_audios: ContextVar[list[str] | None],
        current_videos: ContextVar[list[str] | None],
        current_outbound_media: ContextVar[dict[str, list[str]] | None],
        current_run_id: ContextVar[str | None],
        current_work_progress: ContextVar[dict[str, Any] | None],
    ):
        self._current_session_id = current_session_id
        self._current_channel = current_channel
        self._current_external_chat_id = current_external_chat_id
        self._current_images = current_images
        self._current_audios = current_audios
        self._current_videos = current_videos
        self._current_outbound_media = current_outbound_media
        self._current_run_id = current_run_id
        self._current_work_progress = current_work_progress

    def current_session_id(self) -> str | None:
        """Return the current task-local session id."""
        return self._current_session_id.get()

    def current_channel(self) -> str | None:
        """Return the current task-local channel."""
        return self._current_channel.get()

    def current_external_chat_id(self) -> str | None:
        """Return the current transport-level chat id."""
        return self._current_external_chat_id.get()

    def current_images(self) -> list[str] | None:
        """Return images attached to the current active turn."""
        return self._current_images.get()

    def current_audios(self) -> list[str] | None:
        """Return audios attached to the current active turn."""
        return self._current_audios.get()

    def current_videos(self) -> list[str] | None:
        """Return videos attached to the current active turn."""
        return self._current_videos.get()

    def current_run_id(self) -> str | None:
        """Return the current task-local run id."""
        return self._current_run_id.get()

    def queue_outbound_media(self, kind: str, payload: str) -> str | None:
        """Queue one media payload to be attached to the current assistant reply."""
        return AgentMediaService.queue_outbound_media(self._current_outbound_media.get(), kind, payload)

    def queued_outbound_media(self) -> dict[str, list[str]]:
        """Return queued outbound media for the current turn."""
        return AgentMediaService.queued_outbound_media(self._current_outbound_media.get())

    def reset_work_progress(self) -> None:
        """Reset per-pass progress signals while keeping turn context active."""
        self._current_work_progress.set(self._default_work_progress())

    def note_file_change(self, path: str) -> None:
        """Record one file-change signal for the active pass."""
        state = self._current_work_progress.get()
        if state is None:
            return
        normalized_path = str(path or "").strip()
        state["file_change_count"] = int(state.get("file_change_count", 0)) + 1
        if normalized_path and normalized_path not in state["touched_paths"]:
            state["touched_paths"].append(normalized_path)

    def snapshot_work_progress(self) -> dict[str, Any]:
        """Return the current per-pass progress signals."""
        state = self._current_work_progress.get() or self._default_work_progress()
        return {
            "file_change_count": int(state.get("file_change_count", 0)),
            "touched_paths": tuple(str(path) for path in state.get("touched_paths", []) if str(path).strip()),
        }

    @staticmethod
    def _default_work_progress() -> dict[str, Any]:
        return {"file_change_count": 0, "touched_paths": []}

    @contextmanager
    def activate(
        self,
        *,
        session_id: str,
        channel: str | None,
        external_chat_id: str | None,
        images: list[str] | None,
        audios: list[str] | None,
        videos: list[str] | None,
        run_id: str,
    ) -> Iterator[None]:
        """Set per-turn context values and reset them in reverse order."""
        token = self._current_session_id.set(session_id)
        channel_token = self._current_channel.set(channel)
        external_chat_id_token = self._current_external_chat_id.set(external_chat_id)
        images_token = self._current_images.set(list(images or []))
        audios_token = self._current_audios.set(list(audios or []))
        videos_token = self._current_videos.set(list(videos or []))
        outbound_media_token = self._current_outbound_media.set(
            {"images": [], "voices": [], "audios": [], "videos": []}
        )
        run_token = self._current_run_id.set(run_id)
        work_progress_token = self._current_work_progress.set(self._default_work_progress())
        try:
            yield
        finally:
            self._current_work_progress.reset(work_progress_token)
            self._current_run_id.reset(run_token)
            self._current_outbound_media.reset(outbound_media_token)
            self._current_videos.reset(videos_token)
            self._current_audios.reset(audios_token)
            self._current_images.reset(images_token)
            self._current_external_chat_id.reset(external_chat_id_token)
            self._current_channel.reset(channel_token)
            self._current_session_id.reset(token)


QUICK_ACTION_METADATA_KEY = "quick_action"
TURN_SOURCE_METADATA_KEY = "source"
CLI_VIA_WEB_TURN_SOURCE = "cli_via_web"
RESUME_FOLLOW_UP_QUICK_ACTION = "resume_follow_up"
RUN_VERIFICATION_QUICK_ACTION = "run_verification"


def metadata_is_cli_via_web(metadata: dict[str, Any]) -> bool:
    return str(metadata.get(TURN_SOURCE_METADATA_KEY) or "").strip() == CLI_VIA_WEB_TURN_SOURCE


def metadata_requests_follow_up_resume(metadata: dict[str, Any]) -> bool:
    return _quick_action(metadata) == RESUME_FOLLOW_UP_QUICK_ACTION


def metadata_requests_direct_verification(metadata: dict[str, Any]) -> bool:
    return _quick_action(metadata) == RUN_VERIFICATION_QUICK_ACTION


def _quick_action(metadata: dict[str, Any]) -> str:
    return str(metadata.get(QUICK_ACTION_METADATA_KEY) or "").strip()


@dataclass(frozen=True)
class PreparedTurnInput:
    """Resolved user turn data used by process orchestration."""

    session_id: str
    channel: str | None
    external_chat_id: str | None
    image_files: list[str]
    audio_files: list[str]
    video_files: list[str]
    media_events: list[dict[str, Any]]
    user_metadata: dict[str, Any]
    assistant_metadata: dict[str, Any]


class TurnInputPreparer:
    """Resolves turn ids, persists inbound media, and builds message metadata."""

    def __init__(
        self,
        *,
        media_service: AgentMediaService,
        format_log_preview: Callable[..., str],
    ):
        self.media_service = media_service
        self._format_log_preview = format_log_preview

    def prepare(self, user_message: UserMessage) -> PreparedTurnInput:
        """Prepare all process input fields derived directly from the inbound message."""
        session_id = user_message.session_id or user_message.external_chat_id or "default"
        channel = user_message.channel or None

        if ":" not in session_id:
            logger.warning(
                "Received non-namespaced session_id '{}' in Agent.process; this may mix sessions if MessageQueue is bypassed",
                session_id,
            )

        sender = user_message.sender_name or user_message.sender_id or "-"
        logger.info(
            f"[{session_id}] inbound | channel={channel or '-'} sender={sender} images={len(user_message.images or [])} "
            f"text={self._format_log_preview(user_message.text, max_chars=200)}"
        )
        image_result = self.media_service.persist_inbound_media_with_events(
            session_id,
            user_message.images,
            media_prefix="image",
            directory_name="images",
            extensions=INBOUND_IMAGE_EXTENSIONS,
        )
        audio_result = self.media_service.persist_inbound_media_with_events(
            session_id,
            user_message.audios,
            media_prefix="audio",
            directory_name="audios",
            extensions=INBOUND_AUDIO_EXTENSIONS,
        )
        video_result = self.media_service.persist_inbound_media_with_events(
            session_id,
            user_message.videos,
            media_prefix="video",
            directory_name="videos",
            extensions=INBOUND_VIDEO_EXTENSIONS,
        )
        image_files = image_result.files
        audio_files = audio_result.files
        video_files = video_result.files
        media_events = [*image_result.events, *audio_result.events, *video_result.events]

        user_metadata = {
            **dict(user_message.metadata or {}),
            "channel": channel,
            "external_chat_id": user_message.external_chat_id,
            "sender_id": user_message.sender_id,
            "sender_name": user_message.sender_name,
            "images_count": len(user_message.images or []),
            "image_files": image_files or None,
            "images_dir": "images" if image_files else None,
            "audios_count": len(user_message.audios or []),
            "audio_files": audio_files or None,
            "audios_dir": "audios" if audio_files else None,
            "videos_count": len(user_message.videos or []),
            "video_files": video_files or None,
            "videos_dir": "videos" if video_files else None,
        }
        user_metadata = {key: value for key, value in user_metadata.items() if value is not None}
        assistant_metadata = {
            "channel": channel,
            "external_chat_id": user_message.external_chat_id,
        }
        assistant_metadata = {key: value for key, value in assistant_metadata.items() if value is not None}
        external_chat_id = str(user_message.external_chat_id) if user_message.external_chat_id is not None else None

        return PreparedTurnInput(
            session_id=session_id,
            channel=channel,
            external_chat_id=external_chat_id,
            image_files=image_files,
            audio_files=audio_files,
            video_files=video_files,
            media_events=media_events,
            user_metadata=user_metadata,
            assistant_metadata=assistant_metadata,
        )


@dataclass(frozen=True)
class TurnPassEvaluation:
    """Evaluation output for one normal-turn execution pass."""

    aggregate_result: ExecutionResult
    completion_result: CompletionGateResult
    work_progress: WorkProgressUpdate
    collected_delegated_tasks: tuple[StoredDelegatedTask, ...]
    collected_workflow_outcomes: tuple[dict[str, Any], ...]


class AgentResponseFinalizer:
    """Persists assistant replies, completes runs, and builds outbound messages."""

    def __init__(
        self,
        *,
        run_trace: RunTraceRecorder,
        save_message: Callable[..., Awaitable[None]],
        format_log_preview: Callable[..., str],
        log_config: LogConfig | None = None,
    ):
        self.run_trace = run_trace
        self._save_message = save_message
        self._format_log_preview = format_log_preview
        self.log_config = log_config or LogConfig()

    @staticmethod
    def _reasoning_text_size(value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, dict):
            return sum(AgentResponseFinalizer._reasoning_text_size(item) for item in value.values())
        if isinstance(value, list):
            return sum(AgentResponseFinalizer._reasoning_text_size(item) for item in value)
        return 0

    @staticmethod
    def _reasoning_type_summary(details: list[Any]) -> str:
        counts: dict[str, int] = {}
        for item in details:
            item_type = item.get("type") if isinstance(item, dict) else type(item).__name__
            key = str(item_type or "unknown")
            counts[key] = counts.get(key, 0) + 1
        return ", ".join(f"{key}:{counts[key]}" for key in sorted(counts)) or "none"

    def _log_reasoning_details(self, session_id: str, metadata: dict[str, Any]) -> None:
        details = metadata.get("llm_reasoning_details")
        if not isinstance(details, list) or not details:
            return

        logger.info(
            "[{}] LLM reasoning summary | details={} chars={} types={}",
            session_id,
            len(details),
            self._reasoning_text_size(details),
            self._reasoning_type_summary(details),
        )
        if not self.log_config.log_reasoning_details:
            return

        logger.info(
            "[{}] LLM reasoning details | {}",
            session_id,
            json.dumps(details, ensure_ascii=False, default=str),
        )

    def _log_outbound(
        self,
        session_id: str,
        response: str,
        *,
        prefix: str = "",
    ) -> None:
        logger.info(
            f"[{session_id}] outbound | {prefix}text={self._format_log_preview(response, max_chars=200)}"
        )

    async def finalize(
        self,
        *,
        session_id: str,
        run_id: str,
        response: str,
        channel: str | None,
        external_chat_id: str | None,
        assistant_metadata: dict[str, Any],
        run_part_metadata: dict[str, Any],
        run_event_payload: dict[str, Any],
        persisted_assistant_metadata: dict[str, Any] | None = None,
        status_metadata: dict[str, Any] | None = None,
        images: list[str] | None = None,
        voices: list[str] | None = None,
        audios: list[str] | None = None,
        videos: list[str] | None = None,
        log_prefix: str = "",
        log_before_record: bool = False,
        after_save: Callable[[], Awaitable[None]] | None = None,
    ) -> AssistantMessage:
        """Finalize a visible assistant response for one user turn."""
        if log_before_record:
            self._log_outbound(session_id, response, prefix=log_prefix)

        await self.run_trace.record_assistant_message_part(
            session_id,
            run_id,
            response,
            metadata=run_part_metadata,
        )

        if not log_before_record:
            self._log_outbound(session_id, response, prefix=log_prefix)

        persisted_metadata = persisted_assistant_metadata if persisted_assistant_metadata is not None else assistant_metadata
        self._log_reasoning_details(session_id, persisted_metadata)

        await self._save_message(
            session_id,
            "assistant",
            response,
            metadata=persisted_metadata,
        )
        if after_save is not None:
            await after_save()

        await self.run_trace.complete_run(
            session_id,
            run_id,
            event_payload=run_event_payload,
            status_metadata=status_metadata,
            channel=channel,
            external_chat_id=external_chat_id,
        )

        return AssistantMessage(
            text=response,
            channel=channel or "unknown",
            external_chat_id=external_chat_id,
            session_id=session_id,
            images=images,
            voices=voices,
            audios=audios,
            videos=videos,
            metadata=assistant_metadata,
        )


class AgentTurnRunner:
    """Runs user-turn branches after inbound turn input is prepared."""

    def __init__(
        self,
        *,
        run_trace: RunTraceRecorder,
        response_finalizer: AgentResponseFinalizer,
        turn_context: TurnContextService,
        run_state: AgentRunStateService,
        task_intents: TaskIntentService,
        harness_profiles: HarnessProfileService,
        completion_gate: CompletionGateService,
        completion_judge_context: Callable[[], tuple[Any, str | None]],
        auto_continue: AutoContinueService,
        work_progress: WorkProgressService,
        connect_mcp: Callable[[], Awaitable[None]],
        save_message: Callable[..., Awaitable[None]],
        emit_run_event: Callable[..., Awaitable[None]],
        call_llm: Callable[..., Awaitable[ExecutionResult]],
        transcribe_audio: Callable[[list[str]], Awaitable[str]],
        run_workflow: Callable[[str, str, str | None], Awaitable[str]],
        run_verify: Callable[[str, str, tuple[str, ...]], Awaitable[ExecutionResult]],
        verification_available: Callable[[], bool],
        get_queued_outbound_media: Callable[[], dict[str, list[str]]],
        media_saved_ack: Callable[[], str],
        llm_not_configured_message: Callable[[], str],
        completion_blocker_messages: Callable[[], CompletionBlockerMessages],
        format_log_preview: Callable[..., str],
        set_session_overlay_id: Callable[[str, dict[str, Any] | None, str | None, str | None], None],
        read_active_task_snapshot: Callable[[str], str],
        get_work_state: Callable[[str], Awaitable[StoredWorkState | None]],
        save_work_state: Callable[[StoredWorkState | None], Awaitable[None]],
        apply_completion_gate_result: Callable[[str, CompletionGateResult], Awaitable[None]],
        apply_work_progress: Callable[[str, WorkProgressUpdate, StoredWorkState | None], Awaitable[None]],
        schedule_curator: Callable[[str, str, str | None, str | None, ExecutionResult], None],
        finalize_learning_reuse: Callable[[str, str, bool], None],
        consume_delegated_task_updates: Callable[[str], tuple[StoredDelegatedTask, ...]],
        clear_delegated_task_updates: Callable[[str], None],
        consume_workflow_outcomes: Callable[[str], tuple[dict[str, Any], ...]],
        clear_workflow_outcomes: Callable[[str], None],
        worktree_sandbox_enabled: Callable[[], bool],
        workspace_root: Callable[[], Path],
    ):
        self.run_trace = run_trace
        self.response_finalizer = response_finalizer
        self.turn_context = turn_context
        self.run_state = run_state
        self.task_intents = task_intents
        self.harness_profiles = harness_profiles
        self.completion_gate = completion_gate
        self._completion_judge_context = completion_judge_context
        self.auto_continue = auto_continue
        self.work_progress = work_progress
        self._connect_mcp = connect_mcp
        self._save_message = save_message
        self._emit_run_event = emit_run_event
        self._call_llm = call_llm
        self.audio_input = AudioInputPreprocessor(transcribe_audio)
        self._run_workflow = run_workflow
        self._run_verify = run_verify
        self._verification_available = verification_available
        self._get_queued_outbound_media = get_queued_outbound_media
        self._media_saved_ack = media_saved_ack
        self._llm_not_configured_message = llm_not_configured_message
        self._completion_blocker_messages = completion_blocker_messages
        self._format_log_preview = format_log_preview
        self._set_session_overlay_id = set_session_overlay_id
        self._read_active_task_snapshot = read_active_task_snapshot
        self._get_work_state = get_work_state
        self._save_work_state = save_work_state
        self._apply_completion_gate_result = apply_completion_gate_result
        self._apply_work_progress = apply_work_progress
        self._schedule_curator = schedule_curator
        self._finalize_learning_reuse = finalize_learning_reuse
        self._consume_delegated_task_updates = consume_delegated_task_updates
        self._clear_delegated_task_updates = clear_delegated_task_updates
        self._consume_workflow_outcomes = consume_workflow_outcomes
        self._clear_workflow_outcomes = clear_workflow_outcomes
        self._worktree_sandbox_enabled = worktree_sandbox_enabled
        self._workspace_root = workspace_root

    @staticmethod
    def is_media_only_message(user_message: UserMessage) -> bool:
        """Return whether a turn only carries media without user instructions."""
        if AudioInputPreprocessor.should_pretranscribe(user_message):
            return False
        return AgentMediaService.is_media_only_message(
            text=user_message.text,
            images=user_message.images,
            audios=user_message.audios,
            videos=user_message.videos,
        )

    async def _preprocess_audio_only_message(
        self,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        run_id: str,
    ) -> None:
        """Turn pure voice input into text before it reaches the LLM."""
        result = await self.audio_input.preprocess(user_message, turn)
        if not result.transcribed:
            return
        await self._emit_run_event(
            turn.session_id,
            run_id,
            AUDIO_INPUT_TRANSCRIBED_EVENT,
            {
                "status": result.status,
                "audio_files": list(result.audio_files),
                "transcript_len": result.transcript_len,
            },
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )

    def _resolve_pre_work_task_context(
        self,
        *,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        task_intent: TaskIntent,
        existing_work_state: StoredWorkState | None,
    ) -> TaskContextDecision:
        """Resolve deterministic task context needed before work-state setup."""
        return TaskContextResolver.resolve_deterministic(
            current_message=_message_with_runtime_context(user_message.text, turn.user_metadata),
            task_intent=task_intent,
            active_task=self._read_active_task_snapshot(turn.session_id),
            work_state_summary=self.work_progress.render_state_summary(existing_work_state),
        )

    async def _maybe_record_worktree_sandbox(
        self,
        session_id: str,
        run_id: str,
        *,
        task_kind: str,
        expects_code_change: bool,
    ) -> bool:
        enabled = self._worktree_sandbox_enabled()
        if not enabled and not expects_code_change:
            return False
        metadata = WorktreeSandboxInspector(
            enabled=enabled,
            workspace_root=self._workspace_root(),
        ).create(session_id=session_id, run_id=run_id).to_payload()
        metadata["task_kind"] = task_kind
        metadata["expects_code_change"] = expects_code_change
        await self.run_trace.record_worktree_sandbox_part(session_id, run_id, metadata)
        return True

    async def run_user_turn(
        self,
        *,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        llm_configured: bool,
    ) -> AssistantMessage:
        """Start run telemetry and dispatch one prepared user turn."""
        run_id = f"run_{uuid4().hex}"
        self.run_state.start(turn.session_id, run_id)
        await self.run_trace.start_turn_run(
            turn.session_id,
            run_id,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
            sender_id=user_message.sender_id,
            sender_name=user_message.sender_name,
            text=user_message.text,
            images=user_message.images,
            audios=user_message.audios,
            videos=user_message.videos,
        )
        for media_event in turn.media_events:
            await self._emit_run_event(
                turn.session_id,
                run_id,
                INBOUND_MEDIA_PERSISTED_EVENT
                if media_event.get("status") == "persisted"
                else f"{INBOUND_MEDIA_EVENT_PREFIX}{media_event.get('status') or 'unknown'}",
                {"schema_version": 1, **dict(media_event)},
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
        await self._preprocess_audio_only_message(user_message, turn, run_id)
        task_intent = self.task_intents.classify(
            user_message.text,
            images=user_message.images,
            audios=user_message.audios,
            videos=user_message.videos,
            metadata=user_message.metadata,
        )
        self._set_session_overlay_id(turn.session_id, user_message.metadata, turn.channel, user_message.sender_id)
        existing_work_state = await self._get_work_state(turn.session_id)
        pre_work_task_context_decision = self._resolve_pre_work_task_context(
            user_message=user_message,
            turn=turn,
            task_intent=task_intent,
            existing_work_state=existing_work_state,
        )
        task_intent = self.work_progress.resolve_intent(
            task_intent,
            existing_work_state,
            task_context_decision=pre_work_task_context_decision,
        )
        worktree_sandbox_recorded = await self._maybe_record_worktree_sandbox(
            turn.session_id,
            run_id,
            task_kind=task_intent.kind,
            expects_code_change=False,
        )
        await self._emit_run_event(
            turn.session_id,
            run_id,
            TASK_INTENT_DETECTED_EVENT,
            task_intent.to_metadata(),
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        work_plan = self.work_progress.create_plan(task_intent)
        current_work_state = self.work_progress.build_initial_state(
            session_id=turn.session_id,
            task_intent=task_intent,
            work_plan=work_plan,
            existing_state=existing_work_state,
            task_context_decision=pre_work_task_context_decision,
        )

        try:
            if self.is_media_only_message(user_message):
                return await self.run_media_only_turn(
                    user_message=user_message,
                    turn=turn,
                    run_id=run_id,
                )

            with self.turn_context.activate(
                session_id=turn.session_id,
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
                images=user_message.images,
                audios=user_message.audios,
                videos=user_message.videos,
                run_id=run_id,
            ):
                try:
                    if not llm_configured:
                        return await self.run_llm_not_configured_turn(
                            user_message=user_message,
                            turn=turn,
                            run_id=run_id,
                        )

                    return await self.run_normal_turn(
                        user_message=user_message,
                        turn=turn,
                        run_id=run_id,
                        task_intent=task_intent,
                        harness_profile=None,
                        work_plan=work_plan,
                        current_work_state=current_work_state,
                        worktree_sandbox_recorded=worktree_sandbox_recorded,
                    )
                except asyncio.CancelledError:
                    await self.run_trace.fail_run(
                        turn.session_id,
                        run_id,
                        status="cancelled",
                        event_payload={"status": "cancelled", "error": "cancelled"},
                        channel=turn.channel,
                        external_chat_id=turn.external_chat_id,
                    )
                    raise
                except Exception as exc:
                    logger.exception(
                        f"[{turn.session_id}] Agent.process failed: channel={turn.channel}, "
                        f"text_len={len(user_message.text or '')}, images={len(user_message.images or [])}, audios={len(user_message.audios or [])}, videos={len(user_message.videos or [])}"
                    )
                    self._finalize_learning_reuse(turn.session_id, run_id, False)
                    await self.run_trace.fail_run(
                        turn.session_id,
                        run_id,
                        status="failed",
                        event_payload={
                            "status": "failed",
                            "error": self._format_log_preview(f"{type(exc).__name__}: {exc}", max_chars=240),
                        },
                        channel=turn.channel,
                        external_chat_id=turn.external_chat_id,
                    )
                    raise
        finally:
            self._clear_delegated_task_updates(run_id)
            self._clear_workflow_outcomes(run_id)
            self.run_state.finish(turn.session_id, run_id)

    async def run_media_only_turn(
        self,
        *,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        run_id: str,
    ) -> AssistantMessage:
        """Persist a media-only turn and return the configured acknowledgement."""
        media_history_content = AgentMediaService.format_saved_media_history_content(
            image_files=turn.image_files,
            audio_files=turn.audio_files,
            video_files=turn.video_files,
        )
        await self._save_message(turn.session_id, "user", media_history_content, metadata=turn.user_metadata)
        response = self._media_saved_ack()
        return await self.response_finalizer.finalize(
            session_id=turn.session_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
            assistant_metadata=turn.assistant_metadata,
            run_part_metadata={"reason": MEDIA_ONLY_TURN_REASON, "response_len": len(response or "")},
            run_event_payload={
                "status": "completed",
                "reason": MEDIA_ONLY_TURN_REASON,
                "response_len": len(response or ""),
            },
            log_prefix="media_only=true ",
            log_before_record=True,
        )

    async def run_llm_not_configured_turn(
        self,
        *,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        run_id: str,
    ) -> AssistantMessage:
        """Persist a turn and return the configured setup hint when no LLM is available."""
        logger.warning("[{}] agent.skip | reason={}", turn.session_id, LLM_NOT_CONFIGURED_LOG_REASON)
        await self._save_message(turn.session_id, "user", user_message.text, metadata=turn.user_metadata)
        response = self._llm_not_configured_message()
        return await self.response_finalizer.finalize(
            session_id=turn.session_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
            assistant_metadata=turn.assistant_metadata,
            run_part_metadata={"reason": LLM_NOT_CONFIGURED_TURN_REASON, "response_len": len(response or "")},
            run_event_payload={
                "status": "completed",
                "reason": LLM_NOT_CONFIGURED_TURN_REASON,
                "response_len": len(response or ""),
            },
            log_before_record=True,
        )

    async def _evaluate_turn_pass(
        self,
        *,
        turn: PreparedTurnInput,
        run_id: str,
        task_intent: TaskIntent,
        harness_profile: HarnessProfile | None,
        execution_results: list[ExecutionResult],
        response: str,
        collected_delegated_tasks: tuple[StoredDelegatedTask, ...],
        collected_workflow_outcomes: tuple[dict[str, Any], ...],
        auto_continue_attempts: int,
    ) -> TurnPassEvaluation:
        """Record trace artifacts, aggregate execution, and evaluate completion for one pass."""
        exec_result = execution_results[-1]
        await self.run_trace.record_context_compaction_parts(
            turn.session_id,
            run_id,
            exec_result.context_compaction_events,
        )
        await self.run_trace.record_llm_step_parts(
            turn.session_id,
            run_id,
            exec_result.llm_step_events,
        )
        if exec_result.stop_reason:
            await self._emit_run_event(
                turn.session_id,
                run_id,
                EXECUTION_STOPPED_EVENT,
                {
                    "schema_version": 1,
                    "status": "stopped",
                    "stop_reason": exec_result.stop_reason,
                    **dict(exec_result.stop_metadata or {}),
                },
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
        aggregate_result = self._aggregate_execution_results(execution_results, content=response)
        delegated_task_updates = self._consume_delegated_task_updates(run_id)
        if delegated_task_updates:
            collected_delegated_tasks = self._merge_delegated_task_updates(
                collected_delegated_tasks,
                delegated_task_updates,
            )
        workflow_outcomes = self._consume_workflow_outcomes(run_id)
        if workflow_outcomes:
            collected_workflow_outcomes = self._merge_workflow_outcomes(
                collected_workflow_outcomes,
                workflow_outcomes,
            )
        if collected_delegated_tasks:
            aggregate_result = self._with_delegated_tasks(aggregate_result, collected_delegated_tasks)
        if collected_workflow_outcomes:
            aggregate_result = self._with_workflow_outcomes(aggregate_result, collected_workflow_outcomes)

        completion_result = await self._evaluate_completion(
            task_intent=task_intent,
            response_text=response,
            execution_result=aggregate_result,
        )
        completion_metadata = self._completion_metadata(
            completion_result,
            auto_continue_attempts=auto_continue_attempts,
        )
        await self._emit_run_event(
            turn.session_id,
            run_id,
            COMPLETION_GATE_EVALUATED_EVENT,
            completion_metadata,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        work_progress = self.work_progress.evaluate(
            task_intent=task_intent,
            completion_result=completion_result,
            execution_result=aggregate_result,
            auto_continue_attempts=auto_continue_attempts,
            pass_index=len(execution_results),
            harness_profile=harness_profile,
        )
        await self._emit_run_event(
            turn.session_id,
            run_id,
            WORK_PROGRESS_UPDATED_EVENT,
            work_progress.to_metadata(),
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        harness_checkpoint = _harness_checkpoint_metadata(
            harness_profile=harness_profile,
            aggregate_result=aggregate_result,
            completion_result=completion_result,
            work_progress=work_progress,
            pass_index=len(execution_results),
            auto_continue_attempts=auto_continue_attempts,
        )
        await self._emit_run_event(
            turn.session_id,
            run_id,
            HARNESS_CHECKPOINT_RECORDED_EVENT,
            harness_checkpoint,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        await self.run_trace.record_harness_checkpoint_part(turn.session_id, run_id, harness_checkpoint)
        harness_scorecard = _harness_scorecard_metadata(
            harness_profile=harness_profile,
            aggregate_result=aggregate_result,
            completion_result=completion_result,
        )
        await self._emit_run_event(
            turn.session_id,
            run_id,
            HARNESS_SCORECARD_RECORDED_EVENT,
            harness_scorecard,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        await self.run_trace.record_harness_scorecard_part(turn.session_id, run_id, harness_scorecard)
        if auto_continue_attempts > 0:
            await self._emit_run_event(
                turn.session_id,
                run_id,
                AUTO_CONTINUE_COMPLETED_EVENT,
                {
                    "attempt": auto_continue_attempts,
                    TURN_METADATA_COMPLETION_STATUS_FIELD: completion_result.status,
                    TURN_METADATA_COMPLETION_REASON_FIELD: completion_result.reason,
                },
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
        return TurnPassEvaluation(
            aggregate_result=aggregate_result,
            completion_result=completion_result,
            work_progress=work_progress,
            collected_delegated_tasks=collected_delegated_tasks,
            collected_workflow_outcomes=collected_workflow_outcomes,
        )

    async def _evaluate_completion(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
    ) -> CompletionGateResult:
        provider, model = self._completion_judge_context()
        return await self.completion_gate.evaluate_with_judge(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            provider=provider,
            model=model,
        )

    def _completion_metadata(
        self,
        completion_result: CompletionGateResult,
        *,
        auto_continue_attempts: int,
    ) -> dict[str, Any]:
        metadata = completion_result.to_metadata()
        metadata[TURN_METADATA_AUTO_CONTINUE_ATTEMPTS_FIELD] = auto_continue_attempts
        judge = metadata.setdefault("judge", {})
        if isinstance(judge, dict):
            provider, model = self._completion_judge_context()
            judge.setdefault("method", "llm")
            judge.setdefault("provider", type(provider).__name__ if provider is not None else "")
            judge.setdefault("model", model or "")
        return metadata

    async def run_normal_turn(
        self,
        *,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        run_id: str,
        task_intent: TaskIntent,
        harness_profile: HarnessProfile | None,
        work_plan: WorkPlan | None,
        current_work_state: StoredWorkState | None,
        worktree_sandbox_recorded: bool,
    ) -> AssistantMessage:
        """Execute the normal turn path after special-case early exits are ruled out."""
        await self._connect_mcp()

        # The current user message is persisted before building the prompt so history/search stay current.
        await self._save_message(turn.session_id, "user", user_message.text, metadata=turn.user_metadata)

        logger.info(f"[{turn.session_id}] agent.run | status=processing")
        await self._emit_run_event(
            turn.session_id,
            run_id,
            LLM_STATUS_EVENT,
            {"message": "processing"},
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        execution_results: list[ExecutionResult] = []
        collected_delegated_tasks: tuple[StoredDelegatedTask, ...] = ()
        collected_workflow_outcomes: tuple[dict[str, Any], ...] = ()
        auto_continue_attempts = 0
        direct_actions_used = 0
        last_direct_workflow: str | None = None
        last_direct_start_step: str | None = None
        last_direct_verify_action: str | None = None
        last_direct_verify_path: str | None = None
        last_direct_verify_pytest_args: tuple[str, ...] = ()
        same_target_verify_attempts = 0
        work_plan_recorded = False
        pending_direct_verify: dict[str, Any] | None = self._extract_direct_verify_request(user_message.metadata)
        current_message = _message_with_runtime_context(user_message.text, turn.user_metadata)
        current_allow_tools = True
        current_task_contract_override = None

        pending_direct_resume = self._extract_follow_up_resume_request(user_message.metadata)

        while True:
            self.turn_context.reset_work_progress()
            direct_resume_context: dict[str, str] | None = None
            if pending_direct_resume is not None:
                direct_resume_context = dict(pending_direct_resume)
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    DIRECT_WORKFLOW_RESUME_STARTED_EVENT,
                    {"schema_version": 1, **direct_resume_context},
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
                response, exec_result, collected_delegated_tasks, collected_workflow_outcomes = await self._run_direct_workflow_resume(
                    run_id=run_id,
                    task_intent=task_intent,
                    current_work_state=current_work_state,
                    direct_resume=pending_direct_resume,
                    collected_delegated_tasks=collected_delegated_tasks,
                    collected_workflow_outcomes=collected_workflow_outcomes,
                )
                pending_direct_resume = None
            elif pending_direct_verify is not None:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    DIRECT_VERIFICATION_STARTED_EVENT,
                    {"schema_version": 1, **dict(pending_direct_verify)},
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
                response, exec_result = await self._run_direct_verification(
                    direct_verify=pending_direct_verify,
                )
                pending_direct_verify = None
            else:
                exec_result = await self._call_llm(
                    turn.session_id,
                    current_message=current_message,
                    channel=turn.channel,
                    user_images=user_message.images,
                    user_image_files=turn.image_files,
                    user_audio_files=self.audio_input.audio_files_for_llm(user_message, turn),
                    user_video_files=turn.video_files,
                    external_chat_id=turn.external_chat_id,
                    emit_tool_progress=True,
                    task_intent=task_intent,
                    allow_tools=current_allow_tools,
                    task_contract_override=(
                        current_task_contract_override if auto_continue_attempts > 0 else None
                    ),
                )
                exec_result = self._apply_runtime_progress(exec_result, self.turn_context.snapshot_work_progress())
                if exec_result.task_contract is not None:
                    harness_profile = self.harness_profiles.from_contract(exec_result.task_contract)
                    contract_work_plan = self.work_progress.create_plan(task_intent, harness_profile=harness_profile)
                    if contract_work_plan is None:
                        if _can_replace_initial_work_state(current_work_state):
                            work_plan = None
                            current_work_state = None
                    else:
                        work_plan = contract_work_plan
                        if not work_plan_recorded:
                            await self._emit_run_event(
                                turn.session_id,
                                run_id,
                                WORK_PLAN_CREATED_EVENT,
                                work_plan.to_metadata(),
                                channel=turn.channel,
                                external_chat_id=turn.external_chat_id,
                            )
                            work_plan_recorded = True
                        if not worktree_sandbox_recorded and work_plan.expects_code_change:
                            worktree_sandbox_recorded = await self._maybe_record_worktree_sandbox(
                                turn.session_id,
                                run_id,
                                task_kind=work_plan.kind,
                                expects_code_change=True,
                            )
                        if _can_replace_initial_work_state(current_work_state):
                            current_work_state = self.work_progress.build_initial_state(
                                session_id=turn.session_id,
                                task_intent=task_intent,
                                work_plan=work_plan,
                                existing_state=None,
                            )
                response = exec_result.content
            execution_results.append(exec_result)

            evaluation = await self._evaluate_turn_pass(
                turn=turn,
                run_id=run_id,
                task_intent=task_intent,
                harness_profile=harness_profile,
                execution_results=execution_results,
                response=response,
                collected_delegated_tasks=collected_delegated_tasks,
                collected_workflow_outcomes=collected_workflow_outcomes,
                auto_continue_attempts=auto_continue_attempts,
            )
            aggregate_result = evaluation.aggregate_result
            completion_result = evaluation.completion_result
            work_progress = evaluation.work_progress
            if _is_tool_backed_task_contract(aggregate_result.task_contract):
                current_task_contract_override = aggregate_result.task_contract
            collected_delegated_tasks = evaluation.collected_delegated_tasks
            collected_workflow_outcomes = evaluation.collected_workflow_outcomes

            decision = self.auto_continue.decide(
                task_intent=task_intent,
                completion_result=completion_result,
                execution_result=aggregate_result,
                attempts_used=auto_continue_attempts,
                previous_response=response,
                work_progress=work_progress,
                last_direct_workflow=last_direct_workflow,
                last_direct_start_step=last_direct_start_step,
                direct_actions_used=direct_actions_used,
                last_direct_verify_action=last_direct_verify_action,
                last_direct_verify_path=last_direct_verify_path,
                last_direct_verify_pytest_args=last_direct_verify_pytest_args,
                same_target_verify_attempts=same_target_verify_attempts,
                verification_available=self._verification_available(),
                compaction_handoff=aggregate_result.compaction_handoff,
                harness_profile=harness_profile,
            )
            if decision.should_continue and decision.direct_workflow and decision.direct_start_step:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    AUTO_CONTINUE_SCHEDULED_EVENT,
                    {
                        **decision.to_metadata(),
                        TURN_METADATA_COMPLETION_STATUS_FIELD: completion_result.status,
                        TURN_METADATA_COMPLETION_REASON_FIELD: completion_result.reason,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
                auto_continue_attempts += 1
                direct_actions_used += 1
                last_direct_workflow = decision.direct_workflow
                last_direct_start_step = decision.direct_start_step
                pending_direct_resume = {
                    "workflow": decision.direct_workflow,
                    "start_step": decision.direct_start_step,
                    "step_label": completion_result.follow_up_step_label or decision.direct_start_step,
                    "prompt_type": completion_result.follow_up_prompt_type or "",
                    "detail": completion_result.active_task_detail or "",
                    "previous_response": response,
                }
                continue
            if decision.should_continue and decision.direct_verify_action:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    AUTO_CONTINUE_SCHEDULED_EVENT,
                    {
                        **decision.to_metadata(),
                        TURN_METADATA_COMPLETION_STATUS_FIELD: completion_result.status,
                        TURN_METADATA_COMPLETION_REASON_FIELD: completion_result.reason,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
                auto_continue_attempts += 1
                direct_actions_used += 1
                if (
                    decision.direct_verify_action == last_direct_verify_action
                    and (decision.direct_verify_path or ".") == (last_direct_verify_path or ".")
                    and tuple(decision.direct_verify_pytest_args) == tuple(last_direct_verify_pytest_args)
                ):
                    same_target_verify_attempts += 1
                else:
                    same_target_verify_attempts = 1
                last_direct_verify_action = decision.direct_verify_action
                last_direct_verify_path = decision.direct_verify_path or "."
                last_direct_verify_pytest_args = tuple(decision.direct_verify_pytest_args)
                pending_direct_verify = {
                    "action": decision.direct_verify_action,
                    "path": decision.direct_verify_path or ".",
                    "pytest_args": tuple(decision.direct_verify_pytest_args),
                }
                continue
            if decision.should_continue and decision.prompt:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    AUTO_CONTINUE_SCHEDULED_EVENT,
                    {
                        **decision.to_metadata(),
                        TURN_METADATA_COMPLETION_STATUS_FIELD: completion_result.status,
                        TURN_METADATA_COMPLETION_REASON_FIELD: completion_result.reason,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
                auto_continue_attempts += 1
                if direct_resume_context is not None:
                    current_message = self.auto_continue.build_post_workflow_resume_prompt(
                        task_intent=task_intent,
                        completion_result=completion_result,
                        previous_response=direct_resume_context.get("previous_response") or "continue",
                        workflow_result=response,
                    )
                else:
                    current_message = decision.prompt
                    current_allow_tools = decision.allow_tools
                continue

            if decision.emit_skipped_event:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    AUTO_CONTINUE_SKIPPED_EVENT,
                    {
                        **decision.to_metadata(),
                        TURN_METADATA_COMPLETION_STATUS_FIELD: completion_result.status,
                        TURN_METADATA_COMPLETION_REASON_FIELD: completion_result.reason,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
            break

        ran_source_finalization = False
        source_finalization_sources = _source_finalization_sources(completion_result, aggregate_result)
        if source_finalization_sources:
            finalization_prompt = self.auto_continue.build_prompt(
                task_intent=task_intent,
                completion_result=completion_result,
                previous_response=response,
                compaction_handoff=aggregate_result.compaction_handoff,
                harness_profile=harness_profile,
                execution_result=aggregate_result,
                allow_tools=False,
                source_context_override=format_web_source_context(source_finalization_sources),
            )
            finalization_result = await self._call_llm(
                turn.session_id,
                current_message=finalization_prompt,
                channel=turn.channel,
                user_images=[],
                user_image_files=[],
                user_audio_files=[],
                user_video_files=[],
                external_chat_id=turn.external_chat_id,
                emit_tool_progress=True,
                task_intent=task_intent,
                allow_tools=False,
                task_contract_override=aggregate_result.task_contract,
            )
            finalization_result = self._apply_runtime_progress(
                finalization_result,
                self.turn_context.snapshot_work_progress(),
            )
            execution_results.append(finalization_result)
            response = finalization_result.content
            aggregate_result = self._aggregate_execution_results(execution_results, content=response)
            ran_source_finalization = True
        if ran_source_finalization or response != aggregate_result.content:
            aggregate_result.content = response
            completion_result = await self._evaluate_completion(
                task_intent=task_intent,
                response_text=response,
                execution_result=aggregate_result,
            )
            completion_metadata = self._completion_metadata(
                completion_result,
                auto_continue_attempts=auto_continue_attempts,
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                COMPLETION_GATE_EVALUATED_EVENT,
                completion_metadata,
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
            work_progress = self.work_progress.evaluate(
                task_intent=task_intent,
                completion_result=completion_result,
                execution_result=aggregate_result,
                auto_continue_attempts=auto_continue_attempts,
                pass_index=len(execution_results),
                harness_profile=harness_profile,
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                WORK_PROGRESS_UPDATED_EVENT,
                work_progress.to_metadata(),
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
            final_harness_scorecard = _harness_scorecard_metadata(
                harness_profile=harness_profile,
                aggregate_result=aggregate_result,
                completion_result=completion_result,
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                HARNESS_SCORECARD_RECORDED_EVENT,
                final_harness_scorecard,
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
            await self.run_trace.record_harness_scorecard_part(
                turn.session_id,
                run_id,
                final_harness_scorecard,
            )

        response = _final_response_after_exhausted_continuation(
            response=response,
            completion_result=completion_result,
            auto_continue_attempts=auto_continue_attempts,
            completion_blocker_messages=self._completion_blocker_messages(),
        )
        if response != aggregate_result.content:
            aggregate_result.content = response
            completion_result = await self._evaluate_completion(
                task_intent=task_intent,
                response_text=response,
                execution_result=aggregate_result,
            )
            completion_metadata = self._completion_metadata(
                completion_result,
                auto_continue_attempts=auto_continue_attempts,
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                COMPLETION_GATE_EVALUATED_EVENT,
                completion_metadata,
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
            work_progress = self.work_progress.evaluate(
                task_intent=task_intent,
                completion_result=completion_result,
                execution_result=aggregate_result,
                auto_continue_attempts=auto_continue_attempts,
                pass_index=len(execution_results),
                harness_profile=harness_profile,
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                WORK_PROGRESS_UPDATED_EVENT,
                work_progress.to_metadata(),
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
            final_harness_scorecard = _harness_scorecard_metadata(
                harness_profile=harness_profile,
                aggregate_result=aggregate_result,
                completion_result=completion_result,
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                HARNESS_SCORECARD_RECORDED_EVENT,
                final_harness_scorecard,
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
            await self.run_trace.record_harness_scorecard_part(
                turn.session_id,
                run_id,
                final_harness_scorecard,
            )

        outbound_media = self._get_queued_outbound_media()

        response_metadata = {
            "response_len": len(response or ""),
            "executed_tool_calls": aggregate_result.executed_tool_calls,
            "had_tool_error": aggregate_result.had_tool_error,
            "verification_attempted": aggregate_result.verification_attempted,
            "verification_passed": aggregate_result.verification_passed,
            "context_compactions": aggregate_result.context_compactions,
            TURN_METADATA_AUTO_CONTINUE_ATTEMPTS_FIELD: auto_continue_attempts,
            TURN_METADATA_WORK_PROGRESS_FIELD: work_progress.to_metadata(),
        }
        status_metadata = {
            "executed_tool_calls": aggregate_result.executed_tool_calls,
            "had_tool_error": aggregate_result.had_tool_error,
            "verification_attempted": aggregate_result.verification_attempted,
            "verification_passed": aggregate_result.verification_passed,
            "context_compactions": aggregate_result.context_compactions,
            TURN_METADATA_AUTO_CONTINUE_ATTEMPTS_FIELD: auto_continue_attempts,
        }
        completion_metadata = completion_result.to_metadata()
        completion_metadata[TURN_METADATA_AUTO_CONTINUE_ATTEMPTS_FIELD] = auto_continue_attempts
        response_metadata[TURN_METADATA_COMPLETION_GATE_FIELD] = completion_metadata
        if aggregate_result.task_contract is not None:
            response_metadata[TURN_METADATA_TASK_CONTRACT_FIELD] = aggregate_result.task_contract.to_metadata()
        if aggregate_result.tool_evidence:
            response_metadata[TURN_METADATA_TOOL_EVIDENCE_FIELD] = [item.to_metadata() for item in aggregate_result.tool_evidence]
        if aggregate_result.task_artifacts:
            response_metadata[TURN_METADATA_TASK_ARTIFACTS_FIELD] = [item.to_metadata() for item in aggregate_result.task_artifacts]
        status_metadata[TURN_METADATA_COMPLETION_STATUS_FIELD] = completion_result.status
        response_metadata[TURN_METADATA_DELEGATED_TASKS_FIELD] = [task.to_payload() for task in aggregate_result.delegated_tasks]
        response_metadata[TURN_METADATA_ACTIVE_DELEGATE_TASK_ID_FIELD] = aggregate_result.active_delegate_task_id
        response_metadata[TURN_METADATA_ACTIVE_DELEGATE_PROMPT_TYPE_FIELD] = aggregate_result.active_delegate_prompt_type
        if aggregate_result.stop_reason:
            response_metadata["stop_reason"] = aggregate_result.stop_reason
            status_metadata["stop_reason"] = aggregate_result.stop_reason
            if aggregate_result.stop_metadata:
                response_metadata["stop_metadata"] = dict(aggregate_result.stop_metadata)
                status_metadata["stop_metadata"] = dict(aggregate_result.stop_metadata)
        persisted_assistant_metadata = dict(turn.assistant_metadata)
        if aggregate_result.reasoning_details:
            persisted_assistant_metadata["llm_reasoning_details"] = aggregate_result.reasoning_details

        updated_work_state = self.work_progress.update_state(
            session_id=turn.session_id,
            state=current_work_state,
            task_intent=task_intent,
            work_plan=work_plan,
            progress=work_progress,
            completion_result=completion_result,
            delegated_task_updates=aggregate_result.delegated_tasks,
            delegate_task_id=aggregate_result.active_delegate_task_id,
            delegate_prompt_type=aggregate_result.active_delegate_prompt_type,
        )
        run_finish_status = (
            "completed" if is_complete_completion_status(completion_result.status)
            else (completion_result.status or INCOMPLETE_COMPLETION_STATUS)
        )

        async def after_response_saved() -> None:
            await self._save_work_state(updated_work_state)
            if updated_work_state is not None:
                todos = await self.run_trace.record_task_checklist_part(turn.session_id, run_id, updated_work_state)
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    TASK_CHECKLIST_UPDATED_EVENT,
                    {
                        "status": updated_work_state.status,
                        "objective": updated_work_state.objective,
                        "todos": todos,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
            await self._apply_work_progress(turn.session_id, work_progress, updated_work_state)
            await self._apply_completion_gate_result(turn.session_id, completion_result)
            if aggregate_result.task_artifacts:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    TASK_ARTIFACTS_RECORDED_EVENT,
                    {
                        "status": "completed",
                        "count": len(aggregate_result.task_artifacts),
                        "artifacts": [item.to_metadata() for item in aggregate_result.task_artifacts],
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
            self._finalize_learning_reuse(turn.session_id, run_id, True)

        assistant_message = await self.response_finalizer.finalize(
            session_id=turn.session_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
            assistant_metadata=turn.assistant_metadata,
            persisted_assistant_metadata=persisted_assistant_metadata,
            run_part_metadata=response_metadata,
            run_event_payload={"status": run_finish_status, **response_metadata},
            status_metadata=status_metadata,
            images=outbound_media["images"] or None,
            voices=outbound_media["voices"] or None,
            audios=outbound_media["audios"] or None,
            videos=outbound_media["videos"] or None,
            after_save=after_response_saved,
        )
        self._schedule_curator(
            turn.session_id,
            run_id,
            turn.channel,
            turn.external_chat_id,
            aggregate_result,
        )
        return assistant_message

    @staticmethod
    def _extract_follow_up_resume_request(metadata: dict[str, Any] | None) -> dict[str, str] | None:
        payload = dict(metadata or {}) if isinstance(metadata, dict) else {}
        if not metadata_requests_follow_up_resume(payload):
            return None
        workflow = str(payload.get(COMPLETION_RESULT_FOLLOW_UP_WORKFLOW_FIELD) or "").strip()
        start_step = str(payload.get(COMPLETION_RESULT_FOLLOW_UP_STEP_ID_FIELD) or "").strip()
        if not workflow or not start_step:
            return None
        return {
            "workflow": workflow,
            "start_step": start_step,
            "step_label": str(payload.get(COMPLETION_RESULT_FOLLOW_UP_STEP_LABEL_FIELD) or start_step).strip() or start_step,
            "prompt_type": str(payload.get(COMPLETION_RESULT_FOLLOW_UP_PROMPT_TYPE_FIELD) or "").strip(),
            "detail": str(payload.get(COMPLETION_RESULT_ACTIVE_TASK_DETAIL_FIELD) or "").strip(),
            "previous_response": "continue",
        }

    @staticmethod
    def _extract_direct_verify_request(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        payload = dict(metadata or {}) if isinstance(metadata, dict) else {}
        if not metadata_requests_direct_verification(payload):
            return None
        action = str(payload.get(COMPLETION_RESULT_VERIFICATION_ACTION_FIELD) or "").strip()
        if not action:
            return None
        path = str(payload.get(COMPLETION_RESULT_VERIFICATION_PATH_FIELD) or ".").strip() or "."
        pytest_args = tuple(
            str(item or "").strip()
            for item in (payload.get(COMPLETION_RESULT_VERIFICATION_PYTEST_ARGS_FIELD) or payload.get("verificationPytestArgs") or ())
            if str(item or "").strip()
        )
        return {
            "action": action,
            "path": path,
            "pytest_args": pytest_args,
        }

    async def _run_direct_workflow_resume(
        self,
        *,
        run_id: str,
        task_intent: TaskIntent,
        current_work_state: StoredWorkState | None,
        direct_resume: dict[str, str],
        collected_delegated_tasks: tuple[StoredDelegatedTask, ...],
        collected_workflow_outcomes: tuple[dict[str, Any], ...],
    ) -> tuple[str, ExecutionResult, tuple[StoredDelegatedTask, ...], tuple[dict[str, Any], ...]]:
        task_objective = (
            current_work_state.objective
            if current_work_state is not None and current_work_state.objective.strip()
            else task_intent.objective
        )
        workflow_result = await self._run_workflow(
            direct_resume["workflow"],
            task_objective,
            direct_resume["start_step"],
        )
        direct_result = ExecutionResult(content=workflow_result, executed_tool_calls=1)
        delegated_task_updates = self._consume_delegated_task_updates(run_id)
        if delegated_task_updates:
            collected_delegated_tasks = self._merge_delegated_task_updates(
                collected_delegated_tasks,
                delegated_task_updates,
            )
        workflow_outcomes = self._consume_workflow_outcomes(run_id)
        if workflow_outcomes:
            collected_workflow_outcomes = self._merge_workflow_outcomes(
                collected_workflow_outcomes,
                workflow_outcomes,
            )
        if collected_delegated_tasks:
            direct_result = self._with_delegated_tasks(direct_result, collected_delegated_tasks)
        if collected_workflow_outcomes:
            direct_result = self._with_workflow_outcomes(direct_result, collected_workflow_outcomes)
        return workflow_result, direct_result, collected_delegated_tasks, collected_workflow_outcomes

    async def _run_direct_verification(
        self,
        *,
        direct_verify: dict[str, Any],
    ) -> tuple[str, ExecutionResult]:
        result = await self._run_verify(
            str(direct_verify.get("action") or "auto"),
            str(direct_verify.get("path") or "."),
            tuple(str(item or "").strip() for item in (direct_verify.get("pytest_args") or ()) if str(item or "").strip()),
        )
        return result.content, result

    @staticmethod
    def _with_delegated_tasks(
        result: ExecutionResult,
        delegated_tasks: tuple[StoredDelegatedTask, ...],
    ) -> ExecutionResult:
        selected_task = selected_delegated_task(delegated_tasks)
        return replace(
            result,
            delegated_tasks=delegated_tasks,
            active_delegate_task_id=selected_task.task_id if selected_task is not None else None,
            active_delegate_prompt_type=selected_task.prompt_type if selected_task is not None else None,
        )

    @staticmethod
    def _with_workflow_outcomes(
        result: ExecutionResult,
        workflow_outcomes: tuple[dict[str, Any], ...],
    ) -> ExecutionResult:
        return replace(result, workflow_outcomes=workflow_outcomes)

    @staticmethod
    def _merge_delegated_task_updates(
        existing: tuple[StoredDelegatedTask, ...],
        updates: tuple[StoredDelegatedTask, ...],
    ) -> tuple[StoredDelegatedTask, ...]:
        if not updates:
            return existing
        by_id = {task.task_id: task for task in existing if task.task_id}
        order = [task.task_id for task in existing if task.task_id]
        for update in updates:
            if not update.task_id:
                continue
            previous = by_id.pop(update.task_id, None)
            if update.task_id in order:
                order.remove(update.task_id)
            order.append(update.task_id)
            by_id[update.task_id] = StoredDelegatedTask(
                task_id=update.task_id,
                prompt_type=update.prompt_type or (previous.prompt_type if previous is not None else None),
                status=update.status or (previous.status if previous is not None else "unknown"),
                selected=bool(update.selected),
                summary=update.summary or (previous.summary if previous is not None else ""),
                error=(
                    update.error
                    if update.error
                    else ""
                    if update.status and not is_workflow_failed_status(update.status)
                    else previous.error if previous is not None else ""
                ),
                child_session_id=update.child_session_id or (previous.child_session_id if previous is not None else None),
                last_child_run_id=update.last_child_run_id or (previous.last_child_run_id if previous is not None else None),
                metadata={**(previous.metadata if previous is not None else {}), **dict(update.metadata or {})},
                created_at=(
                    previous.created_at
                    if previous is not None and previous.created_at
                    else update.created_at
                ),
                updated_at=update.updated_at or (previous.updated_at if previous is not None else 0.0),
            )
        tasks = tuple(by_id[task_id] for task_id in order if task_id in by_id)
        selected_task = selected_delegated_task(tuple(task for task in reversed(tasks)))
        if selected_task is None:
            return tasks
        return tuple(replace(task, selected=task.task_id == selected_task.task_id) for task in tasks)

    @staticmethod
    def _merge_workflow_outcomes(
        existing: tuple[dict[str, Any], ...],
        updates: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        by_id = {
            str(item.get("workflow_run_id") or "").strip(): dict(item)
            for item in existing
            if isinstance(item, dict) and str(item.get("workflow_run_id") or "").strip()
        }
        order = [
            str(item.get("workflow_run_id") or "").strip()
            for item in existing
            if isinstance(item, dict) and str(item.get("workflow_run_id") or "").strip()
        ]
        for update in updates:
            if not isinstance(update, dict):
                continue
            workflow_run_id = str(update.get("workflow_run_id") or "").strip()
            if not workflow_run_id:
                continue
            if workflow_run_id in order:
                order.remove(workflow_run_id)
            order.append(workflow_run_id)
            by_id[workflow_run_id] = dict(update)
        return tuple(by_id[workflow_run_id] for workflow_run_id in order if workflow_run_id in by_id)

    @staticmethod
    def _aggregate_execution_results(results: list[ExecutionResult], *, content: str) -> ExecutionResult:
        """Aggregate multi-pass execution telemetry while keeping the final response."""
        delegated_tasks = tuple(task for result in results for task in result.delegated_tasks)
        selected_task = selected_delegated_task(delegated_tasks)
        latest_result = results[-1]
        return ExecutionResult(
            content=content,
            executed_tool_calls=sum(result.executed_tool_calls for result in results),
            file_change_count=sum(result.file_change_count for result in results),
            touched_paths=tuple(
                dict.fromkeys(
                    path
                    for result in results
                    for path in result.touched_paths
                )
            ),
            delegated_tasks=delegated_tasks,
            workflow_outcomes=tuple(outcome for result in results for outcome in result.workflow_outcomes),
            active_delegate_task_id=next(
                (
                    result.active_delegate_task_id
                    for result in reversed(results)
                    if result.active_delegate_task_id
                ),
                selected_task.task_id if selected_task is not None else None,
            ),
            active_delegate_prompt_type=next(
                (
                    result.active_delegate_prompt_type
                    for result in reversed(results)
                    if result.active_delegate_prompt_type
                ),
                selected_task.prompt_type if selected_task is not None else None,
            ),
            used_configure_skill=any(result.used_configure_skill for result in results),
            had_tool_error=any(result.had_tool_error for result in results),
            verification_attempted=any(result.verification_attempted for result in results),
            verification_passed=any(result.verification_passed for result in results),
            stop_reason=latest_result.stop_reason,
            stop_metadata=dict(latest_result.stop_metadata or {}) if latest_result.stop_reason else {},
            compaction_handoff=next(
                (
                    result.compaction_handoff
                    for result in reversed(results)
                    if result.compaction_handoff
                ),
                None,
            ),
            context_compactions=sum(result.context_compactions for result in results),
            context_compaction_events=[
                event
                for result in results
                for event in result.context_compaction_events
            ],
            llm_step_events=[
                event
                for result in results
                for event in result.llm_step_events
            ],
            reasoning_details=next(
                (
                    result.reasoning_details
                    for result in reversed(results)
                    if result.reasoning_details
                ),
                None,
            ),
            assistant_internal_only_response=bool(latest_result.assistant_internal_only_response and not content.strip()),
            task_contract=AgentTurnRunner._select_aggregate_task_contract(results),
            harness_policy=next(
                (
                    dict(result.harness_policy)
                    for result in reversed(results)
                    if result.harness_policy is not None
                ),
                None,
            ),
            tool_evidence=tuple(
                evidence
                for result in results
                for evidence in result.tool_evidence
            ),
            task_artifacts=tuple(
                artifact
                for result in results
                for artifact in result.task_artifacts
            ),
        )

    @staticmethod
    def _select_aggregate_task_contract(results: list[ExecutionResult]):
        """Keep the original tool-backed contract when a later retry only finalizes the answer."""
        latest_contract = next(
            (
                result.task_contract
                for result in reversed(results)
                if result.task_contract is not None
            ),
            None,
        )
        validated = next(
            (
                result.task_contract
                for result in reversed(results)
                if (
                    result.task_contract is not None
                    and _task_planner_status(result.task_contract) == PLANNER_VALIDATED_STATUS
                )
            ),
            None,
        )
        if validated is not None and _is_tool_backed_task_contract(validated):
            return validated
        tool_backed_validated = next(
            (
                result.task_contract
                for result in reversed(results)
                if (
                    result.task_contract is not None
                    and _task_planner_status(result.task_contract) == PLANNER_VALIDATED_STATUS
                    and _is_tool_backed_task_contract(result.task_contract)
                )
            ),
            None,
        )
        if tool_backed_validated is not None:
            return tool_backed_validated
        if validated is not None:
            return validated
        return next(
            (
                result.task_contract
                for result in reversed(results)
                if result.task_contract is not None and result.task_contract.task_type != PLANNING_ERROR_TASK_TYPE
            ),
            latest_contract,
        )

    @staticmethod
    def _apply_runtime_progress(exec_result: ExecutionResult, work_progress: dict[str, object]) -> ExecutionResult:
        exec_result.file_change_count = max(
            int(getattr(exec_result, "file_change_count", 0) or 0),
            int(work_progress.get("file_change_count", 0) or 0),
        )
        touched_paths = tuple(
            dict.fromkeys(
                str(path)
                for path in (
                    *getattr(exec_result, "touched_paths", ()),
                    *(work_progress.get("touched_paths", ()) or ()),
                )
                if str(path).strip()
            )
        )
        exec_result.touched_paths = touched_paths
        return exec_result


def _harness_checkpoint_metadata(
    *,
    harness_profile: HarnessProfile | None,
    aggregate_result: ExecutionResult,
    completion_result: CompletionGateResult,
    work_progress: WorkProgressUpdate,
    pass_index: int,
    auto_continue_attempts: int,
) -> dict[str, Any]:
    task_contract = getattr(aggregate_result, "task_contract", None)
    contract_profile = getattr(task_contract, "harness_profile", None)
    profile_metadata = dict(contract_profile) if isinstance(contract_profile, dict) else (
        harness_profile.to_metadata() if harness_profile is not None else None
    )
    return {
        "schema_version": 1,
        "pass_index": max(1, pass_index),
        TURN_METADATA_AUTO_CONTINUE_ATTEMPTS_FIELD: max(0, auto_continue_attempts),
        "harness_profile": profile_metadata,
        "harness_policy": dict(aggregate_result.harness_policy or {}),
        TURN_METADATA_TASK_CONTRACT_FIELD: task_contract.to_metadata() if task_contract is not None else None,
        "completion": completion_result.to_metadata(),
        TURN_METADATA_WORK_PROGRESS_FIELD: work_progress.to_metadata(),
        "next_action": work_progress.next_action,
        "tool_evidence_count": len(aggregate_result.tool_evidence),
        "task_artifact_count": len(aggregate_result.task_artifacts),
    }


def _final_response_after_exhausted_continuation(
    *,
    response: str,
    completion_result: CompletionGateResult,
    auto_continue_attempts: int,
    completion_blocker_messages: CompletionBlockerMessages,
) -> str:
    if not _should_replace_nonfinal_response(
        response=response,
        completion_result=completion_result,
        auto_continue_attempts=auto_continue_attempts,
    ):
        return response
    return completion_blocker_response(completion_result, completion_blocker_messages)


def _message_with_runtime_context(message: str, metadata: dict[str, Any] | None) -> str:
    data = dict(metadata or {})
    if not metadata_is_cli_via_web(data):
        return message
    context_lines: list[str] = []
    gateway_url = str(data.get("gateway_url") or "").strip()
    if gateway_url:
        health_url = join_url_path(gateway_url, "/healthz")
        context_lines.append(
            f"OpenSprite CLI is connected to the Web gateway at {gateway_url}; "
            f"use {health_url} for health endpoint checks."
        )
    snapshot = data.get("workspace_snapshot")
    if isinstance(snapshot, dict):
        snapshot_path = str(snapshot.get("path") or "").strip()
        snapshot_source = str(snapshot.get("source") or "").strip()
        if snapshot_path:
            context_lines.append(
                f"The requested workspace snapshot is available inside this session at `{snapshot_path}/`."
            )
        if snapshot_source:
            context_lines.append(f"The snapshot came from local path `{snapshot_source}`.")
        context_lines.append("Snapshot copies omit VCS internals such as `.git`.")
    if not context_lines:
        return message
    return f"{message}\n\n[Runtime context]\n" + "\n".join(f"- {line}" for line in context_lines)


def _source_finalization_available(
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult | None,
) -> bool:
    return bool(_source_finalization_sources(completion_result, execution_result))


def _source_finalization_sources(
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult | None,
) -> list[dict[str, Any]]:
    if execution_result is None:
        return []
    if not source_finalization_allowed(completion_result, execution_result):
        return []
    evidence_urls = _completion_evidence_urls(completion_result)
    objective = _execution_objective(execution_result)
    sources = _merge_web_sources(
        _substantive_web_sources(execution_result),
        _merge_web_sources(
            _web_sources_matching_evidence_urls(execution_result, evidence_urls),
            _web_sources_matching_base_url_context(execution_result, objective),
        ),
    )
    if not sources:
        return []
    sources = rank_web_sources_for_objective(sources, objective)
    if execution_result.had_tool_error:
        top_score = web_source_relevance_score(sources[0], objective) if sources else 0
        if top_score <= 0:
            return []
    return sources


def _substantive_web_sources(execution_result: ExecutionResult) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or not is_web_source_artifact_kind(artifact.kind):
            continue
        raw_sources = artifact.metadata.get("sources") if isinstance(artifact.metadata, dict) else None
        if not isinstance(raw_sources, list):
            continue
        for raw_source in raw_sources:
            if not isinstance(raw_source, dict):
                continue
            url = str(raw_source.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            content_chars = _coerce_positive_int(raw_source.get("content_chars"))
            is_too_short = bool(raw_source.get("is_too_short"))
            has_main_content = bool(raw_source.get("has_main_content"))
            if (
                is_web_fetch_source_record_tool(raw_source.get("tool_name"))
                and (content_chars >= 800 or has_main_content)
                and not is_too_short
            ):
                seen_urls.add(url)
                sources.append(raw_source)
    return sources


def _completion_evidence_urls(completion_result: CompletionGateResult) -> tuple[str, ...]:
    text = " ".join(
        (
            str(completion_result.reason or ""),
            str(completion_result.active_task_detail or ""),
            " ".join(str(item or "") for item in completion_result.missing_evidence),
        )
    )
    return tuple(dict.fromkeys(_extract_urls(text)))


def _merge_web_sources(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for source in (*primary, *secondary):
        url = str(source.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        merged.append(source)
    return merged


def _web_sources_matching_evidence_urls(
    execution_result: ExecutionResult,
    evidence_urls: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not evidence_urls:
        return []
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or not is_web_source_artifact_kind(artifact.kind):
            continue
        raw_sources = artifact.metadata.get("sources") if isinstance(artifact.metadata, dict) else None
        if not isinstance(raw_sources, list):
            continue
        for raw_source in raw_sources:
            if not isinstance(raw_source, dict):
                continue
            url = str(raw_source.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            haystack = str(raw_source.get("snippet") or raw_source.get("content") or "")
            if any(evidence_url in haystack for evidence_url in evidence_urls):
                seen_urls.add(url)
                sources.append(raw_source)
    return sources


def _web_sources_matching_base_url_context(
    execution_result: ExecutionResult,
    objective: str,
) -> list[dict[str, Any]]:
    if not _objective_requests_base_url(objective):
        return []
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or not is_web_source_artifact_kind(artifact.kind):
            continue
        raw_sources = artifact.metadata.get("sources") if isinstance(artifact.metadata, dict) else None
        if not isinstance(raw_sources, list):
            continue
        for raw_source in raw_sources:
            if not isinstance(raw_source, dict):
                continue
            url = str(raw_source.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            if _source_base_url_candidates([raw_source]):
                seen_urls.add(url)
                sources.append(raw_source)
    return sources


def _objective_requests_base_url(objective: str) -> bool:
    text = str(objective or "").lower()
    return "base url" in text or "base_url" in text or "api base" in text


def _source_base_url_candidates(sources: list[dict[str, Any]]) -> list[str]:
    candidates: list[str] = []
    for source in sources:
        text = str(source.get("snippet") or source.get("content") or "")
        for match in re.finditer(r"https?://\S+", text):
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end].lower()
            if "base url" not in context and "base_url" not in context and "api base" not in context:
                continue
            candidates.append(_clean_extracted_url(match.group(0)))
    return candidates


def _extract_urls(text: str) -> list[str]:
    return [_clean_extracted_url(match.group(0)) for match in re.finditer(r"https?://\S+", str(text or ""))]


def _clean_extracted_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,;:)]}>\"'")


def _execution_objective(execution_result: ExecutionResult) -> str:
    task_contract = getattr(execution_result, "task_contract", None)
    return str(getattr(task_contract, "objective", "") or "").strip()


def _coerce_positive_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _should_replace_nonfinal_response(
    *,
    response: str,
    completion_result: CompletionGateResult,
    auto_continue_attempts: int,
) -> bool:
    if is_complete_completion_status(completion_result.status):
        return False
    if not (response or "").strip():
        return True
    if is_blocking_completion_status(completion_result.status):
        return False
    return allows_nonfinal_response_replacement(completion_result.status)


def _harness_scorecard_metadata(
    *,
    harness_profile: HarnessProfile | None,
    aggregate_result: ExecutionResult,
    completion_result: CompletionGateResult,
) -> dict[str, Any]:
    task_contract = getattr(aggregate_result, "task_contract", None)
    contract_profile = getattr(task_contract, "harness_profile", None)
    profile_metadata = dict(contract_profile) if isinstance(contract_profile, dict) else (
        harness_profile.to_metadata() if harness_profile is not None else {}
    )
    task_type = str(profile_metadata.get("task_type") or "")
    sensors = evaluate_harness_sensors(
        task_type=task_type,
        execution_result=aggregate_result,
        completion_result=completion_result,
    )
    scorecard = HarnessScorecard(
        profile=profile_metadata,
        contract=task_contract.to_metadata() if task_contract is not None else {},
        tools={
            "executed_tool_calls": aggregate_result.executed_tool_calls,
            "had_tool_error": aggregate_result.had_tool_error,
            "file_change_count": aggregate_result.file_change_count,
            "tool_evidence_count": len(aggregate_result.tool_evidence),
            "task_artifact_count": len(aggregate_result.task_artifacts),
        },
        permissions={
            "harness_policy": dict(aggregate_result.harness_policy or {}),
        },
        sensors=sensors,
        completion=completion_result.to_metadata(),
        trace_health=_harness_trace_health(
            has_profile=harness_profile is not None,
            has_contract=task_contract is not None,
            has_completion=bool(completion_result.status),
            sensors=sensors,
        ),
    )
    return scorecard.to_metadata()


def _harness_trace_health(
    *,
    has_profile: bool,
    has_contract: bool,
    has_completion: bool,
    sensors: tuple[HarnessSensorResult, ...],
) -> dict[str, Any]:
    sensor_statuses = [sensor.status for sensor in sensors]
    missing_sections = [
        section
        for section, present in (
            ("profile", has_profile),
            ("contract", has_contract),
            ("completion", has_completion),
        )
        if not present
    ]
    status = "pass"
    if missing_sections or "fail" in sensor_statuses:
        status = "fail"
    elif "warn" in sensor_statuses or "not_applicable" in sensor_statuses:
        status = "warn"
    return {
        "status": status,
        "has_profile": has_profile,
        "has_contract": has_contract,
        "has_completion": has_completion,
        "missing_sections": missing_sections,
        "sensor_counts": {
            "pass": sensor_statuses.count("pass"),
            "warn": sensor_statuses.count("warn"),
            "fail": sensor_statuses.count("fail"),
            "not_applicable": sensor_statuses.count("not_applicable"),
        },
    }


def _task_planner_status(task_contract: Any) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get(PLANNER_METADATA_STATUS_FIELD) or "").strip()
    return ""


def _is_tool_backed_task_contract(task_contract: Any) -> bool:
    task_type = str(getattr(task_contract, "task_type", "") or "").strip()
    if task_type in {"", PURE_ANSWER_TASK_TYPE, PLANNING_ERROR_TASK_TYPE}:
        return False
    return True


def _can_replace_initial_work_state(state: StoredWorkState | None) -> bool:
    if state is None:
        return True
    metadata = state.metadata if isinstance(state.metadata, dict) else {}
    return (
        metadata_is_work_progress_source(metadata)
        and not str(metadata.get("harness_profile") or "").strip()
        and not state.completed_steps
        and not state.blockers
        and int(state.file_change_count or 0) == 0
        and not state.touched_paths
        and not state.delegated_tasks
    )


# Active task and structured work progress state.
NEXT_ACTION_FINALIZE = "finalize"
NEXT_ACTION_STOP_BUDGET_EXHAUSTED = "stop_budget_exhausted"
NEXT_ACTION_STOP_NO_PROGRESS = "stop_no_progress"
NEXT_ACTION_CONTINUE_VERIFICATION = "continue_verification"
NEXT_ACTION_COLLECT_REVIEW_EVIDENCE = "collect_review_evidence"
NEXT_ACTION_ADDRESS_REVIEW_FINDINGS = "address_review_findings"
NEXT_ACTION_CONTINUE_REVIEW = "continue_review"
NEXT_ACTION_CONTINUE_WORK = "continue_work"
DEFAULT_WORK_STEP_NOT_SET = "not set"
TASK_DONE_RESUME_HINT = "Task is complete; only continue if the user asks for follow-up work."
VERIFICATION_REQUIRED_RESUME_HINT = "Resume by running or fixing the required verification."
REVIEW_FOLLOW_UP_NEXT_ACTIONS = frozenset(
    {
        NEXT_ACTION_COLLECT_REVIEW_EVIDENCE,
        NEXT_ACTION_ADDRESS_REVIEW_FINDINGS,
    }
)
REVIEW_PHASE_NEXT_ACTIONS = frozenset(
    {
        NEXT_ACTION_CONTINUE_REVIEW,
        *REVIEW_FOLLOW_UP_NEXT_ACTIONS,
    }
)
_DEFAULT_VERIFICATION_TARGET = "relevant tests or checks pass, or the verification gap is stated"
_WORK_STATE_ACTIVE_STATUS = "active"
_WORK_STATE_DONE_STATUS = "done"
_WORK_PROGRESS_VERIFYING_STATUS = "verifying"
_WORK_PROGRESS_REVIEWING_STATUS = "reviewing"
_WORK_PROGRESS_WORKING_STATUS = "working"
_NEXT_ACTION_FINALIZE = NEXT_ACTION_FINALIZE
_NEXT_ACTION_STOP_BUDGET_EXHAUSTED = NEXT_ACTION_STOP_BUDGET_EXHAUSTED
_NEXT_ACTION_STOP_NO_PROGRESS = NEXT_ACTION_STOP_NO_PROGRESS
_NEXT_ACTION_CONTINUE_VERIFICATION = NEXT_ACTION_CONTINUE_VERIFICATION
_NEXT_ACTION_COLLECT_REVIEW_EVIDENCE = NEXT_ACTION_COLLECT_REVIEW_EVIDENCE
_NEXT_ACTION_ADDRESS_REVIEW_FINDINGS = NEXT_ACTION_ADDRESS_REVIEW_FINDINGS
_NEXT_ACTION_CONTINUE_REVIEW = NEXT_ACTION_CONTINUE_REVIEW
_NEXT_ACTION_CONTINUE_WORK = NEXT_ACTION_CONTINUE_WORK
_PROGRESS_SIGNAL_TOOL_CALLS = "tool_calls"
_PROGRESS_SIGNAL_FILE_CHANGES = "file_changes"
_PROGRESS_SIGNAL_VERIFICATION_ATTEMPTED = "verification_attempted"
_PROGRESS_SIGNAL_VERIFICATION_PASSED = "verification_passed"
_PROGRESS_SIGNAL_CONTEXT_COMPACTION = "context_compaction"
_PROGRESS_SIGNAL_TOOL_ERROR = "tool_error"
WORK_PROGRESS_METADATA_SOURCE_KEY = "source"
WORK_PROGRESS_METADATA_SOURCE = "work_progress"
WORK_PROGRESS_ACTIVE_TASK_EVENT_TYPE = "work_progress"
WORK_STEP_NOT_SET = "not set"


def normalize_next_action(value: str | None) -> str:
    return str(value or "").strip()


def is_verification_next_action(value: str | None) -> bool:
    return normalize_next_action(value) == NEXT_ACTION_CONTINUE_VERIFICATION


def is_continue_work_next_action(value: str | None) -> bool:
    return normalize_next_action(value) == NEXT_ACTION_CONTINUE_WORK


def is_review_follow_up_next_action(value: str | None) -> bool:
    return normalize_next_action(value) in REVIEW_FOLLOW_UP_NEXT_ACTIONS


def is_review_phase_next_action(value: str | None) -> bool:
    return normalize_next_action(value) in REVIEW_PHASE_NEXT_ACTIONS


def build_policy_resume_hint(
    *,
    status: str,
    current_step: str,
    next_step: str,
    blockers: tuple[str, ...],
    next_action: str,
    workflow: str = "",
    step_label: str = "",
    prompt_type: str = "",
    verification_action: str = "",
    verification_path: str = "",
    done_status: str = "done",
    unset_step: str = DEFAULT_WORK_STEP_NOT_SET,
) -> str:
    if status == done_status:
        return TASK_DONE_RESUME_HINT
    if blockers:
        return f"Resolve blocker first: {blockers[0]}"
    if is_verification_next_action(next_action):
        if workflow and step_label:
            return f"Resume by finishing verification around the {step_label} step in {workflow}."
        if verification_action and verification_path:
            return f"Resume by running verify {verification_action} for `{verification_path}`."
        return VERIFICATION_REQUIRED_RESUME_HINT
    if normalize_next_action(next_action) == NEXT_ACTION_COLLECT_REVIEW_EVIDENCE:
        if workflow and step_label and prompt_type:
            return f"Resume by running or rerunning the delegated {prompt_type} step ({step_label}) for {workflow}."
        if prompt_type:
            return f"Resume by running or rerunning the delegated {prompt_type} step for the changed code."
        return "Resume by running or rerunning a delegated review step for the changed code."
    if normalize_next_action(next_action) == NEXT_ACTION_ADDRESS_REVIEW_FINDINGS:
        if workflow:
            return f"Resume by addressing the review findings for {workflow} before rerunning review if needed."
        return "Resume by addressing the delegated review findings before treating the task as complete."
    if normalize_next_action(next_action) == NEXT_ACTION_CONTINUE_REVIEW:
        return "Resume by collecting review evidence or addressing delegated review findings."
    if workflow and step_label:
        return f"Resume with the {step_label} step in {workflow}."
    if current_step and current_step != unset_step:
        return f"Resume at current step: {current_step}"
    if next_step and next_step != unset_step:
        return f"Resume with next step: {next_step}"
    return "Continue the active task from the latest recorded state."


build_resume_hint = build_policy_resume_hint


def is_verification_work_progress(progress: Any) -> bool:
    """Return whether a structured progress update is in the verification phase."""
    return (
        is_verification_next_action(getattr(progress, "next_action", None))
        or str(getattr(progress, "status", "") or "").strip() == _WORK_PROGRESS_VERIFYING_STATUS
    )


def is_continue_work_progress(progress: Any) -> bool:
    """Return whether a structured progress update should resume regular work."""
    return is_continue_work_next_action(getattr(progress, "next_action", None))


def metadata_is_work_progress_source(metadata: dict[str, Any]) -> bool:
    """Return whether metadata was created by the structured work-progress service."""
    return str(metadata.get(WORK_PROGRESS_METADATA_SOURCE_KEY) or "").strip() == WORK_PROGRESS_METADATA_SOURCE


def _delegated_tasks_for_state(state: StoredWorkState | None) -> tuple[StoredDelegatedTask, ...]:
    if state is None:
        return ()
    return coerce_stored_delegated_tasks(state.delegated_tasks) or legacy_delegated_tasks(
        state.active_delegate_task_id,
        state.active_delegate_prompt_type,
    )


def _merge_delegated_tasks(
    existing_tasks: tuple[StoredDelegatedTask, ...],
    updates: tuple[StoredDelegatedTask, ...],
    *,
    clear_selection: bool,
) -> tuple[StoredDelegatedTask, ...]:
    by_id: dict[str, StoredDelegatedTask] = {task.task_id: task for task in existing_tasks if task.task_id}
    order = [task.task_id for task in existing_tasks if task.task_id]
    normalized_updates = coerce_stored_delegated_tasks(updates)
    now = time.time()

    for update in normalized_updates:
        previous = by_id.pop(update.task_id, None)
        if update.task_id in order:
            order.remove(update.task_id)
        order.append(update.task_id)
        by_id[update.task_id] = StoredDelegatedTask(
            task_id=update.task_id,
            prompt_type=update.prompt_type or (previous.prompt_type if previous is not None else None),
            status=update.status or (previous.status if previous is not None else "unknown"),
            selected=bool(update.selected),
            summary=update.summary or (previous.summary if previous is not None else ""),
            error=(
                update.error
                if update.error
                else ""
                if update.status and not is_workflow_failed_status(update.status)
                else previous.error if previous is not None else ""
            ),
            child_session_id=(
                update.child_session_id
                or previous.child_session_id if previous is not None else None
            ),
            last_child_run_id=(
                update.last_child_run_id
                or previous.last_child_run_id if previous is not None else None
            ),
            metadata={**(previous.metadata if previous is not None else {}), **dict(update.metadata or {})},
            created_at=(
                previous.created_at
                if previous is not None and previous.created_at
                else update.created_at
                or now
            ),
            updated_at=update.updated_at or now,
        )

    tasks = tuple(by_id[task_id] for task_id in order if task_id in by_id)
    if clear_selection:
        return tuple(replace(task, selected=False) for task in tasks)
    if normalized_updates:
        selected_task_id = next((task.task_id for task in reversed(normalized_updates) if task.selected), normalized_updates[-1].task_id)
        return tuple(replace(task, selected=task.task_id == selected_task_id) for task in tasks)
    return tasks


def _continues_existing_task(task_context_decision: TaskContextDecision | None) -> bool:
    if task_context_decision is None:
        return False
    return bool(
        task_context_decision.should_inherit_active_task
        or task_context_decision.continuation_type == CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE
    )


@dataclass(frozen=True)
class WorkPlan:
    """Small durable plan derived from the user intent."""

    objective: str
    kind: str
    steps: tuple[str, ...]
    constraints: tuple[str, ...]
    done_criteria: tuple[str, ...]
    long_running: bool
    coding_task: bool
    expects_code_change: bool
    expects_verification: bool
    harness_profile: str = ""
    verification_policy: str = ""
    continuation_policy: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "objective": self.objective,
            "kind": self.kind,
            "steps": list(self.steps),
            "constraints": list(self.constraints),
            "done_criteria": list(self.done_criteria),
            "long_running": self.long_running,
            "coding_task": self.coding_task,
            "expects_code_change": self.expects_code_change,
            "expects_verification": self.expects_verification,
            "harness_profile": self.harness_profile,
            "verification_policy": self.verification_policy,
            "continuation_policy": self.continuation_policy,
        }


@dataclass(frozen=True)
class WorkProgressUpdate:
    """One pass worth of structured progress signals."""

    status: str
    pass_index: int
    auto_continue_attempts: int
    progress_signals: tuple[str, ...]
    has_progress: bool
    file_change_count: int
    touched_paths: tuple[str, ...]
    verification_required: bool
    verification_attempted: bool
    verification_passed: bool
    completion_status: str
    completion_reason: str
    next_action: str
    continuation_budget: int

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "pass_index": self.pass_index,
            "auto_continue_attempts": self.auto_continue_attempts,
            "progress_signals": list(self.progress_signals),
            "has_progress": self.has_progress,
            "file_change_count": self.file_change_count,
            "touched_paths": list(self.touched_paths),
            "verification_required": self.verification_required,
            "verification_attempted": self.verification_attempted,
            "verification_passed": self.verification_passed,
            "completion_status": self.completion_status,
            "completion_reason": self.completion_reason,
            "next_action": self.next_action,
            "continuation_budget": self.continuation_budget,
        }


@dataclass(frozen=True)
class WorkboardState:
    """Typed durable view of remaining work for one task."""

    pending_steps: tuple[str, ...] = ()
    completed_steps: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    verification_targets: tuple[str, ...] = ()
    resume_hint: str = ""
    last_progress_signals: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "pending_steps": list(self.pending_steps),
            "completed_steps": list(self.completed_steps),
            "blockers": list(self.blockers),
            "verification_targets": list(self.verification_targets),
            "resume_hint": self.resume_hint,
            "last_progress_signals": list(self.last_progress_signals),
        }


class WorkProgressService:
    """Create a coherent work state from intent, execution, and completion signals."""

    def __init__(self, *, default_continuation_budget: int = 1, long_running_continuation_budget: int = 3):
        self.default_continuation_budget = max(0, default_continuation_budget)
        self.long_running_continuation_budget = max(self.default_continuation_budget, long_running_continuation_budget)

    def create_plan(self, task_intent: TaskIntent, harness_profile: HarnessProfile | None = None) -> WorkPlan | None:
        """Return a plan only for actionable tasks, not casual conversation."""
        profile_name = normalize_profile_name(harness_profile.name if harness_profile is not None else "")
        if is_chat_profile_name(profile_name):
            return None
        if not _intent_supports_default_work_plan(task_intent) and profile_name == "":
            return None

        steps: list[str]
        if is_research_profile_name(profile_name):
            steps = ["search for relevant sources", "fetch or inspect source details", "answer with cited evidence"]
        elif is_coding_profile_name(profile_name):
            steps = [
                "inspect relevant workspace context",
                "make the smallest correct change or collect concrete workspace evidence",
                "run focused verification or state the verification gap",
                "summarize changes, evidence, and remaining risk",
            ]
        elif is_media_profile_name(profile_name):
            steps = ["inspect the referenced media", "produce the required media artifact", "answer using the artifact result"]
        elif is_ops_profile_name(profile_name):
            steps = ["inspect the requested operation", "obtain or honor required approval", "execute and validate", "report outcome and risk"]
        elif task_intent.kind == ANALYSIS_TASK_TYPE:
            steps = ["inspect the relevant context", "collect concrete evidence", "deliver the findings clearly"]
        elif task_intent.long_running:
            steps = ["make measurable progress", "verify or summarize remaining work"]
        else:
            steps = ["complete the requested task"]

        if harness_profile is not None:
            expects_code_change = _profile_requires_code_change(harness_profile)
            expects_verification = _profile_requires_verification(harness_profile)
        else:
            expects_code_change = False
            expects_verification = False
        done_criteria = list(task_intent.done_criteria)
        verification_done = _DEFAULT_VERIFICATION_TARGET
        if expects_verification and verification_done not in done_criteria:
            done_criteria.append(verification_done)

        return WorkPlan(
            objective=task_intent.objective,
            kind=task_intent.kind,
            steps=tuple(steps),
            constraints=tuple(task_intent.constraints),
            done_criteria=tuple(done_criteria),
            long_running=task_intent.long_running
            or is_coding_profile_name(profile_name)
            or is_research_profile_name(profile_name),
            coding_task=is_coding_profile_name(profile_name) or task_intent.expects_code_change or task_intent.expects_verification,
            expects_code_change=expects_code_change,
            expects_verification=expects_verification,
            harness_profile=profile_name,
            verification_policy=harness_profile.verification_policy if harness_profile is not None else "",
            continuation_policy=harness_profile.continuation_policy if harness_profile is not None else "",
        )

    def resolve_intent(
        self,
        task_intent: TaskIntent,
        state: StoredWorkState | None,
        *,
        task_context_decision: TaskContextDecision | None = None,
    ) -> TaskIntent:
        """Reuse persisted task semantics when structured context says this turn continues it."""
        if (
            state is None
            or not is_current_active_task_status(state.status)
            or not _continues_existing_task(task_context_decision)
        ):
            return task_intent
        return TaskIntent(
            kind=state.kind,
            objective=state.objective,
            constraints=tuple(state.constraints),
            done_criteria=tuple(state.done_criteria),
            needs_clarification=False,
            verification_hint=(
                task_intent.verification_hint
                or ("Run the requested verification and report pass or fail." if state.expects_verification else None)
            ),
            long_running=bool(state.long_running),
            expects_code_change=bool(state.expects_code_change),
            expects_verification=bool(state.expects_verification),
        )

    def build_initial_state(
        self,
        *,
        session_id: str,
        task_intent: TaskIntent,
        work_plan: WorkPlan | None,
        existing_state: StoredWorkState | None = None,
        task_context_decision: TaskContextDecision | None = None,
    ) -> StoredWorkState | None:
        """Create a new persisted state when a concrete task begins."""
        if work_plan is None:
            if existing_state is not None and task_intent.needs_clarification and task_intent.long_running:
                return existing_state
            return None
        if self._should_resume_existing_state(task_intent, work_plan, existing_state):
            return self._resume_existing_state(existing_state, work_plan)
        if self._should_preserve_existing_state(existing_state, task_context_decision):
            return self._resume_existing_state(existing_state, work_plan)
        if existing_state is not None and task_intent.needs_clarification and task_intent.long_running:
            return existing_state

        numbered_steps = _numbered_steps(work_plan.steps)
        now = time.time()
        pending_steps = tuple(step for step in numbered_steps if step != WORK_STEP_NOT_SET)
        return StoredWorkState(
            session_id=session_id,
            objective=work_plan.objective,
            kind=work_plan.kind,
            status="active",
            steps=numbered_steps,
            constraints=tuple(work_plan.constraints),
            done_criteria=tuple(work_plan.done_criteria),
            long_running=work_plan.long_running,
            coding_task=work_plan.coding_task,
            expects_code_change=work_plan.expects_code_change,
            expects_verification=work_plan.expects_verification,
            current_step=numbered_steps[0] if numbered_steps else WORK_STEP_NOT_SET,
            next_step=numbered_steps[1] if len(numbered_steps) > 1 else WORK_STEP_NOT_SET,
            completed_steps=(),
            pending_steps=pending_steps,
            blockers=(),
            verification_targets=_derive_verification_targets(
                work_plan.done_criteria,
                expects_verification=work_plan.expects_verification,
            ),
            resume_hint=_build_resume_hint(
                status="active",
                current_step=numbered_steps[0] if numbered_steps else WORK_STEP_NOT_SET,
                next_step=numbered_steps[1] if len(numbered_steps) > 1 else WORK_STEP_NOT_SET,
                blockers=(),
                next_action=_NEXT_ACTION_CONTINUE_WORK,
            ),
            last_progress_signals=(),
            file_change_count=0,
            touched_paths=(),
            verification_attempted=False,
            verification_passed=False,
            last_next_action=_NEXT_ACTION_CONTINUE_WORK,
            metadata={
                WORK_PROGRESS_METADATA_SOURCE_KEY: WORK_PROGRESS_METADATA_SOURCE,
                "schema_version": 1,
                "harness_profile": work_plan.harness_profile,
                "verification_policy": work_plan.verification_policy,
                "continuation_policy": work_plan.continuation_policy,
            },
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def extract_workboard(state: StoredWorkState | None) -> WorkboardState:
        """Return the normalized structured workboard metadata for one state."""
        if state is None:
            return WorkboardState()
        legacy = _legacy_workboard(state)
        pending_steps = tuple(state.pending_steps) or tuple(_string_list(legacy.get("pending_steps")))
        blockers = tuple(state.blockers) or tuple(_string_list(legacy.get("blockers")))
        verification_targets = tuple(state.verification_targets) or tuple(_string_list(legacy.get("verification_targets")))
        resume_hint = state.resume_hint or str(legacy.get("resume_hint") or "")
        last_progress_signals = tuple(state.last_progress_signals) or tuple(_string_list(legacy.get("last_progress_signals")))
        if not pending_steps:
            pending_steps = tuple(
                step for step in state.steps if step not in state.completed_steps and step != WORK_STEP_NOT_SET
            )
        if not blockers and is_blocking_completion_status(state.status) and state.last_next_action:
            blockers = (state.last_next_action,)
        if not verification_targets:
            verification_targets = _derive_verification_targets(
                state.done_criteria,
                expects_verification=state.expects_verification,
            )
        if not resume_hint:
            resume_hint = _build_resume_hint(
                status=state.status,
                current_step=state.current_step,
                next_step=state.next_step,
                blockers=blockers,
                next_action=state.last_next_action,
            )
        return WorkboardState(
            pending_steps=pending_steps,
            completed_steps=tuple(state.completed_steps),
            blockers=blockers,
            verification_targets=verification_targets,
            resume_hint=resume_hint,
            last_progress_signals=last_progress_signals,
        )

    def update_state(
        self,
        *,
        session_id: str,
        state: StoredWorkState | None,
        task_intent: TaskIntent,
        work_plan: WorkPlan | None,
        progress: WorkProgressUpdate,
        completion_result: CompletionGateResult,
        delegated_task_updates: tuple[StoredDelegatedTask, ...] = (),
        delegate_task_id: str | None = None,
        delegate_prompt_type: str | None = None,
    ) -> StoredWorkState | None:
        """Apply one turn's progress and completion result to persisted work state."""
        current = state or self.build_initial_state(
            session_id=session_id,
            task_intent=task_intent,
            work_plan=work_plan,
        )
        if current is None:
            return None

        steps = tuple(current.steps)
        status = _map_state_status(completion_result, progress)
        completed_steps = _completed_steps(
            steps,
            current.completed_steps,
            progress,
            expects_code_change=current.expects_code_change,
        )
        current_step, next_step = _state_steps(
            steps,
            progress,
            expects_code_change=current.expects_code_change,
            expects_verification=current.expects_verification,
        )
        touched_paths = tuple(dict.fromkeys((*current.touched_paths, *progress.touched_paths)))
        file_change_count = max(0, current.file_change_count + progress.file_change_count)
        verification_attempted = current.verification_attempted or progress.verification_attempted
        verification_passed = current.verification_passed or progress.verification_passed
        if progress.file_change_count > 0 and current.expects_verification and not progress.verification_passed:
            verification_passed = False

        if not delegated_task_updates and delegate_task_id:
            delegated_task_updates = (
                StoredDelegatedTask(
                    task_id=delegate_task_id,
                    prompt_type=delegate_prompt_type,
                    status="unknown",
                    selected=True,
                    updated_at=time.time(),
                ),
            )
        delegated_tasks = _merge_delegated_tasks(
            _delegated_tasks_for_state(current),
            delegated_task_updates,
            clear_selection=is_complete_completion_status(completion_result.status),
        )
        selected_task = selected_delegated_task(delegated_tasks)

        metadata = dict(current.metadata or {})
        metadata.pop("workboard", None)
        metadata = _apply_structured_follow_up_metadata(metadata, completion_result)
        workboard = self._build_workboard(
            steps=steps,
            completed_steps=completed_steps,
            status=status,
            current_step=current_step,
            next_step=next_step,
            done_criteria=current.done_criteria,
            expects_verification=current.expects_verification,
            progress=progress,
            completion_result=completion_result,
        )

        return StoredWorkState(
            session_id=current.session_id,
            objective=current.objective,
            kind=current.kind,
            status=status,
            steps=steps,
            constraints=tuple(current.constraints),
            done_criteria=tuple(current.done_criteria),
            long_running=current.long_running,
            coding_task=current.coding_task,
            expects_code_change=current.expects_code_change,
            expects_verification=current.expects_verification,
            current_step=current_step,
            next_step=next_step,
            completed_steps=completed_steps,
            pending_steps=workboard.pending_steps,
            blockers=workboard.blockers,
            verification_targets=workboard.verification_targets,
            resume_hint=workboard.resume_hint,
            last_progress_signals=workboard.last_progress_signals,
            file_change_count=file_change_count,
            touched_paths=touched_paths,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
            last_next_action=progress.next_action,
            delegated_tasks=delegated_tasks,
            active_delegate_task_id=selected_task.task_id if selected_task is not None else None,
            active_delegate_prompt_type=selected_task.prompt_type if selected_task is not None else None,
            metadata=metadata,
            created_at=current.created_at or time.time(),
            updated_at=time.time(),
        )

    @staticmethod
    def render_state_summary(state: StoredWorkState | None) -> str:
        """Render a compact state block that can survive compaction and retries."""
        if state is None:
            return ""
        lines = [
            "## Structured Work State",
            f"- Objective: {state.objective}",
            f"- Kind: {state.kind}",
            f"- Status: {state.status}",
            f"- Current step: {state.current_step}",
            f"- Next step: {state.next_step}",
            f"- Verification: attempted={state.verification_attempted} passed={state.verification_passed}",
            f"- Last next action: {state.last_next_action or 'none'}",
        ]
        if state.constraints:
            lines.extend(["- Constraints:", *[f"  - {item}" for item in state.constraints]])
        if state.done_criteria:
            lines.extend(["- Definition of done:", *[f"  - {item}" for item in state.done_criteria]])
        if state.completed_steps:
            lines.extend(["- Completed steps:", *[f"  - {step}" for step in state.completed_steps]])
        workboard = WorkProgressService.extract_workboard(state)
        if workboard.pending_steps:
            lines.extend(["- Pending steps:", *[f"  - {step}" for step in workboard.pending_steps]])
        if workboard.verification_targets:
            lines.extend(["- Verification targets:", *[f"  - {item}" for item in workboard.verification_targets]])
        if workboard.blockers:
            lines.extend(["- Blockers:", *[f"  - {item}" for item in workboard.blockers]])
        if workboard.resume_hint:
            lines.append(f"- Resume hint: {workboard.resume_hint}")
        if state.touched_paths:
            lines.extend(["- Touched paths:", *[f"  - {path}" for path in state.touched_paths[:12]]])
        delegated_tasks = _delegated_tasks_for_state(state)
        selected_task = selected_delegated_task(delegated_tasks)
        if selected_task is not None:
            lines.append(
                f"- Active delegate: {selected_task.prompt_type or 'subagent'} ({selected_task.task_id})"
            )
        elif delegated_tasks:
            lines.append(f"- Delegated tasks tracked: {len(delegated_tasks)}")
        return "\n".join(lines)

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        execution_result: ExecutionResult,
        auto_continue_attempts: int,
        pass_index: int,
        harness_profile: HarnessProfile | None = None,
    ) -> WorkProgressUpdate:
        """Summarize the current pass and choose the next high-level action."""
        signals = self._progress_signals(execution_result)
        continuation_budget = self.continuation_budget(task_intent, harness_profile=harness_profile)
        if _completion_gap_should_get_one_retry(completion_result):
            continuation_budget = max(continuation_budget, self.default_continuation_budget)
        if requires_evidence_follow_up(completion_result.status) or completion_result.verification_required or completion_result.review_required:
            continuation_budget = max(continuation_budget, self.long_running_continuation_budget)
        status = self._status(completion_result)
        return WorkProgressUpdate(
            status=status,
            pass_index=max(1, pass_index),
            auto_continue_attempts=max(0, auto_continue_attempts),
            progress_signals=signals,
            has_progress=bool(signals),
            file_change_count=max(0, execution_result.file_change_count),
            touched_paths=tuple(execution_result.touched_paths),
            verification_required=completion_result.verification_required,
            verification_attempted=completion_result.verification_attempted,
            verification_passed=completion_result.verification_passed,
            completion_status=completion_result.status,
            completion_reason=completion_result.reason,
            next_action=self._next_action(completion_result, has_progress=bool(signals), attempts=auto_continue_attempts, budget=continuation_budget),
            continuation_budget=continuation_budget,
        )

    def continuation_budget(self, task_intent: TaskIntent, harness_profile: HarnessProfile | None = None) -> int:
        profile_name = normalize_profile_name(harness_profile.name if harness_profile is not None else "")
        if is_chat_profile_name(profile_name):
            return 0
        if is_coding_profile_name(profile_name) or is_research_profile_name(profile_name):
            return self.long_running_continuation_budget
        if is_media_profile_name(profile_name) or is_ops_profile_name(profile_name):
            return self.default_continuation_budget
        if task_intent.long_running or task_intent.expects_code_change or task_intent.expects_verification:
            return self.long_running_continuation_budget
        return self.default_continuation_budget

    @staticmethod
    def _should_resume_existing_state(
        task_intent: TaskIntent,
        work_plan: WorkPlan,
        existing_state: StoredWorkState | None,
    ) -> bool:
        if existing_state is None:
            return False
        if not is_current_active_task_status(existing_state.status):
            return False
        if not existing_state.objective.strip():
            return False
        if task_intent.needs_clarification:
            return True
        return (
            existing_state.objective.strip().lower() == work_plan.objective.strip().lower()
            and existing_state.kind == work_plan.kind
        )

    @staticmethod
    def _should_preserve_existing_state(
        existing_state: StoredWorkState | None,
        task_context_decision: TaskContextDecision | None,
    ) -> bool:
        if existing_state is None:
            return False
        if not is_current_active_task_status(existing_state.status):
            return False
        if task_context_decision is None:
            return True
        if (
            task_context_decision.should_replace_active_task
            or task_context_decision.continuation_type in PRESERVE_STATE_RESET_CONTINUATION_TYPES
        ):
            return False
        return True

    def _resume_existing_state(
        self,
        existing_state: StoredWorkState,
        work_plan: WorkPlan,
    ) -> StoredWorkState:
        steps = tuple(existing_state.steps) or _numbered_steps(work_plan.steps)
        delegated_tasks = _delegated_tasks_for_state(existing_state)
        selected_task = selected_delegated_task(delegated_tasks)
        metadata = dict(existing_state.metadata or {})
        metadata.pop("workboard", None)
        existing_workboard = self.extract_workboard(existing_state)
        return StoredWorkState(
            session_id=existing_state.session_id,
            objective=existing_state.objective or work_plan.objective,
            kind=existing_state.kind or work_plan.kind,
            status=existing_state.status,
            steps=steps,
            constraints=tuple(existing_state.constraints or work_plan.constraints),
            done_criteria=tuple(existing_state.done_criteria or work_plan.done_criteria),
            long_running=bool(existing_state.long_running or work_plan.long_running),
            coding_task=bool(existing_state.coding_task or work_plan.coding_task),
            expects_code_change=bool(existing_state.expects_code_change or work_plan.expects_code_change),
            expects_verification=bool(existing_state.expects_verification or work_plan.expects_verification),
            current_step=existing_state.current_step,
            next_step=existing_state.next_step,
            completed_steps=tuple(existing_state.completed_steps),
            pending_steps=tuple(existing_state.pending_steps) or existing_workboard.pending_steps,
            blockers=tuple(existing_state.blockers) or existing_workboard.blockers,
            verification_targets=tuple(existing_state.verification_targets) or existing_workboard.verification_targets,
            resume_hint=existing_state.resume_hint or existing_workboard.resume_hint,
            last_progress_signals=tuple(existing_state.last_progress_signals) or existing_workboard.last_progress_signals,
            file_change_count=int(existing_state.file_change_count),
            touched_paths=tuple(existing_state.touched_paths),
            verification_attempted=bool(existing_state.verification_attempted),
            verification_passed=bool(existing_state.verification_passed),
            last_next_action=existing_state.last_next_action or _NEXT_ACTION_CONTINUE_WORK,
            delegated_tasks=delegated_tasks,
            active_delegate_task_id=selected_task.task_id if selected_task is not None else None,
            active_delegate_prompt_type=selected_task.prompt_type if selected_task is not None else None,
            metadata=metadata,
            created_at=existing_state.created_at or time.time(),
            updated_at=time.time(),
        )

    @staticmethod
    def _build_workboard(
        *,
        steps: tuple[str, ...],
        completed_steps: tuple[str, ...],
        status: str,
        current_step: str,
        next_step: str,
        done_criteria: tuple[str, ...],
        expects_verification: bool,
        progress: WorkProgressUpdate,
        completion_result: CompletionGateResult,
    ) -> WorkboardState:
        pending_steps = [step for step in steps if step not in completed_steps and step != WORK_STEP_NOT_SET]
        follow_up_step = _follow_up_pending_step(completion_result, progress.next_action)
        if follow_up_step and follow_up_step not in pending_steps:
            pending_steps.insert(0, follow_up_step)
        if current_step != WORK_STEP_NOT_SET and current_step not in completed_steps and current_step not in pending_steps:
            pending_steps.insert(0, current_step)
        blockers = _derive_blockers(completion_result)
        return WorkboardState(
            pending_steps=tuple(pending_steps),
            completed_steps=completed_steps,
            blockers=blockers,
            verification_targets=_derive_verification_targets(
                done_criteria,
                expects_verification=expects_verification,
            ),
            resume_hint=_build_resume_hint(
                status=status,
                current_step=current_step,
                next_step=next_step,
                blockers=blockers,
                next_action=progress.next_action,
                completion_result=completion_result,
            ),
            last_progress_signals=progress.progress_signals,
        )

    @staticmethod
    def _progress_signals(execution_result: ExecutionResult) -> tuple[str, ...]:
        signals: list[str] = []
        if execution_result.executed_tool_calls > 0:
            signals.append(_PROGRESS_SIGNAL_TOOL_CALLS)
        if execution_result.file_change_count > 0:
            signals.append(_PROGRESS_SIGNAL_FILE_CHANGES)
        if execution_result.verification_attempted:
            signals.append(_PROGRESS_SIGNAL_VERIFICATION_ATTEMPTED)
        if execution_result.verification_passed:
            signals.append(_PROGRESS_SIGNAL_VERIFICATION_PASSED)
        if execution_result.context_compactions > 0:
            signals.append(_PROGRESS_SIGNAL_CONTEXT_COMPACTION)
        if is_max_tool_iterations_stop_reason(execution_result.stop_reason):
            signals.append(MAX_TOOL_ITERATIONS_STOP_REASON)
        if execution_result.had_tool_error:
            signals.append(_PROGRESS_SIGNAL_TOOL_ERROR)
        return tuple(signals)

    @staticmethod
    def _status(completion_result: CompletionGateResult) -> str:
        if is_terminal_completion_status(completion_result.status):
            return completion_result.status
        if needs_verification_completion_status(completion_result.status):
            return _WORK_PROGRESS_VERIFYING_STATUS
        if needs_review_completion_status(completion_result.status):
            return _WORK_PROGRESS_REVIEWING_STATUS
        return _WORK_PROGRESS_WORKING_STATUS

    @staticmethod
    def _next_action(
        completion_result: CompletionGateResult,
        *,
        has_progress: bool,
        attempts: int,
        budget: int,
    ) -> str:
        if is_complete_completion_status(completion_result.status):
            return _NEXT_ACTION_FINALIZE
        if is_blocking_completion_status(completion_result.status):
            return completion_result.status
        if attempts >= budget:
            return _NEXT_ACTION_STOP_BUDGET_EXHAUSTED
        if attempts > 0 and not has_progress:
            return _NEXT_ACTION_STOP_NO_PROGRESS
        if needs_verification_completion_status(completion_result.status):
            return _NEXT_ACTION_CONTINUE_VERIFICATION
        if needs_review_completion_status(completion_result.status):
            return (
                _NEXT_ACTION_ADDRESS_REVIEW_FINDINGS
                if completion_result.review_attempted
                else _NEXT_ACTION_COLLECT_REVIEW_EVIDENCE
            )
        return _NEXT_ACTION_CONTINUE_WORK


def _numbered_steps(steps: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{index}. {step}" for index, step in enumerate(steps, start=1))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _legacy_workboard(state: StoredWorkState) -> dict[str, Any]:
    metadata = state.metadata if isinstance(state.metadata, dict) else {}
    payload = metadata.get("workboard")
    return payload if isinstance(payload, dict) else {}


def _apply_structured_follow_up_metadata(metadata: dict[str, Any], completion_result: CompletionGateResult) -> dict[str, Any]:
    next_metadata = dict(metadata or {})
    for key in (
        "follow_up_workflow",
        "follow_up_step_id",
        "follow_up_step_label",
        "follow_up_prompt_type",
        "verification_action",
        "verification_path",
        "verification_pytest_args",
        "active_task_detail",
    ):
        next_metadata.pop(key, None)
    if completion_result.follow_up_workflow:
        next_metadata["follow_up_workflow"] = completion_result.follow_up_workflow
    if completion_result.follow_up_step_id:
        next_metadata["follow_up_step_id"] = completion_result.follow_up_step_id
    if completion_result.follow_up_step_label:
        next_metadata["follow_up_step_label"] = completion_result.follow_up_step_label
    if completion_result.follow_up_prompt_type:
        next_metadata["follow_up_prompt_type"] = completion_result.follow_up_prompt_type
    if completion_result.verification_action:
        next_metadata["verification_action"] = completion_result.verification_action
    if completion_result.verification_path:
        next_metadata["verification_path"] = completion_result.verification_path
    if completion_result.verification_pytest_args:
        next_metadata["verification_pytest_args"] = list(completion_result.verification_pytest_args)
    if completion_result.active_task_detail:
        next_metadata["active_task_detail"] = completion_result.active_task_detail
    return next_metadata


def _derive_verification_targets(
    _done_criteria: tuple[str, ...],
    *,
    expects_verification: bool,
) -> tuple[str, ...]:
    if not expects_verification:
        return ()
    return (_DEFAULT_VERIFICATION_TARGET,)


def _profile_requires_code_change(harness_profile: HarnessProfile) -> bool:
    required_tool_groups = set(harness_profile.required_tool_groups)
    required_evidence = set(harness_profile.required_evidence)
    return (
        harness_profile.task_type in {WORKSPACE_CHANGE_TASK_TYPE, CODE_CHANGE_TASK_TYPE}
        or WORKSPACE_WRITE_TOOL_GROUP in required_tool_groups
        or FILE_CHANGE_REQUIREMENT_KIND in required_evidence
    )


def _profile_requires_verification(harness_profile: HarnessProfile) -> bool:
    required_tool_groups = set(harness_profile.required_tool_groups)
    required_evidence = set(harness_profile.required_evidence)
    return VERIFICATION_TOOL_GROUP in required_tool_groups or VERIFICATION_REQUIREMENT_KIND in required_evidence


def _intent_supports_default_work_plan(task_intent: TaskIntent) -> bool:
    return intent_supports_default_work_plan(task_intent)


def _derive_blockers(completion_result: CompletionGateResult) -> tuple[str, ...]:
    if is_blocking_completion_status(completion_result.status):
        detail = completion_result.active_task_detail or completion_result.reason
        if detail:
            return (detail,)
    return ()


def _follow_up_pending_step(completion_result: CompletionGateResult, next_action: str) -> str:
    detail = str(completion_result.active_task_detail or "").strip()
    if not detail:
        return ""
    if is_incomplete_completion_status(completion_result.status):
        return detail
    if is_review_follow_up_next_action(next_action):
        return detail
    return ""


def _build_resume_hint(
    *,
    status: str,
    current_step: str,
    next_step: str,
    blockers: tuple[str, ...],
    next_action: str,
    completion_result: CompletionGateResult | None = None,
) -> str:
    if status == _WORK_STATE_DONE_STATUS:
        return TASK_DONE_RESUME_HINT
    if blockers:
        return f"Resolve blocker first: {blockers[0]}"
    workflow = str(getattr(completion_result, "follow_up_workflow", "") or "").strip()
    step_label = str(getattr(completion_result, "follow_up_step_label", "") or getattr(completion_result, "follow_up_step_id", "") or "").strip()
    prompt_type = str(getattr(completion_result, "follow_up_prompt_type", "") or "").strip()
    verification_action = str(getattr(completion_result, "verification_action", "") or "").strip()
    verification_path = str(getattr(completion_result, "verification_path", "") or "").strip()
    return build_policy_resume_hint(
        status=status,
        current_step=current_step,
        next_step=next_step,
        blockers=blockers,
        next_action=next_action,
        workflow=workflow,
        step_label=step_label,
        prompt_type=prompt_type,
        verification_action=verification_action,
        verification_path=verification_path,
        done_status=_WORK_STATE_DONE_STATUS,
        unset_step=WORK_STEP_NOT_SET,
    )


def _map_state_status(completion_result: CompletionGateResult, progress: WorkProgressUpdate) -> str:
    if is_complete_completion_status(completion_result.status):
        return _WORK_STATE_DONE_STATUS
    if is_blocking_completion_status(completion_result.status):
        return completion_result.status
    return _WORK_STATE_ACTIVE_STATUS


def _completion_gap_should_get_one_retry(completion_result: CompletionGateResult) -> bool:
    if not is_incomplete_completion_status(completion_result.status):
        return False
    if completion_result.progress_only_response:
        return True
    return bool(completion_result.missing_evidence)


def _completed_steps(
    steps: tuple[str, ...],
    existing: tuple[str, ...],
    progress: WorkProgressUpdate,
    *,
    expects_code_change: bool,
) -> tuple[str, ...]:
    completed = list(existing)
    if not completed and steps:
        completed.append(steps[0])
    if expects_code_change and progress.file_change_count > 0 and len(steps) > 1 and steps[1] not in completed:
        completed.append(steps[1])
    if is_complete_completion_status(progress.completion_status):
        for step in steps:
            if step not in completed:
                completed.append(step)
    return tuple(completed)


def _state_steps(
    steps: tuple[str, ...],
    progress: WorkProgressUpdate,
    *,
    expects_code_change: bool,
    expects_verification: bool,
) -> tuple[str, str]:
    if is_complete_completion_status(progress.completion_status):
        return WORK_STEP_NOT_SET, WORK_STEP_NOT_SET
    if is_blocking_completion_status(progress.completion_status):
        current = steps[-1] if steps else WORK_STEP_NOT_SET
        return current, WORK_STEP_NOT_SET
    if is_verification_next_action(progress.next_action):
        return _verification_step(steps, expects_code_change=expects_code_change), WORK_STEP_NOT_SET
    if is_review_phase_next_action(progress.next_action):
        return (steps[-1] if steps else WORK_STEP_NOT_SET), WORK_STEP_NOT_SET
    if expects_code_change and progress.file_change_count <= 0 and len(steps) >= 2:
        next_step = steps[2] if len(steps) > 2 else WORK_STEP_NOT_SET
        return steps[1], next_step
    if expects_verification and steps:
        return _verification_step(steps, expects_code_change=expects_code_change), WORK_STEP_NOT_SET
    if is_continue_work_next_action(progress.next_action) and steps:
        current = steps[-1] if progress.file_change_count > 0 else (steps[1] if len(steps) > 1 else steps[0])
        next_step = WORK_STEP_NOT_SET
        if progress.file_change_count <= 0 and len(steps) > 2:
            next_step = steps[2]
        return current, next_step
    return WORK_STEP_NOT_SET, WORK_STEP_NOT_SET


def _verification_step(steps: tuple[str, ...], *, expects_code_change: bool) -> str:
    if not steps:
        return WORK_STEP_NOT_SET
    if expects_code_change and len(steps) >= 3:
        return steps[2]
    if len(steps) >= 2:
        return steps[1]
    return steps[-1] if steps else WORK_STEP_NOT_SET

class ActiveTaskCommandService:
    """Handles direct commands and immediate updates for ACTIVE_TASK state."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        app_home_getter: Callable[[], Path | None],
        workspace_root_getter: Callable[[], Path | None],
        messages: TaskMessagesConfig | None = None,
    ):
        self.storage = storage
        self._app_home_getter = app_home_getter
        self._workspace_root_getter = workspace_root_getter
        self.messages = messages or TaskMessagesConfig()

    def get_store(self, session_id: str):
        app_home = self._app_home_getter()
        if app_home is None:
            return None
        return create_active_task_store(
            app_home,
            session_id,
            workspace_root=self._workspace_root_getter(),
        )

    def clear(self, session_id: str) -> None:
        """Reset ACTIVE_TASK.md for one session."""
        store = self.get_store(session_id)
        if store is not None:
            store.clear(session_id)

    async def _mark_processed(self, session_id: str, store: Any) -> None:
        message_count = await get_storage_message_count(self.storage, session_id)
        store.set_processed_index(session_id, message_count)

    async def apply_completion_gate_result(
        self,
        session_id: str,
        result: CompletionGateResult,
    ) -> None:
        """Apply conservative task-state updates from completion-gate verdicts."""
        if not result.should_update_active_task or result.active_task_status is None:
            return
        store = self.get_store(session_id)
        if store is None:
            return
        if not is_current_active_task_status(store.read_status()):
            return

        status = result.active_task_status
        detail = result.active_task_detail or result.reason
        if status == WAITING_USER_ACTIVE_TASK_STATUS:
            store.update_fields(
                status=WAITING_USER_ACTIVE_TASK_STATUS,
                open_questions=[detail or WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION],
                force=True,
            )
        elif status == BLOCKED_ACTIVE_TASK_STATUS:
            store.update_fields(
                status=BLOCKED_ACTIVE_TASK_STATUS,
                open_questions=[detail or BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION],
                force=True,
            )
        elif status == DONE_ACTIVE_TASK_STATUS:
            store.update_fields(status=DONE_ACTIVE_TASK_STATUS, open_questions=clear_open_questions(), force=True)
        else:
            return

        await self._mark_processed(session_id, store)
        store.append_event(
            "completion_gate",
            "immediate",
            details={"status": result.status, "reason": result.reason},
        )

    async def apply_work_progress(
        self,
        session_id: str,
        progress: WorkProgressUpdate,
        state: StoredWorkState | None = None,
    ) -> None:
        """Keep ACTIVE_TASK aligned with the final structured work progress state."""
        store = self.get_store(session_id)
        if store is None:
            return
        if not is_current_active_task_status(store.read_status()):
            return

        current_step = state.current_step if state is not None else None
        next_step = state.next_step if state is not None else None
        if not current_step and not next_step:
            if is_verification_work_progress(progress):
                current_step = self.messages.progress_verify_current_step
                next_step = WORK_STEP_NOT_SET
            elif is_continue_work_progress(progress):
                current_step = self.messages.progress_continue_current_step
                next_step = (
                    self.messages.progress_verify_current_step if progress.verification_required else WORK_STEP_NOT_SET
                )
            else:
                return
        if current_step is None or next_step is None:
            return

        workboard = WorkProgressService.extract_workboard(state)
        open_questions: list[str] | None = None
        if workboard.blockers:
            open_questions = list(workboard.blockers)
        elif state is not None and clears_active_task_open_questions(state.status):
            open_questions = clear_open_questions()
        elif is_blocking_completion_status(progress.status):
            open_questions = [progress.completion_reason]

        store.update_fields(
            status=state.status
            if state is not None and is_current_or_done_active_task_status(state.status)
            else ACTIVE_ACTIVE_TASK_STATUS,
            current_step=current_step,
            next_step=next_step,
            open_questions=open_questions,
            force=True,
        )
        store.append_event(
            WORK_PROGRESS_ACTIVE_TASK_EVENT_TYPE,
            "immediate",
            details={
                "status": progress.status,
                "next_action": progress.next_action,
                "file_change_count": progress.file_change_count,
                "verification_required": progress.verification_required,
                "verification_passed": progress.verification_passed,
            },
        )

    async def maybe_seed(
        self,
        session_id: str,
        current_message: str,
        *,
        enabled: bool,
        task_intent: TaskIntent | None = None,
        task_context_decision: TaskContextDecision | None = None,
        task_objective_decision: TaskObjectiveDecision | None = None,
    ) -> None:
        """Create a minimal ACTIVE_TASK.md before the first heavy turn when appropriate."""
        if not enabled:
            return
        store = self.get_store(session_id)
        if store is None:
            return

        current_status = store.read_status()
        replacing = False
        has_current_task = is_current_active_task_status(current_status)
        if has_current_task:
            current_task = store.read_managed_block()
            if _decision_needs_boundary_confirmation(task_context_decision):
                question = _boundary_confirmation_question(current_task, current_message)
                store.update_fields(status=WAITING_USER_ACTIVE_TASK_STATUS, open_questions=[question], force=True)
                await self._mark_processed(session_id, store)
                store.append_event(
                    TASK_BOUNDARY_CONFIRMATION_EVENT,
                    "immediate",
                    details={
                        "message": re.sub(r"\s+", " ", current_message).strip()[:120],
                        "pending_request": _compact_for_prompt(current_message),
                        "confidence": task_context_decision.confidence if task_context_decision else 0.0,
                    },
                )
                return
            if _decision_continues_current_task(task_context_decision):
                if current_status == WAITING_USER_ACTIVE_TASK_STATUS and store.read_pending_boundary_request():
                    store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, open_questions=clear_open_questions(), force=True)
                    await self._mark_processed(session_id, store)
                    store.append_event(
                        TASK_BOUNDARY_CONFIRMATION_RESOLVED_EVENT,
                        "immediate",
                        details={"action": "continue", "message": _compact_for_prompt(current_message)},
                    )
                return
            llm_replace = _decision_replaces_current_task(task_context_decision)
            if task_context_decision and task_context_decision.should_inherit_active_task and not llm_replace:
                return
            if not llm_replace:
                return
            replacing = True

        initial_task = None
        if task_intent is not None:
            if _decision_controls_task_seed(task_context_decision):
                should_seed = (
                    bool(task_context_decision and task_context_decision.should_seed_active_task)
                    or bool(task_context_decision and task_context_decision.should_replace_active_task)
                    or bool(task_objective_decision and task_objective_decision.should_use_resolved_objective)
                )
            else:
                should_seed = bool(task_objective_decision and task_objective_decision.should_use_resolved_objective)
            inheriting_current_task = bool(has_current_task and task_context_decision and task_context_decision.should_inherit_active_task)
            if should_seed and not inheriting_current_task:
                goal = task_intent.objective
                assumptions: list[str] | None = None
                if task_objective_decision and task_objective_decision.should_use_resolved_objective:
                    goal = task_objective_decision.resolved_objective
                    assumptions = _objective_assumptions(task_objective_decision)
                initial_task = build_task_block_from_intent_fields(
                    goal=goal,
                    definition_of_done=task_intent.done_criteria,
                    constraints=task_intent.constraints,
                    assumptions=assumptions,
                )
        else:
            return
        if not initial_task:
            return

        store.write_managed_block(initial_task)
        message_count = await get_storage_message_count(self.storage, session_id)
        store.set_processed_index(session_id, max(0, message_count - 1))
        compact_message = re.sub(r"\s+", " ", current_message).strip()
        if len(compact_message) > 120:
            compact_message = compact_message[:117].rstrip() + "..."
        event_details = {"replace": replacing, "message": compact_message}
        if task_intent is not None:
            event_details.update(
                {
                    "intent_kind": task_intent.kind,
                    "intent_long_running": task_intent.long_running,
                }
            )
        if task_objective_decision and task_objective_decision.should_use_resolved_objective:
            event_details.update(
                {
                    "original_message": task_objective_decision.original_message,
                    "resolved_objective": task_objective_decision.resolved_objective,
                    "objective_method": task_objective_decision.method,
                    "objective_confidence": task_objective_decision.confidence,
                }
            )
        store.append_event("seed", "immediate", details=event_details)
        logger.info("[{}] {} | replace={}", session_id, ACTIVE_TASK_SEEDED_EVENT, replacing)

    async def show(self, session_id: str) -> str | None:
        """Return the current ACTIVE_TASK block for user display, if any."""
        store = self.get_store(session_id)
        if store is None:
            return None
        return store.render_for_user()

    async def show_full(self, session_id: str) -> str | None:
        """Return the full ACTIVE_TASK block for user display, if any."""
        store = self.get_store(session_id)
        if store is None:
            return None
        return store.render_full_for_user()

    async def show_history(self, session_id: str, *, limit: int = 10) -> str | None:
        """Return recent ACTIVE_TASK events for user display, if any."""
        store = self.get_store(session_id)
        if store is None:
            return None
        return store.render_history(limit=limit)

    async def set_from_text(self, session_id: str, task_text: str) -> str | None:
        """Create or replace the current ACTIVE_TASK from explicit user text."""
        store = self.get_store(session_id)
        if store is None:
            return None
        task_block = build_task_block_from_text(task_text, force=True)
        if not task_block:
            return None
        store.write_managed_block(task_block)
        await self._mark_processed(session_id, store)
        store.append_event("set", "user", details={"task": task_text})
        return store.render_full_for_user()

    async def activate(self, session_id: str) -> str | None:
        """Mark the current ACTIVE_TASK as active again."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, open_questions=clear_open_questions(), force=True)
        await self._mark_processed(session_id, store)
        store.append_event("activate", "user")
        return f"# Active Task\n\n{rendered}"

    async def reopen(self, session_id: str) -> str | None:
        """Reopen a terminal ACTIVE_TASK and resume it as active."""
        store = self.get_store(session_id)
        if store is None:
            return None
        if not is_terminal_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("reopen", "user")
        return f"# Active Task\n\n{rendered}"

    async def block(self, session_id: str, reason: str) -> str | None:
        """Mark the current ACTIVE_TASK as blocked with one explicit reason."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=BLOCKED_ACTIVE_TASK_STATUS, open_questions=[reason], force=True)
        await self._mark_processed(session_id, store)
        store.append_event("block", "user", details={"reason": reason})
        return f"# Active Task\n\n{rendered}"

    async def wait_on(self, session_id: str, question: str) -> str | None:
        """Mark the current ACTIVE_TASK as waiting for user input."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=WAITING_USER_ACTIVE_TASK_STATUS, open_questions=[question], force=True)
        await self._mark_processed(session_id, store)
        store.append_event("wait", "user", details={"question": question})
        return f"# Active Task\n\n{rendered}"

    async def set_current_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the current step for the active task."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, current_step=step_text, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("set_current_step", "user", details={"current_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def set_next_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the planned next step for the active task."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(next_step=step_text, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("set_next_step", "user", details={"next_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def advance(self, session_id: str) -> str | None:
        """Promote the next step into the current step and mark the previous step complete."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        next_step = _extract_task_field(current_block, "Next step")
        if next_step == WORK_STEP_NOT_SET:
            return None
        rendered = store.update_fields(
            status=ACTIVE_ACTIVE_TASK_STATUS,
            current_step=next_step,
            next_step=WORK_STEP_NOT_SET,
            append_completed_step=current_step,
            force=True,
        )
        await self._mark_processed(session_id, store)
        store.append_event(
            "advance",
            "user",
            details={"completed_step": current_step, "new_current_step": next_step},
        )
        return f"# Active Task\n\n{rendered}"

    async def complete_step(self, session_id: str, next_step_override: str | None = None) -> str | None:
        """Complete the current step and either advance or finish the task."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        rendered = store.complete_current_step(next_step_override=next_step_override)
        if rendered is None:
            return None
        await self._mark_processed(session_id, store)
        store.append_event(
            "complete_step",
            "user",
            details={
                "completed_step": current_step,
                "next_step_override": next_step_override or "",
            },
        )
        return f"# Active Task\n\n{rendered}"

    async def mark_status(self, session_id: str, status: str) -> str | None:
        """Set the current ACTIVE_TASK status when one exists."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        open_questions = clear_open_questions() if clears_active_task_open_questions(status) else None
        store.update_fields(status=status, open_questions=open_questions, force=True)
        if is_terminal_active_task_status(status):
            await self._mark_processed(session_id, store)
        store.append_event(status, "user")
        return store.render_full_for_user()

    async def reset(self, session_id: str) -> None:
        """Clear the current ACTIVE_TASK state for one session."""
        store = self.get_store(session_id)
        if store is None:
            return
        self.clear(session_id)
        store.append_event("reset", "user")


def _decision_continues_current_task(decision: TaskContextDecision | None) -> bool:
    if decision is None or decision.should_replace_active_task:
        return False
    return bool(
        decision.should_inherit_active_task
        or is_current_task_continuation_type(decision.continuation_type)
        or decision.is_follow_up
    )


def _decision_replaces_current_task(decision: TaskContextDecision | None) -> bool:
    return bool(
        decision
        and decision.should_replace_active_task
        and is_current_task_replacement_type(decision.continuation_type)
    )


def _decision_needs_boundary_confirmation(decision: TaskContextDecision | None) -> bool:
    return bool(decision and is_ambiguous_boundary_continuation_type(decision.continuation_type))


def _decision_controls_task_seed(decision: TaskContextDecision | None) -> bool:
    return bool(decision and decision.method == "llm")


def _boundary_confirmation_question(current_task: str, current_message: str) -> str:
    current_goal = _compact_for_prompt(_extract_task_field(current_task, "Goal"))
    new_request = _compact_for_prompt(current_message) or "the new request"
    if current_goal and current_goal.lower() != WORK_STEP_NOT_SET:
        return (
            f"Reply `{BOUNDARY_SWITCH_REPLY_COMMAND}` to replace the active task ({current_goal}) "
            f"with the new request ({new_request}), or `{BOUNDARY_CONTINUE_REPLY_COMMAND}` to keep the active task."
        )
    return (
        f"Reply `{BOUNDARY_SWITCH_REPLY_COMMAND}` to replace it with the new request ({new_request}), "
        f"or `{BOUNDARY_CONTINUE_REPLY_COMMAND}` to keep the active task."
    )


def _compact_for_prompt(value: str, max_chars: int = 120) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _objective_assumptions(decision: TaskObjectiveDecision) -> list[str]:
    return [
        f"Original user message: {decision.original_message}",
        f"Objective inferred from conversation context ({decision.method}, confidence={decision.confidence:.2f}).",
    ]
