"""Shared execution loop for agent and subagent message runs."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from ..config import AgentConfig, DEFAULT_CONTEXT_OVERFLOW_ERROR_MARKERS, DocumentLlmConfig, LogConfig, ToolsConfig
from ..llms import (
    CHAT_CONTENT_TYPE_IMAGE_URL,
    CHAT_CONTENT_TYPE_TEXT,
    CHAT_ROLE_ASSISTANT,
    CHAT_ROLE_SYSTEM,
    CHAT_ROLE_TOOL,
    CHAT_ROLE_USER,
    ChatMessage,
    LLMProvider,
)
from ..llms.retry import retry_delay_from_error
from ..runs.events import (
    HARNESS_POLICY_SELECTED_EVENT,
    HARNESS_POLICY_MERGE_RESOLVED_EVENT,
    HARNESS_PROFILE_SELECTED_EVENT,
    HISTORY_LOADED_EVENT,
    MCP_TOOLS_SYNCED_EVENT,
    PROMPT_BUILT_EVENT,
    PROMPT_TOKENS_ESTIMATED_EVENT,
    PLANNING_MODE_SELECTED_EVENT,
    RETRIEVAL_PROACTIVE_CHECKED_EVENT,
    TASK_CONTRACT_CREATED_EVENT,
    TASK_CONTRACT_PLANNED_EVENT,
    TASK_CONTRACT_PLANNING_STARTED_EVENT,
    TASK_CONTRACT_VALIDATED_EVENT,
    TASK_CONTRACT_VALIDATION_FAILED_EVENT,
    TASK_CONTEXT_RESOLVED_EVENT,
    TASK_OBJECTIVE_RESOLVED_EVENT,
)
from ..context.builder import ContextBuilder
from ..documents.active_task import has_current_active_task
from ..storage.base import StoredDelegatedTask
from ..tool_names import (
    ANALYZE_IMAGE_TOOL_NAME,
    ANALYZE_VIDEO_TOOL_NAME,
    CONFIGURE_SKILL_TOOL_NAME,
    CONFIGURE_SUBAGENT_TOOL_NAME,
    DELEGATE_TOOL_NAME,
    EXEC_TOOL_NAME,
    OCR_IMAGE_TOOL_NAME,
    TRANSCRIBE_AUDIO_TOOL_NAME,
)
from ..tools import ToolRegistry
from ..tools.evidence import (
    VERIFICATION_RESULT_ARTIFACT_KIND,
    VERIFICATION_TOOL_NAME,
    WEB_SOURCE_ARTIFACT_KIND,
    WEB_SOURCE_ARTIFACT_TOOLS,
    ToolEvidence,
    is_verification_tool_name,
    is_web_research_source_artifact_tool,
    is_web_source_artifact_kind,
    is_web_source_evidence_tool,
)
from ..tools.result_status import classify_tool_result_status, tool_error_result
from ..tools.verify import classify_verification_result
from ..utils import (
    count_messages_tokens,
    count_text_tokens,
    sanitize_assistant_visible_text,
    strip_assistant_internal_scaffolding,
)
from ..utils.log import logger
from ..utils.log_redaction import redact_log_preview
from ..runs.trace import RunCancelledError, mcp_tool_names as list_mcp_tool_names
from ..harness import (
    HISTORY_RETRIEVAL_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP,
    HarnessPolicy,
    HarnessProfile,
    is_chat_profile_name,
)
from .task_contract import (
    PLANNER_VALIDATED_STATUS,
    TaskContextDecision,
    TaskContract,
    TaskIntent,
    TaskObjectiveDecision,
    is_itemized_output_criterion,
    is_media_artifact_criterion,
    is_operation_report_criterion,
    is_source_artifact_criterion,
    is_source_detail_criterion,
    is_source_reference_criterion,
    is_substantive_final_answer_criterion,
    is_verification_or_gap_criterion,
    is_workspace_location_criterion,
    resolve_planning_mode,
    task_planner_status,
)
from .subagent import (
    DEFAULT_MAX_PARALLEL_SUBAGENTS,
    DEFAULT_SUBAGENT_MAX_TOOL_ITERATIONS,
    MAX_PARALLEL_SUBAGENTS,
    READONLY_SUBAGENT_RESULT_CONTRACT,
    STRUCTURED_SUBAGENT_CONTRACT_FIELD,
    STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD,
    STRUCTURED_SUBAGENT_INCONCLUSIVE_STATUS,
    STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD,
    STRUCTURED_SUBAGENT_ITEMS_FIELD,
    STRUCTURED_SUBAGENT_NEEDS_INPUT_STATUS,
    STRUCTURED_SUBAGENT_OK_STATUS,
    STRUCTURED_SUBAGENT_PROMPT_TYPE_FIELD,
    STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD,
    STRUCTURED_SUBAGENT_QUESTIONS_FIELD,
    STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD,
    STRUCTURED_SUBAGENT_RESIDUAL_RISKS_FIELD,
    STRUCTURED_SUBAGENT_SCHEMA_VERSION,
    STRUCTURED_SUBAGENT_SCHEMA_VERSION_FIELD,
    STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD,
    STRUCTURED_SUBAGENT_SECTION_TYPE_FIELD,
    STRUCTURED_SUBAGENT_SECTIONS_FIELD,
    STRUCTURED_SUBAGENT_SOURCE_COUNT_FIELD,
    STRUCTURED_SUBAGENT_SOURCES_FIELD,
    STRUCTURED_SUBAGENT_STATUS_FIELD,
    STRUCTURED_SUBAGENT_SUMMARY_FIELD,
    STRUCTURED_SUBAGENT_TRUNCATED_FIELD,
    SUBAGENT_PROMPT_TYPE_LABEL,
    SUBAGENT_TASK_ID_LABEL,
    SUBAGENT_TASK_ID_PATTERN,
    PreparedSubagentTask,
    SubagentMessageBuilder,
    SubagentTaskOutcome,
    _structured_subagent_result_fields,
    build_child_subagent_session_id,
    build_structured_subagent_contract_instructions,
    extract_subagent_prompt_type,
    first_structured_review_finding,
    format_review_finding,
    is_clean_structured_subagent_status,
    new_subagent_task_id,
    parse_structured_subagent_output,
    parse_subagent_result_line,
    subagent_result_line,
    validate_subagent_task_id,
)
from .subagent_run import (
    SubagentRunService,
    WritePathPermissionPolicy,
    _subagent_error_result,
    _subagent_preparation_error_detail,
    _subagent_validation_error,
    build_subagent_tool_registry,
)
from .workflow import (
    BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID,
    IMPLEMENT_THEN_REVIEW_WORKFLOW_ID,
    RESEARCH_THEN_OUTLINE_WORKFLOW_ID,
    REVIEW_WORKFLOW_IDS,
    WORKFLOW_CANCELLED_STATUS,
    WORKFLOW_COMPLETED_STATUS,
    WORKFLOW_ERROR_FIELD,
    WORKFLOW_ERROR_STATUS,
    WORKFLOW_FAILED_STATUS,
    WORKFLOW_FAILURE_STATUSES,
    WORKFLOW_ID_FIELD,
    WORKFLOW_LAST_COMPLETED_PROMPT_TYPE_FIELD,
    WORKFLOW_LAST_COMPLETED_STEP_ID_FIELD,
    WORKFLOW_LAST_COMPLETED_STEP_LABEL_FIELD,
    WORKFLOW_NEXT_STEP_ID_FIELD,
    WORKFLOW_NEXT_STEP_LABEL_FIELD,
    WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD,
    WORKFLOW_REVIEW_ATTEMPTED_FIELD,
    WORKFLOW_REVIEW_FINDING_COUNT_FIELD,
    WORKFLOW_REVIEW_FIRST_FINDING_FIELD,
    WORKFLOW_REVIEW_PASSED_FIELD,
    WORKFLOW_REVIEW_SUMMARY_FIELD,
    WORKFLOW_RUNNING_STATUS,
    WORKFLOW_SPECS,
    WORKFLOW_STATUS_FIELD,
    WORKFLOW_SUMMARY_FIELD,
    WORKFLOW_UNSUCCESSFUL_STATUSES,
    WORKFLOW_VERIFICATION_ATTEMPTED_FIELD,
    WORKFLOW_VERIFICATION_PASSED_FIELD,
    SubagentWorkflowService,
    WorkflowSpec,
    WorkflowStepSpec,
    is_workflow_cancelled_status,
    is_workflow_completed_status,
    is_workflow_failed_status,
    is_workflow_running_status,
    is_workflow_unsuccessful_status,
)
from ..tools.loop_guardrail import (
    ToolLoopGuardrail,
    append_toolguard_guidance,
    build_toolguard_synthetic_result,
)


LLM_STEP_COMPLETED_STATUS = "completed"
LLM_STEP_ERROR_STATUS = "error"
COMPACTED_CONVERSATION_STATE_HEADING = "# Compacted Conversation State"
COMPACTED_TASK_STATE_HEADING = "# Compacted Task State"
COMPACTION_HANDOFF_HEADINGS = (
    COMPACTED_CONVERSATION_STATE_HEADING,
    COMPACTED_TASK_STATE_HEADING,
)
LLM_COMPACTION_TOO_LARGE_REASON = "llm_too_large"
LLM_COMPACTION_CONFIG_MISSING_REASON = "llm_config_missing"
LLM_COMPACTION_NO_BODY_REASON = "no_body"
LLM_COMPACTION_NO_PROMPT_REASON = "no_prompt"
LLM_COMPACTION_ERROR_REASON = "llm_error"
LLM_COMPACTION_EMPTY_REASON = "llm_empty"
MAX_TOOL_ITERATIONS_STOP_REASON = "max_tool_iterations"
TASK_ARTIFACTS_NOT_PRODUCED_REASON = "required task artifacts were not produced"
_TOOL_ARTIFACT_KINDS: dict[str, str] = {
    OCR_IMAGE_TOOL_NAME: "image_text",
    ANALYZE_IMAGE_TOOL_NAME: "image_analysis",
    TRANSCRIBE_AUDIO_TOOL_NAME: "audio_transcript",
    ANALYZE_VIDEO_TOOL_NAME: "video_analysis",
    **{tool_name: WEB_SOURCE_ARTIFACT_KIND for tool_name in WEB_SOURCE_ARTIFACT_TOOLS},
    VERIFICATION_TOOL_NAME: VERIFICATION_RESULT_ARTIFACT_KIND,
    EXEC_TOOL_NAME: "command_result",
}


@dataclass(frozen=True)
class TaskArtifact:
    """One structured output artifact available for completion quality checks."""

    kind: str
    source_tool: str
    resource_ids: tuple[str, ...] = ()
    content_preview: str = ""
    ok: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_tool": self.source_tool,
            "resource_ids": list(self.resource_ids),
            "content_preview": self.content_preview,
            "ok": self.ok,
            "metadata": dict(self.metadata),
        }


def build_task_artifact(evidence: ToolEvidence) -> TaskArtifact | None:
    """Create a typed artifact when a tool produced reusable task output."""
    if not evidence.ok:
        return None
    kind = _TOOL_ARTIFACT_KINDS.get(evidence.name)
    if kind is None:
        return None
    if is_web_source_artifact_kind(kind) and not _has_traceable_sources(evidence.metadata):
        return None
    metadata = {"tool_args": dict(evidence.args)}
    metadata.update(dict(evidence.metadata))
    return TaskArtifact(
        kind=kind,
        source_tool=evidence.name,
        resource_ids=tuple(evidence.resource_ids),
        content_preview=evidence.result_preview,
        ok=evidence.ok,
        metadata=metadata,
    )


def _has_traceable_sources(metadata: dict[str, Any]) -> bool:
    sources = metadata.get("sources") if isinstance(metadata, dict) else None
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or "").strip()
        snippet = str(source.get("snippet") or "").strip()
        if url and (title or snippet):
            return True
    return False


def is_max_tool_iterations_stop_reason(stop_reason: str | None) -> bool:
    return str(stop_reason or "").strip() == MAX_TOOL_ITERATIONS_STOP_REASON


def contains_compaction_handoff(content: str | None) -> bool:
    text = str(content or "")
    return any(heading in text for heading in COMPACTION_HANDOFF_HEADINGS)


def format_repeated_invalid_tool_call_content(template: str | None, result: str) -> str:
    cleaned_template = str(template or "").strip()
    cleaned_result = str(result or "").strip()
    if not cleaned_template:
        return cleaned_result
    try:
        return cleaned_template.format(result=result)
    except (KeyError, IndexError, ValueError):
        return f"{cleaned_template}\n\n{result}"


@dataclass
class ContextCompactionEvent:
    """Structured telemetry for one context compaction decision."""

    trigger: str
    strategy: str
    outcome: str
    iteration: int
    messages_before: int
    messages_after: int
    estimated_tokens: int | None = None
    compacted_tokens: int | None = None
    threshold_tokens: int | None = None
    budget_tokens: int | None = None
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    message_tokens: int | None = None
    tool_schema_tokens: int | None = None
    fallback_reason: str | None = None
    error: str | None = None


@dataclass
class LlmStepEvent:
    """Structured telemetry for one LLM request attempt."""

    iteration: int
    attempt: int
    status: str
    provider: str | None
    model: str | None
    duration_ms: int
    estimated_input_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    tools_enabled: bool = False
    tool_count: int = 0
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    finish_reason: str | None = None
    tool_calls: int = 0
    error: str | None = None
    retryable: bool = False
    retry_after_ms: int | None = None
    next_retry_at: float | None = None


@dataclass
class ExecutionResult:
    """Outcome of one execute_messages run (visible reply plus tool-use telemetry)."""

    content: str
    executed_tool_calls: int = 0
    file_change_count: int = 0
    touched_paths: tuple[str, ...] = ()
    delegated_tasks: tuple[StoredDelegatedTask, ...] = ()
    workflow_outcomes: tuple[dict[str, Any], ...] = ()
    active_delegate_task_id: str | None = None
    active_delegate_prompt_type: str | None = None
    used_configure_skill: bool = False
    had_tool_error: bool = False
    verification_attempted: bool = False
    verification_passed: bool = False
    stop_reason: str | None = None
    stop_metadata: dict[str, Any] = field(default_factory=dict)
    compaction_handoff: str | None = None
    context_compactions: int = 0
    context_compaction_events: list[ContextCompactionEvent] = field(default_factory=list)
    llm_step_events: list[LlmStepEvent] = field(default_factory=list)
    reasoning_details: list[dict[str, Any]] | None = None
    assistant_internal_only_response: bool = False
    task_contract: TaskContract | None = None
    harness_policy: dict[str, Any] | None = None
    tool_evidence: tuple[ToolEvidence, ...] = ()
    task_artifacts: tuple[TaskArtifact, ...] = ()


@dataclass
class _LlmCompactionAttempt:
    """Internal result for one optional LLM compaction attempt."""

    messages: list[ChatMessage] | None = None
    fallback_reason: str | None = None
    error: str | None = None


@dataclass
class _ProactiveCompactionResult:
    """Internal result for one proactive compaction build."""

    messages: list[ChatMessage]
    estimated_tokens: int
    compacted_tokens: int
    threshold_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    strategy: str
    fallback_reason: str | None = None
    error: str | None = None


class ToolResultPersistence:
    """Persist tool execution results to storage."""

    def __init__(
        self,
        *,
        save_message: Callable[[str, str, str, str | None, dict[str, Any] | None], Awaitable[None]],
    ):
        self.save_message = save_message

    async def persist(
        self,
        *,
        session_id: str | None,
        tool_name: str,
        tool_args: dict[str, Any],
        result: str,
    ) -> None:
        """Persist a single tool result when a target session is available."""
        if session_id is None:
            return

        await self.save_message(
            session_id,
            "tool",
            result,
            tool_name,
            {"tool_args": dict(tool_args or {})},
        )


class ExecutionEngine:
    """Run the LLM and tool-calling loop for prepared chat messages."""

    _MAIN_SYSTEM_REFRESH_TOOLS = frozenset({CONFIGURE_SKILL_TOOL_NAME, CONFIGURE_SUBAGENT_TOOL_NAME})
    _MAIN_SYSTEM_REFRESH_ACTIONS = frozenset({"add", "upsert", "remove"})

    REPEATED_TOOL_ERROR_LIMIT = 2
    TOOL_RESULT_MAX_CHARS = 1200
    EXEC_RESULT_MAX_CHARS = 1200
    EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Previous attempt produced no visible user-facing text. "
        "Please answer again with a direct, displayable reply for the user."
    )
    SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Previous attempt only contained hidden or non-displayable content. "
        "Do not output <think>, <system-reminder>, or hidden reasoning. If tools are needed, call them. Otherwise answer now in plain visible text for the user."
    )
    CONTEXT_COMPACTION_RETRY_LIMIT = 1
    PROACTIVE_CONTEXT_COMPACTION_LIMIT = 1
    RESPONSE_DELTA_CHARS = 240
    COMPACTED_MESSAGE_MAX_CHARS = 900
    COMPACTED_LATEST_USER_MAX_CHARS = 1600
    COMPACTED_TRANSCRIPT_MAX_CHARS = 10_000
    COMPACTION_HANDOFF_MAX_CHARS = 6_000
    COMPACTED_TAIL_MESSAGE_LIMIT = 2
    COMPACTED_TAIL_MESSAGE_MAX_CHARS = 1200
    LLM_COMPACTION_TRANSCRIPT_MAX_CHARS = 30_000
    LLM_COMPACTION_MESSAGE_MAX_CHARS = 1800
    CONTINUATION_AFTER_COMPACTION_MESSAGE = (
        "Continue from the compacted conversation handoff above. "
        "Treat it as reference state from a previous context window, not as a fresh user request. "
        "Do not ask the user to repeat information that is already preserved there. "
        "Prefer the preserved recent tail and current active task state when deciding the next step. "
        "Do not treat this handoff as completion evidence; verification, review, and evidence gates still apply. "
        "If more tool work is needed, continue using tools; otherwise provide the final answer."
    )
    CONTEXT_OVERFLOW_STATUS_MESSAGE = "上下文已接近上限，正在壓縮目前任務並繼續…"
    PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE = "上下文接近上限，正在壓縮目前任務並繼續…"
    PROVIDER_RETRY_LIMIT = 1
    PROVIDER_RETRY_STATUS_MESSAGE = "模型服務暫時忙碌，我會稍等後重試。"
    LLM_COMPACTION_SYSTEM_PROMPT = f"""You are a context compaction engine for an autonomous assistant.
Compress the provided conversation state into a concise, factual Markdown handoff snapshot.
Do not solve the user's task. Do not ask questions. Do not invent facts.
Treat this as a handoff to a future context window of the same assistant.
Preserve enough state to continue work without asking the user to repeat details.
Do not turn old requests into new instructions. Distinguish clearly between completed work and remaining work.
Preserve verification requirements, missing evidence, review findings, quality gaps, and blockers exactly. Never convert incomplete work into completed work.

Output exactly these sections when applicable:
{COMPACTED_TASK_STATE_HEADING}
## Current Goal
## Latest User Instruction
## Important Context And Constraints
## Completed Work
## Remaining Work
## Relevant Files, Tools, Or IDs
## Recent Tool Results
## Open Questions Or Blockers
"""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        tools: ToolRegistry,
        tools_config: ToolsConfig | None = None,
        empty_response_fallback: str,
        repeated_invalid_tool_call_fallback: str,
        save_message: Callable[[str, str, str, str | None], Awaitable[None]],
        format_log_preview: Callable[..., str],
        summarize_messages: Callable[..., str],
        sanitize_response_content: Callable[[str], str],
        context_compaction_enabled: bool = False,
        context_compaction_token_budget: int = 0,
        context_window_tokens: int | None = None,
        context_output_reserve_tokens: int | None = None,
        context_compaction_threshold_ratio: float = 0.9,
        context_compaction_min_messages: int = 8,
        context_compaction_strategy: str = "deterministic",
        context_compaction_llm: DocumentLlmConfig | None = None,
        context_overflow_error_markers: Sequence[str] | None = None,
        llm_request_timeout_seconds: float | None = None,
    ):
        self.provider = provider
        if llm_request_timeout_seconds is None:
            raise ValueError("llm_request_timeout_seconds must be provided from agent config")
        if context_output_reserve_tokens is None:
            raise ValueError("context_output_reserve_tokens must be provided from agent config")
        self.tools = tools
        self.llm_request_timeout_seconds = max(0.001, float(llm_request_timeout_seconds))
        self.context_compaction_enabled = context_compaction_enabled
        self.context_compaction_token_budget = max(0, context_compaction_token_budget)
        self.context_window_tokens = context_window_tokens
        self.context_output_reserve_tokens = max(0, context_output_reserve_tokens)
        self.context_compaction_threshold_ratio = context_compaction_threshold_ratio
        self.context_compaction_min_messages = max(1, context_compaction_min_messages)
        self.context_compaction_strategy = context_compaction_strategy
        self.context_compaction_llm = context_compaction_llm
        marker_source = (
            DEFAULT_CONTEXT_OVERFLOW_ERROR_MARKERS
            if context_overflow_error_markers is None
            else context_overflow_error_markers
        )
        self.context_overflow_error_markers = tuple(str(marker).strip().lower() for marker in marker_source if str(marker).strip())
        self.tools_config = tools_config or ToolsConfig()
        self.tool_result_max_chars = max(200, self.tools_config.tool_result_max_chars)
        self.exec_result_max_chars = max(200, self.tools_config.exec_result_max_chars)
        self.empty_response_fallback = empty_response_fallback
        self.repeated_invalid_tool_call_fallback = repeated_invalid_tool_call_fallback
        self.format_log_preview = format_log_preview
        self.summarize_messages = summarize_messages
        self.sanitize_response_content = sanitize_response_content
        self.tool_result_persistence = ToolResultPersistence(
            save_message=save_message,
        )

    def _context_request_kwargs(self, provider: LLMProvider) -> dict[str, Any]:
        hook = getattr(provider, "context_request_kwargs", None)
        if not callable(hook):
            return {}
        kwargs = hook(output_token_reserve=self.context_output_reserve_tokens)
        return dict(kwargs) if isinstance(kwargs, dict) else {}

    @staticmethod
    def _should_refresh_main_system_after_tool(tool_name: str, tool_args: dict[str, Any]) -> bool:
        """Skill/subagent definitions on disk may change; optional mid-loop system rebuild."""
        if tool_name not in ExecutionEngine._MAIN_SYSTEM_REFRESH_TOOLS:
            return False
        action = tool_args.get("action")
        return action in ExecutionEngine._MAIN_SYSTEM_REFRESH_ACTIONS

    @staticmethod
    def _tool_result_ok_for_system_refresh(result: str) -> bool:
        return classify_tool_result_status(result).ok

    @staticmethod
    def _classify_tool_result(result: str) -> str | None:
        """Classify tool-result errors that should trigger early stopping."""
        return classify_tool_result_status(result).repeated_error_key

    @staticmethod
    def _tool_result_looks_like_failure(result: str) -> bool:
        return not classify_tool_result_status(result).ok

    def _repeated_invalid_tool_call_content(self, result: str) -> str:
        return format_repeated_invalid_tool_call_content(self.repeated_invalid_tool_call_fallback, result)

    @staticmethod
    def _extract_delegate_task_info(result: str) -> tuple[str | None, str | None]:
        """Parse the delegate tool's stable task id and prompt type from its result text."""
        task_id = None
        prompt_type = None
        for line in str(result or "").splitlines():
            task_id = task_id or parse_subagent_result_line(line, SUBAGENT_TASK_ID_LABEL)
            prompt_type = prompt_type or parse_subagent_result_line(line, SUBAGENT_PROMPT_TYPE_LABEL)
        return task_id, prompt_type

    @staticmethod
    def _should_force_final_after_web_sources(
        task_artifacts: list[TaskArtifact],
        tool_evidence: list[ToolEvidence],
    ) -> bool:
        web_artifacts = [
            artifact
            for artifact in task_artifacts
            if artifact.ok and is_web_source_artifact_kind(artifact.kind) and artifact.metadata.get("sources")
        ]
        if not web_artifacts:
            return False

        for artifact in web_artifacts:
            if not is_web_research_source_artifact_tool(artifact.source_tool):
                continue
            coverage = artifact.metadata.get("coverage")
            if isinstance(coverage, dict) and coverage.get("target_met"):
                return True

        traceable_web_evidence_count = 0
        for evidence in tool_evidence:
            if not evidence.ok or not is_web_source_evidence_tool(evidence.name):
                continue
            if evidence.metadata.get("sources"):
                traceable_web_evidence_count += 1
        return traceable_web_evidence_count >= 2

    @classmethod
    async def _emit_response_deltas(
        cls,
        content: str,
        *,
        part_id: str,
        on_response_delta: Callable[[str, str, str, int], Awaitable[None]] | None,
    ) -> None:
        """Project a completed visible response into streaming-compatible chunks."""
        if on_response_delta is None:
            return
        text = str(content or "")
        if not text:
            return
        sequence = 0
        for start in range(0, len(text), cls.RESPONSE_DELTA_CHARS):
            sequence += 1
            state = "completed" if start + cls.RESPONSE_DELTA_CHARS >= len(text) else "running"
            await on_response_delta(part_id, text[start:start + cls.RESPONSE_DELTA_CHARS], state, sequence)

    @classmethod
    def _summarize_tool_result_for_context(cls, tool_name: str, result: str) -> str:
        """Shrink verbose tool output before feeding it back into the LLM loop."""
        text = result.strip()
        if tool_name == EXEC_TOOL_NAME:
            return cls._summarize_exec_result_for_context(text)
        if len(text) <= cls.TOOL_RESULT_MAX_CHARS:
            return text

        head_limit = cls.TOOL_RESULT_MAX_CHARS // 2
        tail_limit = cls.TOOL_RESULT_MAX_CHARS - head_limit
        head = text[:head_limit].rstrip()
        tail = text[-tail_limit:].lstrip()
        return (
            f"[tool:{tool_name}] Output truncated for context. Full result was persisted separately "
            f"({len(text)} chars total).\n"
            f"--- BEGIN HEAD ---\n{head}\n"
            f"--- MIDDLE TRUNCATED ---\n"
            f"--- END TAIL ---\n{tail}"
        )

    @staticmethod
    def _sanitize_tool_args_for_display(active_tools: ToolRegistry, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        tool = active_tools.get(tool_name)
        if tool is None:
            return dict(tool_args)
        safe_args = tool.sanitize_params_for_display(tool_args)
        return dict(safe_args) if isinstance(safe_args, dict) else {}

    @staticmethod
    def _sanitize_tool_input_delta_for_display(active_tools: ToolRegistry, tool_name: str, delta: str) -> str:
        tool = active_tools.get(tool_name)
        if tool is None:
            return str(delta or "")
        return str(tool.sanitize_input_delta_for_display(str(delta or "")))

    @classmethod
    def _summarize_exec_result_for_context(cls, text: str) -> str:
        """Prefer owned tool error envelopes and the latest lines for shell command output."""
        if len(text) <= cls.EXEC_RESULT_MAX_CHARS:
            return text

        status = classify_tool_result_status(text)
        summary_text = status.error if not status.ok and status.error else text
        lines = [line for line in summary_text.splitlines() if line.strip()]
        first_lines = lines[:6]
        stderr_lines = [line for line in lines if "[stderr]" in line][:4]
        error_lines = lines[:2] if not status.ok and status.error else cls._tool_error_highlight_lines(lines)
        tail_lines = lines[-8:]

        summary_parts: list[str] = [
            f"[tool:exec] Output truncated for context. Full result was persisted separately ({len(text)} chars total)."
        ]
        if error_lines:
            summary_parts.extend(["Timeout/Error summary:", *error_lines])
        elif not status.ok and lines:
            summary_parts.extend(["Error summary:", lines[0]])

        if stderr_lines:
            summary_parts.extend(["stderr highlights:", *stderr_lines])

        if first_lines:
            summary_parts.extend(["output start:", *first_lines])

        if tail_lines:
            summary_parts.extend(["output tail:", *tail_lines])

        summarized = "\n".join(summary_parts)
        if len(summarized) <= cls.EXEC_RESULT_MAX_CHARS:
            return summarized

        return summarized[: cls.EXEC_RESULT_MAX_CHARS].rstrip() + "\n... (exec context summary truncated)"

    def _summarize_tool_result_for_context_with_config(self, tool_name: str, result: str) -> str:
        """Shrink verbose tool output using runtime-configured limits."""
        text = result.strip()
        if tool_name == EXEC_TOOL_NAME:
            return self._summarize_exec_result_for_context_with_config(text)
        if len(text) <= self.tool_result_max_chars:
            return text

        head_limit = self.tool_result_max_chars // 2
        tail_limit = self.tool_result_max_chars - head_limit
        head = text[:head_limit].rstrip()
        tail = text[-tail_limit:].lstrip()
        return (
            f"[tool:{tool_name}] Output truncated for context. Full result was persisted separately "
            f"({len(text)} chars total).\n"
            f"--- BEGIN HEAD ---\n{head}\n"
            f"--- MIDDLE TRUNCATED ---\n"
            f"--- END TAIL ---\n{tail}"
        )

    def _summarize_exec_result_for_context_with_config(self, text: str) -> str:
        """Prefer owned tool error envelopes and latest lines for shell output using configured limits."""
        if len(text) <= self.exec_result_max_chars:
            return text

        status = classify_tool_result_status(text)
        summary_text = status.error if not status.ok and status.error else text
        lines = [line for line in summary_text.splitlines() if line.strip()]
        first_lines = lines[:6]
        stderr_lines = [line for line in lines if "[stderr]" in line][:4]
        error_lines = lines[:2] if not status.ok and status.error else self._tool_error_highlight_lines(lines)
        tail_lines = lines[-8:]

        summary_parts: list[str] = [
            f"[tool:exec] Output truncated for context. Full result was persisted separately ({len(text)} chars total)."
        ]
        if error_lines:
            summary_parts.extend(["Timeout/Error summary:", *error_lines])
        elif not status.ok and lines:
            summary_parts.extend(["Error summary:", lines[0]])

        if stderr_lines:
            summary_parts.extend(["stderr highlights:", *stderr_lines])

        if first_lines:
            summary_parts.extend(["output start:", *first_lines])

        if tail_lines:
            summary_parts.extend(["output tail:", *tail_lines])

        summarized = "\n".join(summary_parts)
        if len(summarized) <= self.exec_result_max_chars:
            return summarized

        return summarized[: self.exec_result_max_chars].rstrip() + "\n... (exec context summary truncated)"

    @staticmethod
    def _tool_error_highlight_lines(lines: list[str], *, limit: int = 2) -> list[str]:
        highlights: list[str] = []
        for line in lines:
            if not classify_tool_result_status(line).ok:
                highlights.append(line)
                if len(highlights) >= limit:
                    break
        return highlights

    @staticmethod
    def _summarize_tool_names(tool_calls: list[Any] | None) -> str:
        """Build a compact tool-name list for diagnostics."""
        if not tool_calls:
            return "-"
        names = [getattr(tc, "name", "") or "<unknown>" for tc in tool_calls]
        preview = ", ".join(names[:5])
        if len(names) > 5:
            preview += f", ... (+{len(names) - 5} more)"
        return preview

    def _looks_like_context_overflow(self, exc: Exception) -> bool:
        """Return whether an LLM exception appears to be caused by context size."""
        text = f"{type(exc).__name__}: {str(exc)}".lower()
        return any(marker in text for marker in self.context_overflow_error_markers)

    @staticmethod
    def _raise_if_cancel_requested(should_cancel: Callable[[], bool] | None) -> None:
        """Raise a cooperative cancellation error when the current run was cancelled."""
        if should_cancel is not None and should_cancel():
            raise RunCancelledError("run cancellation requested")

    def _format_raw_log_preview(self, content: str | list[dict[str, Any]] | None, max_chars: int = 160) -> str:
        """Build a redacted preview without stripping hidden assistant blocks."""
        try:
            return self.format_log_preview(content, max_chars=max_chars, strip_internal=False)
        except TypeError:
            return self.format_log_preview(content, max_chars=max_chars)

    @classmethod
    def _format_tool_history_for_user(cls, tool_results_history: list[str]) -> str:
        if not tool_results_history:
            return ""
        summaries = [
            cls._summarize_tool_history_item_for_user(result)
            for result in tool_results_history[-5:]
        ]
        return "\n\n我嘗試了以下工具但未能完成任務：\n" + "\n".join(
            f"- {summary}" for summary in summaries
        )

    @staticmethod
    def _extract_structured_preview_from_detail(detail: str) -> str | None:
        parsed = ExecutionEngine._parse_json_object_from_text(detail)
        if isinstance(parsed, dict):
            summary = parsed.get("summary") or parsed.get("error") or parsed.get("title")
            if summary:
                return str(summary)
        for key in ("summary", "error", "title"):
            summary = ExecutionEngine._extract_json_string_field_preview(detail, key)
            if summary:
                return summary
        return None

    @staticmethod
    def _parse_json_object_from_text(text: str) -> dict | None:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", str(text or "")):
            try:
                parsed, _ = decoder.raw_decode(str(text or "")[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _extract_json_string_field_preview(text: str, key: str) -> str | None:
        pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)'
        match = re.search(pattern, str(text or ""))
        if not match:
            return None
        raw = match.group(1)
        try:
            return str(json.loads(f'"{raw}"'))
        except Exception:
            return raw.replace(r"\"", '"')

    @staticmethod
    def _summarize_tool_history_item_for_user(result: str) -> str:
        text = str(result or "").strip()
        if not text:
            return "工具呼叫沒有回傳可顯示內容。"
        tool_name, separator, detail = text.partition(":")
        if not separator:
            return text[:220]
        detail = detail.strip()
        if (
            "Output truncated for context" in detail
            or "[tool:" in detail
            or "--- BEGIN HEAD ---" in detail
        ):
            return f"{tool_name}: 工具輸出過長，已省略原始內容；請在 Trace 查看詳細結果。"
        summary = ExecutionEngine._extract_structured_preview_from_detail(detail)
        if summary:
            return f"{tool_name}: {summary[:180]}"
        if len(detail) > 180:
            detail = detail[:177].rstrip() + "..."
        return f"{tool_name}: {detail}"

    def _get_token_model(self, provider: LLMProvider | None = None) -> str | None:
        """Best-effort model name lookup for local token estimates."""
        active_provider = provider or self.provider
        get_default_model = getattr(active_provider, "get_default_model", None)
        if not callable(get_default_model):
            return None
        try:
            return str(get_default_model() or "") or None
        except Exception:
            return None

    @staticmethod
    def _estimate_tool_schema_tokens(tools: list[dict[str, Any]] | None, *, model: str | None) -> int:
        if not tools:
            return 0
        try:
            tool_schema_text = json.dumps(tools, ensure_ascii=False, sort_keys=True)
        except Exception:
            tool_schema_text = str(tools)
        return count_text_tokens(tool_schema_text, model=model)

    def _estimate_request_tokens(
        self,
        chat_messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        *,
        provider: LLMProvider | None = None,
    ) -> tuple[int, int, int]:
        model = self._get_token_model(provider)
        message_tokens = count_messages_tokens(chat_messages, model=model)
        tool_schema_tokens = self._estimate_tool_schema_tokens(tools, model=model)
        return message_tokens + tool_schema_tokens, message_tokens, tool_schema_tokens

    @staticmethod
    def _usage_int(usage: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _reasoning_tokens(cls, usage: dict[str, Any]) -> int | None:
        details = usage.get("completion_tokens_details")
        if not isinstance(details, dict):
            return None
        return cls._usage_int(details, "reasoning_tokens")

    @classmethod
    def _cached_tokens(cls, usage: dict[str, Any]) -> int | None:
        details = usage.get("prompt_tokens_details")
        if not isinstance(details, dict):
            return None
        return cls._usage_int(details, "cached_tokens")

    async def _build_proactive_compaction(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        tool_results_history: list[str],
        work_state_summary: str = "",
        provider: LLMProvider | None = None,
    ) -> _ProactiveCompactionResult | None:
        """Return compacted messages when the next request is nearing the configured budget."""
        if not self.context_compaction_enabled:
            return None
        if self.context_compaction_token_budget <= 0 or self.context_compaction_threshold_ratio <= 0:
            return None
        if len(chat_messages) < self.context_compaction_min_messages:
            return None

        threshold_tokens = max(1, int(self.context_compaction_token_budget * self.context_compaction_threshold_ratio))
        estimated_tokens, message_tokens, tool_schema_tokens = self._estimate_request_tokens(
            chat_messages,
            tools,
            provider=provider,
        )
        if estimated_tokens < threshold_tokens:
            return None

        llm_fallback_reason: str | None = None
        llm_fallback_error: str | None = None
        if self.context_compaction_strategy == "llm":
            llm_attempt = await self._compact_messages_with_llm(
                log_id,
                chat_messages,
                tool_results_history=tool_results_history,
                work_state_summary=work_state_summary,
                provider=provider,
            )
            compacted_messages = llm_attempt.messages
            if compacted_messages is not None:
                compacted_tokens, _, _ = self._estimate_request_tokens(compacted_messages, tools, provider=provider)
                if compacted_tokens < estimated_tokens:
                    return _ProactiveCompactionResult(
                        messages=compacted_messages,
                        estimated_tokens=estimated_tokens,
                        compacted_tokens=compacted_tokens,
                        threshold_tokens=threshold_tokens,
                        message_tokens=message_tokens,
                        tool_schema_tokens=tool_schema_tokens,
                        strategy="llm",
                    )
                llm_fallback_reason = LLM_COMPACTION_TOO_LARGE_REASON
                logger.warning(
                    f"[{log_id}] llm.context-compact.llm-too-large | "
                    f"estimated_tokens={estimated_tokens} compacted_tokens={compacted_tokens} fallback=deterministic"
                )
            else:
                llm_fallback_reason = llm_attempt.fallback_reason
                llm_fallback_error = llm_attempt.error

        compacted_messages = self._compact_messages_for_continuation(
            chat_messages,
            tool_results_history=tool_results_history,
            work_state_summary=work_state_summary,
            reason=(
                "The in-turn context was compacted automatically before the LLM request because "
                "it was approaching the configured context budget."
            ),
        )
        if compacted_messages is None:
            return None

        compacted_tokens, _, _ = self._estimate_request_tokens(compacted_messages, tools, provider=provider)
        if compacted_tokens >= estimated_tokens:
            return None

        return _ProactiveCompactionResult(
            messages=compacted_messages,
            estimated_tokens=estimated_tokens,
            compacted_tokens=compacted_tokens,
            threshold_tokens=threshold_tokens,
            message_tokens=message_tokens,
            tool_schema_tokens=tool_schema_tokens,
            strategy="deterministic",
            fallback_reason=llm_fallback_reason,
            error=llm_fallback_error,
        )

    @classmethod
    def _split_leading_system_messages(
        cls,
        chat_messages: list[ChatMessage],
    ) -> tuple[list[ChatMessage], list[ChatMessage]]:
        leading_system: list[ChatMessage] = []
        body_start = 0
        for message in chat_messages:
            if getattr(message, "role", None) != CHAT_ROLE_SYSTEM:
                break
            leading_system.append(ChatMessage(role=CHAT_ROLE_SYSTEM, content=getattr(message, "content", "")))
            body_start += 1
        return leading_system, chat_messages[body_start:]

    @classmethod
    def _clone_tail_message(cls, message: ChatMessage) -> ChatMessage:
        """Clone one recent message while bounding content size for compacted retries."""
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            content = cls._message_content_to_text(content)
        clone = ChatMessage(
            role=getattr(message, "role", "?"),
            content=cls._truncate_text(content, cls.COMPACTED_TAIL_MESSAGE_MAX_CHARS),
        )
        if getattr(message, "tool_call_id", None):
            clone.tool_call_id = getattr(message, "tool_call_id", None)
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            clone.tool_calls = [dict(item) if isinstance(item, dict) else item for item in tool_calls]
        reasoning_details = getattr(message, "reasoning_details", None)
        if reasoning_details:
            clone.reasoning_details = [dict(item) if isinstance(item, dict) else item for item in reasoning_details]
        return clone

    @classmethod
    def _split_compaction_head_and_tail(
        cls,
        messages: list[ChatMessage],
    ) -> tuple[list[ChatMessage], list[ChatMessage]]:
        """Keep a tiny recent tail verbatim and compact only the older head."""
        if len(messages) <= 2:
            return messages, []

        tail_start = max(0, len(messages) - cls.COMPACTED_TAIL_MESSAGE_LIMIT)
        latest_user_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if getattr(messages[index], "role", None) == CHAT_ROLE_USER),
            None,
        )
        if latest_user_index is not None and latest_user_index >= max(0, tail_start - 1):
            tail_start = min(tail_start, latest_user_index)
        if tail_start <= 0:
            return messages, []

        return messages[:tail_start], [cls._clone_tail_message(message) for message in messages[tail_start:]]

    @classmethod
    def _build_compacted_message_list(
        cls,
        *,
        leading_system: list[ChatMessage],
        summary_sections: list[str],
        tail_messages: list[ChatMessage],
    ) -> list[ChatMessage]:
        compacted = [
            *leading_system,
            ChatMessage(role=CHAT_ROLE_SYSTEM, content="\n".join(summary_sections)),
        ]
        if tail_messages:
            compacted.extend(tail_messages)
            return compacted
        compacted.append(ChatMessage(role=CHAT_ROLE_USER, content=cls.CONTINUATION_AFTER_COMPACTION_MESSAGE))
        return compacted

    @classmethod
    def _extract_compaction_handoff(cls, messages: list[ChatMessage]) -> str | None:
        """Return the latest compacted-state system handoff for continuation prompts."""
        for message in reversed(messages):
            if getattr(message, "role", None) != CHAT_ROLE_SYSTEM:
                continue
            content = cls._message_content_to_text(getattr(message, "content", ""))
            if contains_compaction_handoff(content):
                return cls._truncate_text(content, cls.COMPACTION_HANDOFF_MAX_CHARS)
        return None

    @classmethod
    def _latest_user_text(cls, messages: list[ChatMessage], *, max_chars: int) -> str:
        latest_user = next((message for message in reversed(messages) if getattr(message, "role", None) == CHAT_ROLE_USER), None)
        if latest_user is None:
            return ""
        return cls._truncate_text(
            cls._message_content_to_text(getattr(latest_user, "content", "")),
            max_chars,
        )

    def _build_llm_compaction_prompt(
        self,
        chat_messages: list[ChatMessage],
        *,
        tool_results_history: list[str],
        work_state_summary: str = "",
    ) -> list[ChatMessage] | None:
        _, body = self._split_leading_system_messages(chat_messages)
        if not body:
            return None

        head, tail = self._split_compaction_head_and_tail(body)
        latest_user_text = self._latest_user_text(body, max_chars=self.COMPACTED_LATEST_USER_MAX_CHARS)
        transcript = self._build_compacted_transcript(
            head,
            max_chars=self.LLM_COMPACTION_TRANSCRIPT_MAX_CHARS,
            message_max_chars=self.LLM_COMPACTION_MESSAGE_MAX_CHARS,
        )
        sections = [
            "# Conversation State To Compact",
            "Use this state to produce a continuation snapshot for the assistant.",
        ]
        if latest_user_text:
            sections.extend(["", "## Latest User Instruction", latest_user_text])
        if work_state_summary.strip():
            sections.extend(["", work_state_summary.strip()])
        sections.extend(["", "## Transcript", transcript or "(no transcript details)"])
        if tail:
            sections.extend([
                "",
                "## Preserved Recent Tail",
                "These exact recent messages will remain verbatim after compaction. Use them as the freshest ground truth.",
                self._build_compacted_transcript(
                    tail,
                    max_chars=self.LLM_COMPACTION_MESSAGE_MAX_CHARS * max(1, len(tail)),
                    message_max_chars=self.LLM_COMPACTION_MESSAGE_MAX_CHARS,
                ),
            ])
        if tool_results_history:
            sections.extend([
                "",
                "## Recent Tool Results",
                "\n".join(f"- {item}" for item in tool_results_history[-12:]),
            ])
        return [
            ChatMessage(role=CHAT_ROLE_SYSTEM, content=self.LLM_COMPACTION_SYSTEM_PROMPT),
            ChatMessage(role=CHAT_ROLE_USER, content="\n".join(sections)),
        ]

    async def _compact_messages_with_llm(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        tool_results_history: list[str],
        work_state_summary: str = "",
        provider: LLMProvider | None = None,
    ) -> _LlmCompactionAttempt:
        compaction_llm = self.context_compaction_llm
        if compaction_llm is None:
            return _LlmCompactionAttempt(fallback_reason=LLM_COMPACTION_CONFIG_MISSING_REASON)

        active_provider = provider or self.provider
        leading_system, body = self._split_leading_system_messages(chat_messages)
        if not body:
            return _LlmCompactionAttempt(fallback_reason=LLM_COMPACTION_NO_BODY_REASON)

        compaction_messages = self._build_llm_compaction_prompt(
            chat_messages,
            tool_results_history=tool_results_history,
            work_state_summary=work_state_summary,
        )
        if compaction_messages is None:
            return _LlmCompactionAttempt(fallback_reason=LLM_COMPACTION_NO_PROMPT_REASON)

        _, tail = self._split_compaction_head_and_tail(body)

        try:
            response = await active_provider.chat(
                messages=compaction_messages,
                tools=None,
                status_callback=None,
                **compaction_llm.decoding_kwargs(),
            )
        except Exception as exc:
            error_preview = self.format_log_preview(str(exc), max_chars=240)
            logger.warning(
                f"[{log_id}] llm.context-compact.llm-error | fallback=deterministic "
                f"error={error_preview}"
            )
            return _LlmCompactionAttempt(fallback_reason=LLM_COMPACTION_ERROR_REASON, error=error_preview)

        summary = self.sanitize_response_content(response.content or "").strip()
        if not summary:
            logger.warning(f"[{log_id}] llm.context-compact.llm-empty | fallback=deterministic")
            return _LlmCompactionAttempt(fallback_reason=LLM_COMPACTION_EMPTY_REASON)

        summary_sections = [
            COMPACTED_CONVERSATION_STATE_HEADING,
            "The in-turn context was compacted by an LLM before the next request because it was approaching the configured context budget.",
            "This summary is a handoff from a previous context window. Continue the same task from this state; do not restart it.",
            "Treat summarized older context as reference only. Prefer the preserved recent tail and current active task state if they conflict with the summary.",
            "This handoff is not completion evidence. Preserve verification, review, evidence, and quality-gate gaps until tools or final answers satisfy them.",
            "Do not ask the user to repeat details already summarized here.",
            "",
            summary,
        ]
        return _LlmCompactionAttempt(
            messages=self._build_compacted_message_list(
                leading_system=leading_system,
                summary_sections=summary_sections,
                tail_messages=tail,
            )
        )

    @staticmethod
    def _message_content_to_text(content: Any) -> str:
        """Render ChatMessage content into compact text for deterministic summaries."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    parts.append(str(item))
                    continue
                item_type = item.get("type")
                if item_type == CHAT_CONTENT_TYPE_TEXT:
                    parts.append(str(item.get("text", "")))
                elif item_type == CHAT_CONTENT_TYPE_IMAGE_URL:
                    parts.append("[image attachment omitted during compaction]")
                else:
                    try:
                        parts.append(json.dumps(item, ensure_ascii=False))
                    except Exception:
                        parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content or "")

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """Truncate text while retaining both the beginning and the end."""
        value = str(text or "").strip()
        if len(value) <= max_chars:
            return value
        tail_chars = max(120, max_chars // 3)
        head_chars = max_chars - tail_chars
        return (
            value[:head_chars].rstrip()
            + f"\n... (truncated {len(value) - max_chars} chars during compaction) ...\n"
            + value[-tail_chars:].lstrip()
        )

    @classmethod
    def _format_tool_calls_for_compaction(cls, tool_calls: list[dict[str, Any]] | None) -> str:
        if not tool_calls:
            return ""

        formatted: list[str] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                formatted.append(cls._truncate_text(str(call), 240))
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = function.get("name") or call.get("name") or "<unknown>"
            args = function.get("arguments") or call.get("arguments") or "{}"
            formatted.append(f"{name}({cls._truncate_text(str(args), 240)})")
        return "; ".join(formatted)

    @classmethod
    def _build_compacted_transcript(
        cls,
        messages: list[ChatMessage],
        *,
        max_chars: int,
        message_max_chars: int | None = None,
    ) -> str:
        lines: list[str] = []
        used_chars = 0
        current_message_max_chars = message_max_chars or cls.COMPACTED_MESSAGE_MAX_CHARS
        for index, message in enumerate(messages, start=1):
            role = getattr(message, "role", "?")
            content = cls._truncate_text(
                cls._message_content_to_text(getattr(message, "content", "")),
                current_message_max_chars,
            )
            tool_call_summary = cls._format_tool_calls_for_compaction(getattr(message, "tool_calls", None))
            tool_call_id = getattr(message, "tool_call_id", None)
            parts = [f"{index}. role={role}"]
            if tool_call_id:
                parts.append(f"tool_call_id={tool_call_id}")
            header = " ".join(parts)
            rendered = header
            if content:
                rendered += f"\n{content}"
            if tool_call_summary:
                rendered += f"\ntool_calls: {tool_call_summary}"

            if lines and used_chars + len(rendered) + 2 > max_chars:
                lines.append("... (older compacted transcript entries omitted due to size) ...")
                break
            lines.append(rendered)
            used_chars += len(rendered) + 2
        return "\n\n".join(lines)

    @classmethod
    def _compact_messages_for_continuation(
        cls,
        chat_messages: list[ChatMessage],
        *,
        tool_results_history: list[str],
        work_state_summary: str = "",
        reason: str | None = None,
    ) -> list[ChatMessage] | None:
        """Create a smaller message list that can retry the same turn after overflow."""
        if not chat_messages:
            return None

        leading_system: list[ChatMessage] = []
        body_start = 0
        for message in chat_messages:
            if getattr(message, "role", None) != CHAT_ROLE_SYSTEM:
                break
            leading_system.append(ChatMessage(role=CHAT_ROLE_SYSTEM, content=getattr(message, "content", "")))
            body_start += 1

        body = chat_messages[body_start:]
        if not body:
            return None

        head, tail = cls._split_compaction_head_and_tail(body)

        latest_user = next((message for message in reversed(body) if getattr(message, "role", None) == CHAT_ROLE_USER), None)
        latest_user_text = ""
        if latest_user is not None:
            latest_user_text = cls._truncate_text(
                cls._message_content_to_text(getattr(latest_user, "content", "")),
                cls.COMPACTED_LATEST_USER_MAX_CHARS,
            )

        transcript = cls._build_compacted_transcript(
            head,
            max_chars=cls.COMPACTED_TRANSCRIPT_MAX_CHARS,
        )
        summary_sections = [
            COMPACTED_CONVERSATION_STATE_HEADING,
            reason
            or "The previous in-turn context was compacted automatically after the LLM reported a context-window error.",
            "This is a handoff from a previous context window. Continue the same task from this state; do not restart it.",
            "Treat summarized older context as reference only. Prefer the preserved recent tail and current active task state if they conflict with the summary.",
            "This handoff is not completion evidence. Preserve verification, review, evidence, and quality-gate gaps until tools or final answers satisfy them.",
            "Do not ask the user to repeat details already summarized here.",
        ]
        if latest_user_text:
            summary_sections.extend(["", "## Latest User Instruction", latest_user_text])
        if work_state_summary.strip():
            summary_sections.extend(["", work_state_summary.strip()])
        summary_sections.extend(["", "## Compacted Transcript", transcript or "(no transcript details)"])
        if tail:
            summary_sections.extend([
                "",
                "## Preserved Recent Tail",
                "Recent live context is preserved verbatim below. Prefer it over any summarized older context if they differ.",
            ])
        if tool_results_history:
            summary_sections.extend([
                "",
                "## Recent Tool Results",
                "\n".join(f"- {item}" for item in tool_results_history[-8:]),
            ])

        return cls._build_compacted_message_list(
            leading_system=leading_system,
            summary_sections=summary_sections,
            tail_messages=tail,
        )


    async def execute_messages(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        allow_tools: bool,
        provider_override: LLMProvider | None = None,
        tool_result_session_id: str | None = None,
        tool_registry: ToolRegistry | None = None,
        on_tool_before_execute: Callable[..., Awaitable[None]] | None = None,
        on_tool_after_execute: Callable[..., Awaitable[None]] | None = None,
        on_llm_status: Callable[[Any], Awaitable[None]] | None = None,
        on_response_delta: Callable[[str, str, str, int], Awaitable[None]] | None = None,
        on_tool_input_delta: Callable[[str, str, str, int], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        refresh_system_prompt: Callable[[], str] | None = None,
        max_tool_iterations: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
        work_state_summary: str = "",
    ) -> ExecutionResult:
        """Execute the prepared messages, including tool calls when enabled."""
        active_provider = provider_override or self.provider
        return await self._execute_messages_with_provider(
            log_id,
            chat_messages,
            allow_tools=allow_tools,
            active_provider=active_provider,
            tool_result_session_id=tool_result_session_id,
            tool_registry=tool_registry,
            on_tool_before_execute=on_tool_before_execute,
            on_tool_after_execute=on_tool_after_execute,
            on_llm_status=on_llm_status,
            on_response_delta=on_response_delta,
            on_tool_input_delta=on_tool_input_delta,
            on_reasoning_delta=on_reasoning_delta,
            refresh_system_prompt=refresh_system_prompt,
            max_tool_iterations=max_tool_iterations,
            should_cancel=should_cancel,
            work_state_summary=work_state_summary,
        )

    async def _execute_messages_with_provider(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        allow_tools: bool,
        active_provider: LLMProvider | None = None,
        tool_result_session_id: str | None = None,
        tool_registry: ToolRegistry | None = None,
        on_tool_before_execute: Callable[..., Awaitable[None]] | None = None,
        on_tool_after_execute: Callable[..., Awaitable[None]] | None = None,
        on_llm_status: Callable[[Any], Awaitable[None]] | None = None,
        on_response_delta: Callable[[str, str, str, int], Awaitable[None]] | None = None,
        on_tool_input_delta: Callable[[str, str, str, int], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        refresh_system_prompt: Callable[[], str] | None = None,
        max_tool_iterations: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
        work_state_summary: str = "",
    ) -> ExecutionResult:
        """Provider-bound execution body used by execute_messages()."""
        active_provider = active_provider or self.provider
        active_tools = tool_registry or self.tools
        tools = None
        if allow_tools and active_tools.tool_names:
            tools = active_tools.get_definitions()
            logger.info(f"[{log_id}] tools.enabled | names={', '.join(active_tools.tool_names)}")

        tool_results_history: list[str] = []
        tool_loop_guardrail = ToolLoopGuardrail()
        empty_response_retried = False
        repeated_tool_error_key: tuple[str, str] | None = None
        repeated_tool_error_count = 0
        executed_tool_calls = 0
        used_configure_skill = False
        had_tool_error = False
        tool_evidence: list[ToolEvidence] = []
        task_artifacts: list[TaskArtifact] = []
        delegated_tasks: list[StoredDelegatedTask] = []
        active_delegate_task_id: str | None = None
        active_delegate_prompt_type: str | None = None
        verification_attempted = False
        verification_passed = False
        context_compactions = 0
        context_compaction_events: list[ContextCompactionEvent] = []
        llm_step_events: list[LlmStepEvent] = []
        latest_compaction_handoff: str | None = None
        proactive_context_compactions = 0
        overflow_context_compactions = 0
        iteration_limit = (
            max_tool_iterations if max_tool_iterations is not None else self.tools_config.max_tool_iterations
        )

        for iteration in range(iteration_limit):
            self._raise_if_cancel_requested(should_cancel)
            if proactive_context_compactions < self.PROACTIVE_CONTEXT_COMPACTION_LIMIT:
                proactive_compaction = await self._build_proactive_compaction(
                    log_id,
                    chat_messages,
                    tools=tools,
                    tool_results_history=tool_results_history,
                    work_state_summary=work_state_summary,
                    provider=active_provider,
                )
                if proactive_compaction is not None:
                    proactive_context_compactions += 1
                    context_compactions += 1
                    before_count = len(chat_messages)
                    chat_messages[:] = proactive_compaction.messages
                    latest_compaction_handoff = self._extract_compaction_handoff(chat_messages)
                    context_compaction_events.append(
                        ContextCompactionEvent(
                            trigger="proactive",
                            strategy=proactive_compaction.strategy,
                            outcome="fallback" if proactive_compaction.fallback_reason else "compacted",
                            iteration=iteration + 1,
                            messages_before=before_count,
                            messages_after=len(chat_messages),
                            estimated_tokens=proactive_compaction.estimated_tokens,
                            compacted_tokens=proactive_compaction.compacted_tokens,
                            threshold_tokens=proactive_compaction.threshold_tokens,
                            budget_tokens=self.context_compaction_token_budget,
                            context_window_tokens=self.context_window_tokens,
                            output_reserve_tokens=self.context_output_reserve_tokens,
                            message_tokens=proactive_compaction.message_tokens,
                            tool_schema_tokens=proactive_compaction.tool_schema_tokens,
                            fallback_reason=proactive_compaction.fallback_reason,
                            error=proactive_compaction.error,
                        )
                    )
                    logger.warning(
                        f"[{log_id}] llm.context-proactive-compact | iter={iteration + 1} "
                        f"compaction={proactive_context_compactions}/{self.PROACTIVE_CONTEXT_COMPACTION_LIMIT} "
                        f"strategy={proactive_compaction.strategy} outcome={context_compaction_events[-1].outcome} "
                        f"estimated_tokens={proactive_compaction.estimated_tokens} compacted_tokens={proactive_compaction.compacted_tokens} "
                        f"threshold={proactive_compaction.threshold_tokens} budget={self.context_compaction_token_budget} "
                        f"message_tokens={proactive_compaction.message_tokens} tool_schema_tokens={proactive_compaction.tool_schema_tokens} "
                        f"messages_before={before_count} messages_after={len(chat_messages)}"
                    )
                    if on_llm_status is not None:
                        try:
                            await on_llm_status(
                                {
                                    "message": self.PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE,
                                    "status": "compacting",
                                    "trigger": "proactive_context_compaction",
                                }
                            )
                        except Exception:
                            logger.exception(f"[{log_id}] llm.context-proactive.status-hook.error")

            logger.info(
                f"[{log_id}] llm.request | iter={iteration + 1} messages={len(chat_messages)} "
                f"tools={'on' if tools else 'off'} tail={self.summarize_messages(chat_messages)}"
            )
            response_delta_count = 0
            response_part_id = f"assistant:{log_id}:{iteration + 1}"

            async def _provider_response_delta(delta: str) -> None:
                nonlocal response_delta_count
                response_delta_count += 1
                if on_response_delta is not None:
                    await on_response_delta(response_part_id, delta, "running", response_delta_count)

            async def _provider_tool_input_delta(tool_call_id: str, tool_name: str, delta: str, sequence: int = 0) -> None:
                if on_tool_input_delta is None:
                    return
                await on_tool_input_delta(
                    tool_call_id,
                    tool_name,
                    self._sanitize_tool_input_delta_for_display(active_tools, tool_name, delta),
                    sequence,
                )

            while True:
                self._raise_if_cancel_requested(should_cancel)
                request_attempt = len([event for event in llm_step_events if event.iteration == iteration + 1]) + 1
                estimated_tokens, message_tokens, tool_schema_tokens = self._estimate_request_tokens(
                    chat_messages,
                    tools,
                    provider=active_provider,
                )
                started_at = time.perf_counter()
                try:
                    logger.info(
                        f"[{log_id}] llm.request.attempt | iter={iteration + 1} attempt={request_attempt} "
                        f"provider={type(active_provider).__name__} model={self._get_token_model(active_provider) or '-'} "
                        f"messages={len(chat_messages)} tools={len(tools or [])} "
                        f"estimated_tokens={estimated_tokens} message_tokens={message_tokens} tool_schema_tokens={tool_schema_tokens}"
                    )
                    response = await asyncio.wait_for(
                        active_provider.chat(
                            messages=chat_messages,
                            tools=tools,
                            status_callback=on_llm_status,
                            response_delta_callback=_provider_response_delta if on_response_delta is not None else None,
                            tool_input_delta_callback=_provider_tool_input_delta if on_tool_input_delta is not None else None,
                            reasoning_delta_callback=on_reasoning_delta,
                            **self._context_request_kwargs(active_provider),
                        ),
                        timeout=self.llm_request_timeout_seconds,
                    )
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    usage = dict(getattr(response, "usage", {}) or {})
                    output_tokens = self._usage_int(usage, "completion_tokens", "output_tokens")
                    total_tokens = self._usage_int(usage, "total_tokens")
                    reasoning_tokens = self._reasoning_tokens(usage)
                    cached_tokens = self._cached_tokens(usage)
                    finish_reason = getattr(response, "finish_reason", None)
                    llm_step_events.append(
                        LlmStepEvent(
                            iteration=iteration + 1,
                            attempt=request_attempt,
                            status=LLM_STEP_COMPLETED_STATUS,
                            provider=type(active_provider).__name__,
                            model=getattr(response, "model", None),
                            duration_ms=duration_ms,
                            estimated_input_tokens=estimated_tokens,
                            message_tokens=message_tokens,
                            tool_schema_tokens=tool_schema_tokens,
                            tools_enabled=bool(tools),
                            tool_count=len(tools or []),
                            output_tokens=output_tokens,
                            total_tokens=total_tokens,
                            reasoning_tokens=reasoning_tokens,
                            cached_tokens=cached_tokens,
                            finish_reason=finish_reason,
                            tool_calls=len(getattr(response, "tool_calls", None) or []),
                        )
                    )
                    break
                except Exception as exc:
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    error_preview = self.format_log_preview(str(exc), max_chars=240)
                    retry_delay = retry_delay_from_error(exc, attempt=request_attempt)
                    llm_step_events.append(
                        LlmStepEvent(
                            iteration=iteration + 1,
                            attempt=request_attempt,
                            status=LLM_STEP_ERROR_STATUS,
                            provider=type(active_provider).__name__,
                            model=self._get_token_model(active_provider),
                            duration_ms=duration_ms,
                            estimated_input_tokens=estimated_tokens,
                            message_tokens=message_tokens,
                            tool_schema_tokens=tool_schema_tokens,
                            tools_enabled=bool(tools),
                            tool_count=len(tools or []),
                            error=error_preview,
                            retryable=retry_delay.retryable,
                            retry_after_ms=retry_delay.retry_after_ms,
                            next_retry_at=retry_delay.next_retry_at,
                        )
                    )
                    if (
                        overflow_context_compactions < self.CONTEXT_COMPACTION_RETRY_LIMIT
                        and self._looks_like_context_overflow(exc)
                    ):
                        compacted_messages = self._compact_messages_for_continuation(
                            chat_messages,
                            tool_results_history=tool_results_history,
                            work_state_summary=work_state_summary,
                        )
                        if compacted_messages is not None:
                            overflow_context_compactions += 1
                            context_compactions += 1
                            before_count = len(chat_messages)
                            compacted_tokens, _, _ = self._estimate_request_tokens(
                                compacted_messages,
                                tools,
                                provider=active_provider,
                            )
                            chat_messages[:] = compacted_messages
                            latest_compaction_handoff = self._extract_compaction_handoff(chat_messages)
                            context_compaction_events.append(
                                ContextCompactionEvent(
                                    trigger="overflow",
                                    strategy="deterministic",
                                    outcome="compacted",
                                    iteration=iteration + 1,
                                    messages_before=before_count,
                                    messages_after=len(chat_messages),
                                    estimated_tokens=estimated_tokens,
                                    compacted_tokens=compacted_tokens,
                                    budget_tokens=self.context_compaction_token_budget,
                                    context_window_tokens=self.context_window_tokens,
                                    output_reserve_tokens=self.context_output_reserve_tokens,
                                    message_tokens=message_tokens,
                                    tool_schema_tokens=tool_schema_tokens,
                                    error=error_preview,
                                )
                            )
                            logger.warning(
                                f"[{log_id}] llm.context-overflow | iter={iteration + 1} "
                                f"compaction={overflow_context_compactions}/{self.CONTEXT_COMPACTION_RETRY_LIMIT} "
                                f"messages_before={before_count} messages_after={len(chat_messages)} retrying=true "
                                f"error={error_preview}"
                            )
                            if on_llm_status is not None:
                                try:
                                    await on_llm_status(
                                        {
                                            "message": self.CONTEXT_OVERFLOW_STATUS_MESSAGE,
                                            "status": "compacting",
                                            "trigger": "context_overflow",
                                        }
                                    )
                                except Exception:
                                    logger.exception(f"[{log_id}] llm.context-overflow.status-hook.error")
                            continue

                    if retry_delay.retryable and request_attempt <= self.PROVIDER_RETRY_LIMIT:
                        recovered = False
                        recover_after_error = getattr(active_provider, "recover_after_error", None)
                        if callable(recover_after_error):
                            try:
                                recovered = bool(recover_after_error(exc))
                            except Exception:
                                logger.exception(f"[{log_id}] llm.retry.recover-hook.error | iter={iteration + 1}")
                        logger.warning(
                            f"[{log_id}] llm.retryable-error | iter={iteration + 1} attempt={request_attempt} "
                            f"retry_after_ms={retry_delay.retry_after_ms} recovered={recovered} error={error_preview}"
                        )
                        if on_llm_status is not None:
                            try:
                                await on_llm_status(
                                    {
                                        "message": self.PROVIDER_RETRY_STATUS_MESSAGE,
                                        "status": "retry",
                                        "trigger": "provider_retry",
                                    }
                                )
                            except Exception:
                                logger.exception(f"[{log_id}] llm.retry.status-hook.error")
                        await asyncio.sleep((retry_delay.retry_after_ms or 0) / 1000)
                        continue

                    logger.exception(
                        f"[{log_id}] llm.error | iter={iteration + 1} messages={len(chat_messages)} "
                        f"tools={'on' if tools else 'off'} tail={self.summarize_messages(chat_messages)}"
                    )
                    raise

            raw_content = response.content or ""
            response.content = self.sanitize_response_content(raw_content)
            sanitized_became_empty = bool(raw_content.strip() and not response.content)
            tool_calls_count = len(response.tool_calls or [])
            assistant_internal_only_response = sanitized_became_empty and tool_calls_count == 0
            reasoning_details_count = len(response.reasoning_details or [])
            logger.info(
                f"[{log_id}] llm.response | iter={iteration + 1} model={response.model} raw_len={len(raw_content)} "
                f"visible_len={len(response.content)} tool_calls={tool_calls_count} "
                f"finish_reason={finish_reason or '-'} output_tokens={output_tokens if output_tokens is not None else '-'} "
                f"total_tokens={total_tokens if total_tokens is not None else '-'} "
                f"reasoning_tokens={reasoning_tokens if reasoning_tokens is not None else '-'} "
                f"cached_tokens={cached_tokens if cached_tokens is not None else '-'} reasoning_details={reasoning_details_count} "
                f"preview={self.format_log_preview(response.content)}"
            )
            logger.debug(
                f"[{log_id}] llm.response.raw | iter={iteration + 1} raw_len={len(raw_content)} "
                f"raw_preview={self._format_raw_log_preview(raw_content, max_chars=500)}"
            )
            if sanitized_became_empty:
                logger.warning(
                    f"[{log_id}] llm.sanitized-empty | iter={iteration + 1} raw_len={len(raw_content)} raw_non_ws={len(raw_content.strip())} "
                    f"tool_calls={tool_calls_count} tools={self._summarize_tool_names(response.tool_calls)} "
                    f"raw_preview={self._format_raw_log_preview(raw_content, max_chars=240)}"
                )
                logger.warning(
                    f"[{log_id}] llm.raw-hidden-blocks | iter={iteration + 1} "
                    f"raw_preview={self._format_raw_log_preview(raw_content, max_chars=500)}"
                )
                if "<system-reminder>" in raw_content:
                    logger.warning(
                        f"[{log_id}] llm.system-reminder-hidden | iter={iteration + 1} raw_len={len(raw_content)} "
                        f"visible_len={len(response.content)} tool_calls={tool_calls_count} "
                        f"tools={self._summarize_tool_names(response.tool_calls)}"
                    )
                    logger.warning(
                        f"[{log_id}] llm.system-reminder-hidden-state | iter={iteration + 1} "
                        f"phase={'tool-call' if tool_calls_count else 'final-response'} "
                        f"tool_history_count={len(tool_results_history)} sanitized_from_nonempty=true"
                    )

            if response_delta_count > 0 and on_response_delta is not None:
                await on_response_delta(response_part_id, "", "completed", response_delta_count + 1)

            if response.tool_calls:
                if not tools:
                    logger.warning(
                        f"[{log_id}] llm.tool-calls-ignored | iter={iteration + 1} count={len(response.tool_calls)} tools=off"
                    )
                    if not response.content:
                        content = self.empty_response_fallback
                        await self._emit_response_deltas(
                            content,
                            part_id=response_part_id,
                            on_response_delta=on_response_delta,
                        )
                        return ExecutionResult(
                            content=content,
                            executed_tool_calls=executed_tool_calls,
                            used_configure_skill=used_configure_skill,
                            had_tool_error=had_tool_error,
                            delegated_tasks=tuple(delegated_tasks),
                            active_delegate_task_id=active_delegate_task_id,
                            active_delegate_prompt_type=active_delegate_prompt_type,
                            verification_attempted=verification_attempted,
                            verification_passed=verification_passed,
                            compaction_handoff=latest_compaction_handoff,
                            context_compactions=context_compactions,
                            context_compaction_events=context_compaction_events,
                            llm_step_events=llm_step_events,
                            assistant_internal_only_response=True,
                            tool_evidence=tuple(tool_evidence),
                            task_artifacts=tuple(task_artifacts),
                        )

                    if response_delta_count == 0:
                        await self._emit_response_deltas(
                            response.content,
                            part_id=response_part_id,
                            on_response_delta=on_response_delta,
                        )
                    return ExecutionResult(
                        content=response.content,
                        executed_tool_calls=executed_tool_calls,
                        used_configure_skill=used_configure_skill,
                        had_tool_error=had_tool_error,
                        delegated_tasks=tuple(delegated_tasks),
                        active_delegate_task_id=active_delegate_task_id,
                        active_delegate_prompt_type=active_delegate_prompt_type,
                        verification_attempted=verification_attempted,
                        verification_passed=verification_passed,
                        compaction_handoff=latest_compaction_handoff,
                        context_compactions=context_compactions,
                        context_compaction_events=context_compaction_events,
                        llm_step_events=llm_step_events,
                        reasoning_details=response.reasoning_details,
                        tool_evidence=tuple(tool_evidence),
                        task_artifacts=tuple(task_artifacts),
                    )

                logger.info(
                    f"[{log_id}] llm.tool-calls | iter={iteration + 1} count={len(response.tool_calls)} "
                    f"tools={self._summarize_tool_names(response.tool_calls)} visible_len={len(response.content)}"
                )

                tool_calls_api = []
                for tc in response.tool_calls:
                    tool_args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    display_tool_args = self._sanitize_tool_args_for_display(active_tools, tc.name, tool_args)
                    tool_calls_api.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(display_tool_args, ensure_ascii=False),
                        },
                    })

                chat_messages.append(ChatMessage(
                    role=CHAT_ROLE_ASSISTANT,
                    content=response.content or "",
                    tool_calls=tool_calls_api,
                    reasoning_details=response.reasoning_details,
                ))

                for tc in response.tool_calls:
                    self._raise_if_cancel_requested(should_cancel)
                    tool_name = tc.name
                    tool_args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    display_tool_args = self._sanitize_tool_args_for_display(active_tools, tool_name, tool_args)
                    args_preview = self.format_log_preview(json.dumps(display_tool_args, ensure_ascii=False), max_chars=200)
                    logger.info(f"[{log_id}] tool.run | id={tc.id} name={tool_name} args={args_preview}")
                    tool_started = False
                    before_guardrail = tool_loop_guardrail.before_call(tool_name, display_tool_args)

                    async def _notify_tool_before_execute(name: str, args: dict[str, Any]) -> None:
                        nonlocal tool_started
                        tool_started = True
                        if on_tool_before_execute is None:
                            return
                        try:
                            try:
                                await on_tool_before_execute(name, args, tc.id, iteration + 1)
                            except TypeError:
                                await on_tool_before_execute(name, args)
                        except Exception:
                            logger.exception(
                                f"[{log_id}] tool.progress-hook.error | name={name}"
                            )

                    async def _notify_tool_cancelled() -> None:
                        if not tool_started or on_tool_after_execute is None:
                            return
                        aborted_result = tool_error_result(
                            "Tool execution aborted",
                            error_type="ToolGuardrailError",
                            category="tool_guardrail",
                            metadata={"tool_name": tool_name},
                        )
                        try:
                            try:
                                await on_tool_after_execute(
                                    tool_name,
                                    display_tool_args,
                                    aborted_result,
                                    tc.id,
                                    iteration + 1,
                                    None,
                                    None,
                                    "error",
                                    True,
                                )
                            except TypeError:
                                await on_tool_after_execute(
                                    tool_name,
                                    display_tool_args,
                                    aborted_result,
                                    tc.id,
                                    iteration + 1,
                                )
                        except Exception:
                            logger.exception(
                                f"[{log_id}] tool.cancel-hook.error | name={tool_name}"
                            )

                    if not before_guardrail.allows_execution:
                        logger.warning(
                            f"[{log_id}] tool.guardrail-block | name={tool_name} "
                            f"code={before_guardrail.code} count={before_guardrail.count}"
                        )
                        result = build_toolguard_synthetic_result(before_guardrail)
                    else:
                        try:
                            result = await active_tools.execute(
                                tool_name,
                                tool_args,
                                on_before_execute=_notify_tool_before_execute,
                            )
                            self._raise_if_cancel_requested(should_cancel)
                        except RunCancelledError:
                            await _notify_tool_cancelled()
                            raise
                        executed_tool_calls += 1
                    raw_result_for_repeated_error = result
                    tool_failed = (not before_guardrail.allows_execution) or self._tool_result_looks_like_failure(result)
                    if before_guardrail.allows_execution:
                        after_guardrail = tool_loop_guardrail.after_call(
                            tool_name,
                            display_tool_args,
                            result,
                            failed=tool_failed,
                        )
                        if after_guardrail.action == "warn":
                            logger.warning(
                                f"[{log_id}] tool.guardrail-warning | name={tool_name} "
                                f"code={after_guardrail.code} count={after_guardrail.count}"
                            )
                            result = append_toolguard_guidance(result, after_guardrail)
                            tool_failed = self._tool_result_looks_like_failure(result)
                    if tool_failed:
                        had_tool_error = True
                    evidence = active_tools.build_evidence(
                        tool_name,
                        display_tool_args,
                        result,
                        ok=not tool_failed,
                    )
                    tool_evidence.append(evidence)
                    artifact = build_task_artifact(evidence)
                    if artifact is not None:
                        task_artifacts.append(artifact)
                    if is_verification_tool_name(tool_name):
                        verification_outcome = classify_verification_result(result)
                        verification_attempted = verification_attempted or bool(verification_outcome["attempted"])
                        verification_passed = verification_passed or bool(verification_outcome["ok"])
                    if tool_name == DELEGATE_TOOL_NAME:
                        delegate_task_id, delegate_prompt_type = self._extract_delegate_task_info(result)
                        if delegate_task_id:
                            active_delegate_task_id = delegate_task_id
                        if delegate_prompt_type:
                            active_delegate_prompt_type = delegate_prompt_type
                        if delegate_task_id:
                            delegated_tasks.append(
                                StoredDelegatedTask(
                                    task_id=delegate_task_id,
                                    prompt_type=delegate_prompt_type,
                                    status=WORKFLOW_COMPLETED_STATUS,
                                    selected=True,
                                    updated_at=time.time(),
                                )
                            )
                    if tool_name == CONFIGURE_SKILL_TOOL_NAME and tool_args.get("action") in ("add", "upsert"):
                        used_configure_skill = True
                    logger.info(
                        f"[{log_id}] tool.result | name={tool_name} preview={self.format_log_preview(result, max_chars=200)}"
                    )
                    if on_tool_after_execute is not None:
                        try:
                            try:
                                await on_tool_after_execute(
                                    tool_name,
                                    display_tool_args,
                                    result,
                                    tc.id,
                                    iteration + 1,
                                    active_delegate_task_id if tool_name == DELEGATE_TOOL_NAME else None,
                                    active_delegate_prompt_type if tool_name == DELEGATE_TOOL_NAME else None,
                                    "error" if tool_failed else "completed",
                                )
                            except TypeError:
                                await on_tool_after_execute(tool_name, display_tool_args, result)
                        except Exception:
                            logger.exception(
                                f"[{log_id}] tool.result-hook.error | name={tool_name}"
                            )
                    result_for_context = self._summarize_tool_result_for_context_with_config(tool_name, result)

                    repeated_error_marker = self._classify_tool_result(raw_result_for_repeated_error)
                    if repeated_error_marker is not None:
                        current_error_key = (tool_name, repeated_error_marker)
                        if repeated_tool_error_key == current_error_key:
                            repeated_tool_error_count += 1
                        else:
                            repeated_tool_error_key = current_error_key
                            repeated_tool_error_count = 1

                        if repeated_tool_error_count >= self.REPEATED_TOOL_ERROR_LIMIT:
                            logger.warning(
                                f"[{log_id}] tool.repeated-error | name={tool_name} count={repeated_tool_error_count} stopping_early=true"
                            )
                            content = self._repeated_invalid_tool_call_content(result)
                            await self._emit_response_deltas(
                                content,
                                part_id=response_part_id,
                                on_response_delta=on_response_delta,
                            )
                            return ExecutionResult(
                                content=content,
                                executed_tool_calls=executed_tool_calls,
                                used_configure_skill=used_configure_skill,
                                had_tool_error=had_tool_error,
                                delegated_tasks=tuple(delegated_tasks),
                                active_delegate_task_id=active_delegate_task_id,
                                active_delegate_prompt_type=active_delegate_prompt_type,
                                verification_attempted=verification_attempted,
                                verification_passed=verification_passed,
                                compaction_handoff=latest_compaction_handoff,
                                context_compactions=context_compactions,
                                context_compaction_events=context_compaction_events,
                                llm_step_events=llm_step_events,
                                tool_evidence=tuple(tool_evidence),
                                task_artifacts=tuple(task_artifacts),
                            )
                    else:
                        repeated_tool_error_key = None
                        repeated_tool_error_count = 0

                    tool_results_history.append(f"{tool_name}: {result_for_context[:200]}")
                    chat_messages.append(ChatMessage(
                        role=CHAT_ROLE_TOOL,
                        content=result_for_context,
                        tool_call_id=tc.id,
                    ))

                    if (
                        refresh_system_prompt is not None
                        and self._should_refresh_main_system_after_tool(tool_name, tool_args)
                        and self._tool_result_ok_for_system_refresh(result)
                    ):
                        try:
                            new_system = refresh_system_prompt()
                            if chat_messages and chat_messages[0].role == CHAT_ROLE_SYSTEM:
                                chat_messages[0].content = new_system
                                if allow_tools and active_tools.tool_names:
                                    tools = active_tools.get_definitions()
                                logger.info(
                                    f"[{log_id}] prompt.refresh | after_tool={tool_name} "
                                    "system_rebuilt=true tools_refreshed=true"
                                )
                        except Exception:
                            logger.exception(f"[{log_id}] prompt.refresh.error | after_tool={tool_name}")

                    await self.tool_result_persistence.persist(
                        session_id=tool_result_session_id,
                        tool_name=tool_name,
                        tool_args=display_tool_args,
                        result=result,
                    )

                if self._should_force_final_after_web_sources(task_artifacts, tool_evidence):
                    tools = None
                    chat_messages.append(ChatMessage(
                        role=CHAT_ROLE_SYSTEM,
                        content=(
                            "You already have enough traceable web source evidence for this turn. "
                            "Stop calling tools now and write the final answer from the gathered sources. "
                            "Cite source URLs or domains, and state uncertainty plainly if exact current data is unavailable."
                        ),
                    ))
                    logger.info(
                        f"[{log_id}] llm.force-final-after-web-sources | "
                        f"artifacts={len(task_artifacts)} evidence={len(tool_evidence)}"
                    )

                continue

            if not response.content:
                if not empty_response_retried:
                    empty_response_retried = True
                    logger.warning(
                        f"[{log_id}] llm.empty-visible-response | iter={iteration + 1} retrying_once=true "
                        f"sanitized_from_nonempty={'true' if sanitized_became_empty else 'false'} "
                        f"tool_history_count={len(tool_results_history)}"
                    )
                    chat_messages.append(
                        ChatMessage(
                            role=CHAT_ROLE_SYSTEM,
                            content=(
                                self.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE
                                if sanitized_became_empty
                                else self.EMPTY_RESPONSE_RETRY_MESSAGE
                            ),
                        )
                    )
                    continue

                logger.warning(
                    f"[{log_id}] llm.empty-visible-response | iter={iteration + 1} using_fallback=true "
                    f"sanitized_from_nonempty={'true' if sanitized_became_empty else 'false'} "
                    f"tool_history_count={len(tool_results_history)}"
                )
                content = self.empty_response_fallback
                await self._emit_response_deltas(
                    content,
                    part_id=response_part_id,
                    on_response_delta=on_response_delta,
                )
                return ExecutionResult(
                    content=content,
                    executed_tool_calls=executed_tool_calls,
                    used_configure_skill=used_configure_skill,
                    had_tool_error=had_tool_error,
                    delegated_tasks=tuple(delegated_tasks),
                    active_delegate_task_id=active_delegate_task_id,
                    active_delegate_prompt_type=active_delegate_prompt_type,
                    verification_attempted=verification_attempted,
                    verification_passed=verification_passed,
                    compaction_handoff=latest_compaction_handoff,
                    context_compactions=context_compactions,
                    context_compaction_events=context_compaction_events,
                    llm_step_events=llm_step_events,
                    assistant_internal_only_response=assistant_internal_only_response,
                    tool_evidence=tuple(tool_evidence),
                    task_artifacts=tuple(task_artifacts),
                )

            if response_delta_count == 0:
                await self._emit_response_deltas(
                    response.content,
                    part_id=response_part_id,
                    on_response_delta=on_response_delta,
                )
            return ExecutionResult(
                content=response.content,
                executed_tool_calls=executed_tool_calls,
                used_configure_skill=used_configure_skill,
                had_tool_error=had_tool_error,
                delegated_tasks=tuple(delegated_tasks),
                active_delegate_task_id=active_delegate_task_id,
                active_delegate_prompt_type=active_delegate_prompt_type,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                compaction_handoff=latest_compaction_handoff,
                context_compactions=context_compactions,
                context_compaction_events=context_compaction_events,
                llm_step_events=llm_step_events,
                reasoning_details=response.reasoning_details,
                tool_evidence=tuple(tool_evidence),
                task_artifacts=tuple(task_artifacts),
            )

        stop_metadata = {
            "schema_version": 1,
            "iteration_limit": iteration_limit,
            "executed_tool_calls": executed_tool_calls,
            "tool_result_count": len(tool_results_history),
        }
        logger.warning(
            f"[{log_id}] llm.max-iterations | limit={iteration_limit} "
            f"executed_tool_calls={executed_tool_calls} tool_result_count={len(tool_results_history)}"
        )

        history_msg = self._format_tool_history_for_user(tool_results_history)

        content = (
            f"我嘗試完成你的請求，但超過了最大迭代次數（{iteration_limit}次）。"
            f"請將任務拆分為較小的步驟。{history_msg}"
        )
        await self._emit_response_deltas(
            content,
            part_id=f"assistant:{log_id}:max-iterations",
            on_response_delta=on_response_delta,
        )
        return ExecutionResult(
            content=content,
            executed_tool_calls=executed_tool_calls,
            used_configure_skill=used_configure_skill,
            had_tool_error=had_tool_error,
            delegated_tasks=tuple(delegated_tasks),
            active_delegate_task_id=active_delegate_task_id,
            active_delegate_prompt_type=active_delegate_prompt_type,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
            stop_reason=MAX_TOOL_ITERATIONS_STOP_REASON,
            stop_metadata=stop_metadata,
            compaction_handoff=latest_compaction_handoff,
            context_compactions=context_compactions,
            context_compaction_events=context_compaction_events,
            llm_step_events=llm_step_events,
            tool_evidence=tuple(tool_evidence),
            task_artifacts=tuple(task_artifacts),
        )


# LLM prompt preparation and call orchestration.
PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON = "base-exceeds-budget"
PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON = "first-message-exceeds-budget"
LOG_WHITESPACE_RE = re.compile(r"\s+")


def prompt_trim_base_exceeds_budget_reason() -> str:
    return PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON


def prompt_trim_first_message_exceeds_budget_reason() -> str:
    return PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON


class PromptBudgetService:
    """Estimates prompt token usage and trims history to fit the configured budget."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        provider: LLMProvider,
        tools: ToolRegistry,
        history_token_budget_getter: Callable[[], int],
        context_window_tokens_getter: Callable[[], int | None],
        output_token_reserve_getter: Callable[[], int],
    ):
        self.context_builder = context_builder
        self.provider = provider
        self.tools = tools
        self._history_token_budget_getter = history_token_budget_getter
        self._context_window_tokens_getter = context_window_tokens_getter
        self._output_token_reserve_getter = output_token_reserve_getter

    def effective_context_token_budget(self) -> int:
        """Return the prompt token budget after applying model window and output reserve."""
        history_budget = max(0, self._history_token_budget_getter())
        context_window_tokens = self._context_window_tokens_getter()
        if context_window_tokens is None:
            return history_budget

        output_reserve = max(0, self._output_token_reserve_getter())
        model_input_budget = max(1, context_window_tokens - output_reserve)
        if history_budget <= 0:
            return model_input_budget
        return min(history_budget, model_input_budget)

    def estimate_tool_schema_tokens(self, *, allow_tools: bool, tool_registry: ToolRegistry | None = None) -> int:
        """Estimate token cost of tool schemas sent with the request."""
        if not allow_tools:
            return 0

        active_tools = tool_registry or self.tools
        if not active_tools.tool_names:
            return 0

        try:
            tool_schema_text = json.dumps(active_tools.get_definitions(), ensure_ascii=False, sort_keys=True)
        except Exception:
            return 0

        return count_text_tokens(tool_schema_text, model=self.provider.get_default_model())

    def trim_history_to_token_budget(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        channel: str | None,
        session_id: str,
        tool_schema_tokens: int = 0,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Trim oldest history messages when the prompt would exceed the history token budget."""
        budget = self.effective_context_token_budget()
        model = self.provider.get_default_model()
        base_messages = self.context_builder.build_messages(
            history=[],
            current_message=current_message,
            current_images=None,
            channel=channel,
            session_id=session_id,
        )
        base_tokens = count_messages_tokens(base_messages, model=model) + tool_schema_tokens
        if budget <= 0 or not history:
            history_tokens = count_messages_tokens(history, model=model) if history else 0
            return history, base_tokens, history_tokens, base_tokens + history_tokens

        if base_tokens >= budget:
            reason = prompt_trim_base_exceeds_budget_reason()
            logger.warning(
                f"[{session_id}] prompt.trim | base_tokens={base_tokens} budget={budget} history_retained=0 reason={reason}"
            )
            return [], base_tokens, 0, base_tokens

        trimmed_reversed: list[dict[str, Any]] = []
        running_tokens = base_tokens
        retained_history_tokens = 0
        for message in reversed(history):
            message_tokens = count_messages_tokens([message], model=model)
            if trimmed_reversed and running_tokens + message_tokens > budget:
                break
            if not trimmed_reversed and running_tokens + message_tokens > budget:
                reason = prompt_trim_first_message_exceeds_budget_reason()
                logger.warning(
                    f"[{session_id}] prompt.trim | base_tokens={base_tokens} first_history_tokens={message_tokens} budget={budget} history_retained=0 reason={reason}"
                )
                return [], base_tokens, 0, base_tokens
            trimmed_reversed.append(message)
            running_tokens += message_tokens
            retained_history_tokens += message_tokens

        trimmed_history = list(reversed(trimmed_reversed))
        if len(trimmed_history) != len(history):
            logger.info(
                f"[{session_id}] prompt.trim | budget={budget} base_tokens={base_tokens} history_before={len(history)} history_after={len(trimmed_history)} estimated_tokens={running_tokens}"
            )
        return trimmed_history, base_tokens, retained_history_tokens, running_tokens


class PromptLoggingService:
    """Handles prompt log files and compact log previews for agent diagnostics."""

    def __init__(self, *, log_config: LogConfig, app_home_getter: Callable[[], Path | None]):
        self.log_config = log_config
        self._app_home_getter = app_home_getter

    @staticmethod
    def sanitize_log_filename(value: str) -> str:
        """Sanitize a string for use in per-prompt log filenames."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
        return cleaned[:80] or "prompt"

    def get_system_prompt_log_path(self, log_id: str) -> Path:
        """Return a unique file path for one full system prompt log entry."""
        logs_root = (self._app_home_getter() or Path.home() / ".opensprite") / "logs" / "system-prompts"
        if ":subagent:" in log_id:
            logs_root = logs_root / "subagents"
        dated_root = logs_root / time.strftime("%Y-%m-%d")
        dated_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%H-%M-%S")
        safe_log_id = self.sanitize_log_filename(log_id)
        filename_base = f"{timestamp}_{safe_log_id}_{time.time_ns()}"
        candidate = dated_root / f"{filename_base}.md"
        counter = 1
        while candidate.exists():
            candidate = dated_root / f"{filename_base}_{counter}.md"
            counter += 1
        return candidate

    def write_full_system_prompt_log(self, log_id: str, content: str) -> None:
        """Write the full system prompt to a dedicated per-prompt log file."""
        try:
            log_path = self.get_system_prompt_log_path(log_id)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            entry = (
                f"[{timestamp}] [{log_id}] prompt.system.begin\n"
                f"{content}\n"
                f"[{timestamp}] [{log_id}] prompt.system.end\n"
            )
            with log_path.open("w", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"[{log_id}] prompt.file.error | error={e}")

    @staticmethod
    def sanitize_response_content(content: str) -> str:
        """Remove provider-internal control blocks from visible replies."""
        return sanitize_assistant_visible_text(content)

    @staticmethod
    def format_log_preview(
        content: str | list[dict[str, Any]] | None,
        max_chars: int = 160,
        *,
        strip_internal: bool = True,
    ) -> str:
        """Build a compact, single-line preview for logs."""
        if isinstance(content, list):
            text_parts: list[str] = []
            image_count = 0
            other_items = 0
            for item in content:
                if not isinstance(item, dict):
                    other_items += 1
                    continue
                item_type = item.get("type")
                if item_type == CHAT_CONTENT_TYPE_TEXT:
                    text_parts.append(str(item.get("text", "")))
                elif item_type == CHAT_CONTENT_TYPE_IMAGE_URL:
                    image_count += 1
                else:
                    other_items += 1

            text = " ".join(part for part in text_parts if part)
            if strip_internal:
                text = strip_assistant_internal_scaffolding(text)
            text = LOG_WHITESPACE_RE.sub(" ", text).strip() or "<multimodal>"
            suffix_parts = []
            if image_count:
                suffix_parts.append(f"images={image_count}")
            if other_items:
                suffix_parts.append(f"items={other_items}")
            if suffix_parts:
                text = f"{text} [{' '.join(suffix_parts)}]"
        else:
            text = str(content or "")
            if strip_internal:
                text = strip_assistant_internal_scaffolding(text)
            text = LOG_WHITESPACE_RE.sub(" ", text).strip()

        if not text:
            return "<empty>"
        text = redact_log_preview(text)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    @staticmethod
    def summarize_messages(messages: list[Any], tail: int = 4) -> str:
        """Build a compact summary of the trailing chat messages for diagnostics."""
        summary = []
        for msg in messages[-tail:]:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content_kind = f"list[{len(content)}]"
            else:
                content_kind = f"str[{len(content or '')}]"
            tool_id = "y" if getattr(msg, "tool_call_id", None) else "n"
            tool_calls = len(getattr(msg, "tool_calls", None) or [])
            summary.append(
                f"{getattr(msg, 'role', '?')}({content_kind},tool_id={tool_id},tool_calls={tool_calls})"
            )
        return ", ".join(summary) if summary else "<empty>"

    @staticmethod
    def extract_available_subagents(system_prompt: str) -> list[str]:
        """Parse the Available Subagents section from a rendered system prompt."""
        in_section = False
        subagents: list[str] = []

        for raw_line in system_prompt.splitlines():
            line = raw_line.strip()
            if not in_section:
                if line in {"# Available Subagents", "## Available Subagents"}:
                    in_section = True
                continue

            if not line:
                continue
            if line == "---" or line.startswith("#"):
                break
            if not line.startswith("- `"):
                continue

            end_tick = line.find("`", 3)
            if end_tick <= 3:
                continue
            subagents.append(line[3:end_tick])

        return subagents

    def log_prepared_messages(self, log_id: str, messages: list[dict[str, Any]]) -> None:
        """Log prepared prompt/messages when prompt logging is enabled."""
        if not self.log_config.log_system_prompt:
            return

        try:
            system_msg = next((m for m in messages if m.get("role") == "system"), None)
            if system_msg:
                system_prompt = str(system_msg.get("content", ""))
                self.write_full_system_prompt_log(log_id, system_prompt)
                max_chars = 240
                if self.log_config.log_system_prompt_lines > 0:
                    max_chars = max(120, self.log_config.log_system_prompt_lines * 120)
                logger.info(
                    f"[{log_id}] prompt.system | {self.format_log_preview(system_prompt, max_chars=max_chars)}"
                )
                if ":subagent:" not in log_id:
                    available_subagents = self.extract_available_subagents(system_prompt)
                    names = ", ".join(available_subagents) if available_subagents else "<none>"
                    logger.info(
                        f"[{log_id}] prompt.subagents | count={len(available_subagents)} names={names}"
                    )

            for index, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                if role == "system":
                    continue
                preview = self.format_log_preview(msg.get("content", ""))
                logger.info(
                    f"[{log_id}] prompt.message[{index}] | role={role} preview={preview}"
                )
        except Exception as e:
            logger.error(f"[{log_id}] prompt.log.error | error={e}")


class LlmCallService:
    """Builds the prompt for one LLM call and delegates to the execution engine."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        maybe_seed_active_task: Callable[..., Awaitable[None]],
        load_history: Callable[[str], Awaitable[list[Any]]],
        get_current_audios: Callable[[], list[str] | None],
        get_current_videos: Callable[[], list[str] | None],
        augment_message_for_media: Callable[..., str],
        estimate_tool_schema_tokens: Callable[..., int],
        trim_history_to_token_budget: Callable[..., tuple[list[dict[str, Any]], int, int, int]],
        effective_context_token_budget: Callable[[], int],
        llm_context_window_tokens: Callable[[], int | None],
        llm_output_reserve_tokens: Callable[[], int],
        sync_runtime_mcp_tools_context: Callable[[], None],
        build_messages: Callable[..., list[dict[str, Any]]],
        build_system_prompt: Callable[[str], str],
        log_prepared_messages: Callable[[str, list[dict[str, Any]]], None],
        get_work_state_summary: Callable[[str], Awaitable[str]],
        read_active_task_snapshot: Callable[[str], str],
        resolve_task_context: Callable[..., Awaitable[TaskContextDecision]],
        resolve_task_objective: Callable[..., Awaitable[TaskObjectiveDecision]],
        plan_task: Callable[..., Awaitable[TaskContract]],
        select_harness_profile: Callable[[TaskContract], HarnessProfile],
        select_harness_policy: Callable[[HarnessProfile], HarnessPolicy],
        build_harness_tool_registry: Callable[[ToolRegistry, HarnessProfile, HarnessPolicy], ToolRegistry],
        emit_run_event: Callable[..., Awaitable[None]],
        build_proactive_retrieval_context: Callable[..., Awaitable[str]],
        get_tool_registry: Callable[[], ToolRegistry],
        get_current_run_id: Callable[[], str | None],
        should_cancel_run: Callable[[str, str | None], bool],
        make_tool_progress_hook: Callable[..., Callable[[str, dict[str, Any]], Awaitable[None]] | None],
        make_tool_result_hook: Callable[..., Callable[[str, dict[str, Any], str], Awaitable[None]] | None],
        make_llm_status_hook: Callable[..., Callable[[Any], Awaitable[None]] | None],
        make_llm_delta_hook: Callable[..., Callable[[str, str, str, int], Awaitable[None]] | None],
        make_tool_input_delta_hook: Callable[..., Callable[[str, str, str, int], Awaitable[None]] | None],
        make_reasoning_delta_hook: Callable[..., Callable[[str, int], Awaitable[None]] | None],
        execute_messages: Callable[..., Awaitable[ExecutionResult]],
    ):
        self.config = config
        self._maybe_seed_active_task = maybe_seed_active_task
        self._load_history = load_history
        self._get_current_audios = get_current_audios
        self._get_current_videos = get_current_videos
        self._augment_message_for_media = augment_message_for_media
        self._estimate_tool_schema_tokens = estimate_tool_schema_tokens
        self._trim_history_to_token_budget = trim_history_to_token_budget
        self._effective_context_token_budget = effective_context_token_budget
        self._llm_context_window_tokens = llm_context_window_tokens
        self._llm_output_reserve_tokens = llm_output_reserve_tokens
        self._sync_runtime_mcp_tools_context = sync_runtime_mcp_tools_context
        self._build_messages = build_messages
        self._build_system_prompt = build_system_prompt
        self._log_prepared_messages = log_prepared_messages
        self._get_work_state_summary = get_work_state_summary
        self._read_active_task_snapshot = read_active_task_snapshot
        self._resolve_task_context = resolve_task_context
        self._resolve_task_objective = resolve_task_objective
        self._plan_task = plan_task
        self._select_harness_profile = select_harness_profile
        self._select_harness_policy = select_harness_policy
        self._build_harness_tool_registry = build_harness_tool_registry
        self._emit_run_event = emit_run_event
        self._build_proactive_retrieval_context = build_proactive_retrieval_context
        self._get_tool_registry = get_tool_registry
        self._get_current_run_id = get_current_run_id
        self._should_cancel_run = should_cancel_run
        self._make_tool_progress_hook = make_tool_progress_hook
        self._make_tool_result_hook = make_tool_result_hook
        self._make_llm_status_hook = make_llm_status_hook
        self._make_llm_delta_hook = make_llm_delta_hook
        self._make_tool_input_delta_hook = make_tool_input_delta_hook
        self._make_reasoning_delta_hook = make_reasoning_delta_hook
        self._execute_messages = execute_messages

    async def call_llm(
        self,
        session_id: str,
        current_message: str,
        channel: str | None = None,
        allow_tools: bool = True,
        user_images: list[str] | None = None,
        user_image_files: list[str] | None = None,
        user_audio_files: list[str] | None = None,
        user_video_files: list[str] | None = None,
        *,
        external_chat_id: str | None = None,
        emit_tool_progress: bool = False,
        task_intent: TaskIntent | None = None,
        task_contract_override: TaskContract | None = None,
    ) -> ExecutionResult:
        """Prepare prompt messages and run the LLM/tool execution loop."""
        run_id = self._get_current_run_id()
        logger.info(f"[{session_id}] history.load | requested=true")
        history_messages = await self._load_history(session_id)
        loaded_history_count = len(history_messages)

        # Tool results are only valid inside the turn where they were produced.
        filtered = []
        for m in history_messages:
            role = m.get("role", "?") if isinstance(m, dict) else getattr(m, "role", "?")
            if role != CHAT_ROLE_TOOL:
                filtered.append(m)
        history_messages = filtered
        filtered_tool_messages = loaded_history_count - len(history_messages)

        # The current user message is already passed explicitly to the context builder.
        # Drop the newest persisted user message for this turn to avoid duplicate/blank user entries.
        if history_messages:
            latest = history_messages[-1]
            latest_role = latest.get("role", "?") if isinstance(latest, dict) else getattr(latest, "role", "?")
            latest_content = latest.get("content", "") if isinstance(latest, dict) else getattr(latest, "content", "")
            if latest_role == CHAT_ROLE_USER and latest_content == current_message:
                history_messages = history_messages[:-1]

        history_dicts = []
        for m in history_messages:
            if isinstance(m, dict):
                msg = {"role": m.get("role", "?"), "content": m.get("content", "")}
                if m.get("tool_call_id"):
                    msg["tool_call_id"] = m["tool_call_id"]
                if m.get("reasoning_details"):
                    msg["reasoning_details"] = m["reasoning_details"]
            else:
                msg = {"role": m.role, "content": m.content}
                if getattr(m, "tool_call_id", None):
                    msg["tool_call_id"] = m.tool_call_id
                if getattr(m, "reasoning_details", None):
                    msg["reasoning_details"] = m.reasoning_details
            history_dicts.append(msg)

        logger.info(
            f"[{session_id}] prompt.build | history={len(history_dicts)} channel={channel or '-'} images={len(user_images or [])}"
        )
        if run_id is not None:
            await self._emit_run_event(
                session_id,
                run_id,
                HISTORY_LOADED_EVENT,
                {
                    "loaded_messages": loaded_history_count,
                    "history_messages": len(history_dicts),
                    "filtered_tool_messages": filtered_tool_messages,
                },
                channel=channel,
                external_chat_id=external_chat_id,
            )
        work_state_summary = await self._get_work_state_summary(session_id)
        active_task_snapshot = self._read_active_task_snapshot(session_id)
        if run_id is not None:
            await self._emit_run_event(
                session_id,
                run_id,
                PROMPT_BUILT_EVENT,
                {
                    "history_messages": len(history_dicts),
                    "current_message_len": len(str(current_message or "")),
                    "images": len(user_images or []),
                    "audio_files": len(user_audio_files or []),
                    "video_files": len(user_video_files or []),
                    "has_work_state_summary": bool(work_state_summary),
                    "has_active_task_snapshot": bool(active_task_snapshot),
                },
                channel=channel,
                external_chat_id=external_chat_id,
            )
        task_context_decision = None
        task_objective_decision = None
        if task_intent is not None:
            task_context_decision = await self._resolve_task_context(
                current_message=current_message,
                history=history_dicts,
                task_intent=task_intent,
                active_task=active_task_snapshot,
                work_state_summary=work_state_summary,
            )
            logger.info(
                f"[{session_id}] task.context | method={task_context_decision.method} "
                f"follow_up={task_context_decision.is_follow_up} "
                f"inherit_active={task_context_decision.should_inherit_active_task} "
                f"replace_active={task_context_decision.should_replace_active_task} "
                f"tool_group={task_context_decision.inherited_tool_group or '-'} "
                f"confidence={task_context_decision.confidence:.2f}"
            )
            if run_id is not None:
                await self._emit_run_event(
                    session_id,
                    run_id,
                    TASK_CONTEXT_RESOLVED_EVENT,
                    task_context_decision.to_metadata(),
                    channel=channel,
                    external_chat_id=external_chat_id,
                )
            task_objective_decision = await self._resolve_task_objective(
                current_message=current_message,
                history=history_dicts,
                task_intent=task_intent,
                task_context_decision=task_context_decision,
                active_task=active_task_snapshot,
                work_state_summary=work_state_summary,
            )
            logger.info(
                f"[{session_id}] task.objective | method={task_objective_decision.method} "
                f"use_resolved={task_objective_decision.should_use_resolved_objective} "
                f"confidence={task_objective_decision.confidence:.2f}"
            )
            if run_id is not None:
                await self._emit_run_event(
                    session_id,
                    run_id,
                    TASK_OBJECTIVE_RESOLVED_EVENT,
                    task_objective_decision.to_metadata(),
                    channel=channel,
                    external_chat_id=external_chat_id,
                )
        effective_task_intent = _effective_task_intent(task_intent, task_objective_decision)
        effective_current_message = _message_with_resolved_objective(current_message, task_objective_decision)
        if (
            work_state_summary
            and effective_task_intent is not None
            and effective_task_intent.objective.strip() != str(effective_current_message or "").strip()
        ):
            effective_current_message = (
                f"{effective_current_message}\n\n"
                "Use the existing structured work state below as the source of truth for continuing the task.\n"
                f"{work_state_summary}"
            )
        current_audios = self._get_current_audios()
        current_videos = self._get_current_videos()
        prompt_message = self._augment_message_for_media(
            effective_current_message,
            user_images,
            current_audios,
            current_videos,
            user_image_files=user_image_files,
            user_audio_files=user_audio_files,
            user_video_files=user_video_files,
        )
        task_contract = None
        harness_policy = None
        harness_tool_registry = None
        base_tool_registry = self._get_tool_registry()
        if effective_task_intent is not None:
            if task_contract_override is not None:
                task_contract = task_contract_override
            else:
                if run_id is not None:
                    await self._emit_run_event(
                        session_id,
                        run_id,
                        TASK_CONTRACT_PLANNING_STARTED_EVENT,
                        {
                            "schema_version": 1,
                            "objective": effective_task_intent.objective,
                            "task_kind": effective_task_intent.kind,
                            "history_messages": len(history_dicts),
                        },
                        channel=channel,
                        external_chat_id=external_chat_id,
                    )
                task_contract = await self._plan_task(
                    tool_registry=base_tool_registry,
                    fallback_objective=getattr(effective_task_intent, "objective", ""),
                    current_message=prompt_message,
                    history=history_dicts,
                    current_image_files=user_image_files,
                    current_audio_files=user_audio_files,
                    current_video_files=user_video_files,
                    task_context_decision=task_context_decision,
                )
                if run_id is not None:
                    await self._emit_run_event(
                        session_id,
                        run_id,
                        TASK_CONTRACT_PLANNED_EVENT,
                        task_contract.to_metadata(),
                        channel=channel,
                        external_chat_id=external_chat_id,
                    )
                    validation_event_type = (
                        TASK_CONTRACT_VALIDATED_EVENT
                        if task_planner_status(task_contract) == PLANNER_VALIDATED_STATUS
                        else TASK_CONTRACT_VALIDATION_FAILED_EVENT
                    )
                    await self._emit_run_event(
                        session_id,
                        run_id,
                        validation_event_type,
                        task_contract.to_metadata(),
                        channel=channel,
                        external_chat_id=external_chat_id,
                    )
            if run_id is not None and task_contract_override is not None:
                await self._emit_run_event(
                    session_id,
                    run_id,
                    TASK_CONTRACT_PLANNED_EVENT,
                    task_contract.to_metadata(),
                    channel=channel,
                    external_chat_id=external_chat_id,
                )
                await self._emit_run_event(
                    session_id,
                    run_id,
                    TASK_CONTRACT_VALIDATED_EVENT,
                    task_contract.to_metadata(),
                    channel=channel,
                    external_chat_id=external_chat_id,
                )
            harness_profile = self._select_harness_profile(task_contract)
            task_contract = replace(task_contract, harness_profile=harness_profile.to_metadata())
            harness_policy = self._select_harness_policy(harness_profile)
            harness_tool_registry = self._build_harness_tool_registry(base_tool_registry, harness_profile, harness_policy)
            if _should_seed_active_task_for_contract(
                active_task_snapshot=active_task_snapshot,
                harness_profile=harness_profile,
                task_context_decision=task_context_decision,
            ):
                await self._maybe_seed_active_task(
                    session_id,
                    current_message,
                    task_intent=effective_task_intent,
                    task_context_decision=task_context_decision,
                    task_objective_decision=task_objective_decision,
                )
            if run_id is not None:
                await self._emit_run_event(
                    session_id,
                    run_id,
                    HARNESS_PROFILE_SELECTED_EVENT,
                    {
                        **harness_profile.to_metadata(),
                        "selection_phase": "contract",
                    },
                    channel=channel,
                    external_chat_id=external_chat_id,
                )
                await self._emit_run_event(
                    session_id,
                    run_id,
                    TASK_CONTRACT_CREATED_EVENT,
                    task_contract.to_metadata(),
                    channel=channel,
                    external_chat_id=external_chat_id,
                )
                await self._emit_run_event(
                    session_id,
                    run_id,
                    HARNESS_POLICY_SELECTED_EVENT,
                    harness_policy.to_metadata(),
                    channel=channel,
                    external_chat_id=external_chat_id,
                )
                policy_resolution = getattr(harness_tool_registry, "permission_resolution_metadata", None)
                if isinstance(policy_resolution, dict) and policy_resolution:
                    await self._emit_run_event(
                        session_id,
                        run_id,
                        HARNESS_POLICY_MERGE_RESOLVED_EVENT,
                        policy_resolution,
                        channel=channel,
                        external_chat_id=external_chat_id,
                    )
            guidance = _build_task_contract_guidance(task_contract)
            if guidance:
                prompt_message = f"{prompt_message}\n\n{guidance}"
            logger.info(
                f"[{session_id}] task.contract | type={task_contract.task_type} "
                f"requirements={len(task_contract.requirements)} resources={len(task_contract.selected_resources)} "
                f"acceptance_criteria={len(task_contract.acceptance_criteria)} "
                f"allow_no_tool_final={task_contract.allow_no_tool_final}"
            )
        planning_mode = resolve_planning_mode(
            base_registry=harness_tool_registry or base_tool_registry,
            task_contract=task_contract,
        )
        selected_tool_registry = planning_mode.tool_registry or harness_tool_registry
        if (
            not planning_mode.enabled
            and task_contract is not None
            and _should_answer_contract_without_tools(task_contract)
        ):
            selected_tool_registry = ToolRegistry(
                permission_policy=(harness_tool_registry or base_tool_registry).permission_policy
            )
        if planning_mode.enabled and selected_tool_registry is not None:
            logger.info(
                f"[{session_id}] prompt.mode | planning_mode=true allowed_tools={','.join(selected_tool_registry.tool_names)}"
            )
        if run_id is not None:
            await self._emit_run_event(
                session_id,
                run_id,
                PLANNING_MODE_SELECTED_EVENT,
                {
                    "enabled": bool(planning_mode.enabled),
                    "tool_names": list(selected_tool_registry.tool_names) if selected_tool_registry is not None else [],
                },
                channel=channel,
                external_chat_id=external_chat_id,
            )
        tool_schema_tokens = self._estimate_tool_schema_tokens(
            allow_tools=allow_tools,
            tool_registry=selected_tool_registry,
        )
        history_dicts, base_tokens, history_tokens, final_tokens = self._trim_history_to_token_budget(
            history=history_dicts,
            current_message=prompt_message,
            channel=channel,
            session_id=session_id,
            tool_schema_tokens=tool_schema_tokens,
        )
        structured_retrieval_decision = _structured_retrieval_decision(task_context_decision)
        should_retrieve = bool(structured_retrieval_decision)
        proactive_retrieval_context = await self._build_proactive_retrieval_context(
            session_id=session_id,
            current_message=effective_current_message,
            should_retrieve=should_retrieve,
        )
        if run_id is not None:
            await self._emit_run_event(
                session_id,
                run_id,
                RETRIEVAL_PROACTIVE_CHECKED_EVENT,
                {
                    "should_retrieve": should_retrieve,
                    "applied": bool(proactive_retrieval_context),
                    "context_len": len(proactive_retrieval_context or ""),
                    "decision_source": "task_context" if structured_retrieval_decision is not None else "none",
                },
                channel=channel,
                external_chat_id=external_chat_id,
            )
        if proactive_retrieval_context:
            history_dicts = [{"role": CHAT_ROLE_SYSTEM, "content": proactive_retrieval_context}, *history_dicts]
        effective_context_budget = self._effective_context_token_budget()
        logger.info(
            f"[{session_id}] prompt.tokens | budget={effective_context_budget} "
            f"history_budget={self.config.history_token_budget} model_window={self._llm_context_window_tokens() or '-'} "
            f"output_reserve={self._llm_output_reserve_tokens()} base={base_tokens} tools={tool_schema_tokens} "
            f"history={history_tokens} final_estimated={final_tokens}"
        )
        if run_id is not None:
            await self._emit_run_event(
                session_id,
                run_id,
                PROMPT_TOKENS_ESTIMATED_EVENT,
                {
                    "budget": effective_context_budget,
                    "history_budget": self.config.history_token_budget,
                    "model_window": self._llm_context_window_tokens(),
                    "output_reserve": self._llm_output_reserve_tokens(),
                    "base_tokens": base_tokens,
                    "tool_schema_tokens": tool_schema_tokens,
                    "history_tokens": history_tokens,
                    "final_estimated_tokens": final_tokens,
                },
                channel=channel,
                external_chat_id=external_chat_id,
        )
        self._sync_runtime_mcp_tools_context()
        if run_id is not None:
            tool_names = list(selected_tool_registry.tool_names) if selected_tool_registry is not None else []
            mcp_tool_names = list_mcp_tool_names(tool_names)
            await self._emit_run_event(
                session_id,
                run_id,
                MCP_TOOLS_SYNCED_EVENT,
                {"tool_names": mcp_tool_names, "tool_count": len(mcp_tool_names)},
                channel=channel,
                external_chat_id=external_chat_id,
            )
        full_messages = self._build_messages(
            history=history_dicts,
            current_message=prompt_message,
            current_images=None,
            channel=channel,
            session_id=session_id,
        )

        chat_messages = []
        for m in full_messages:
            msg = ChatMessage(role=m["role"], content=m.get("content", ""))
            if m.get("tool_call_id"):
                msg.tool_call_id = m["tool_call_id"]
            if m.get("tool_calls"):
                msg.tool_calls = m["tool_calls"]
            if m.get("reasoning_details"):
                msg.reasoning_details = m["reasoning_details"]
            chat_messages.append(msg)

        self._log_prepared_messages(session_id, full_messages)
        on_tool_before_execute = self._make_tool_progress_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        on_tool_after_execute = self._make_tool_result_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        on_llm_status = self._make_llm_status_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        on_response_delta = self._make_llm_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        on_tool_input_delta = self._make_tool_input_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        reasoning_delta_count = 0

        reasoning_hook = self._make_reasoning_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )

        async def on_reasoning_delta(delta: str) -> None:
            nonlocal reasoning_delta_count
            reasoning_delta_count += 1
            if reasoning_hook is not None:
                await reasoning_hook(delta, reasoning_delta_count)
        execute_kwargs = {
            "allow_tools": allow_tools,
            "tool_result_session_id": session_id if allow_tools else None,
            "tool_registry": selected_tool_registry,
            "on_tool_before_execute": on_tool_before_execute,
            "on_llm_status": on_llm_status,
            "on_response_delta": on_response_delta,
            "on_tool_input_delta": on_tool_input_delta,
            "on_reasoning_delta": on_reasoning_delta if reasoning_hook is not None else None,
            "refresh_system_prompt": lambda: self._build_system_prompt(session_id),
            "should_cancel": lambda: self._should_cancel_run(session_id, run_id),
            "work_state_summary": work_state_summary,
        }
        if on_tool_after_execute is not None:
            execute_kwargs["on_tool_after_execute"] = on_tool_after_execute
        result = await self._execute_messages(session_id, chat_messages, **execute_kwargs)
        result.task_contract = task_contract
        result.harness_policy = harness_policy.to_metadata() if harness_policy is not None else None
        return result


def _structured_retrieval_decision(task_context_decision: TaskContextDecision | None) -> bool | None:
    if task_context_decision is None:
        return None
    inherited_tool_group = str(task_context_decision.inherited_tool_group or "").strip()
    inherited_task_type = str(task_context_decision.inherited_task_type or "").strip()
    return (
        inherited_tool_group == HISTORY_RETRIEVAL_TOOL_GROUP
        or inherited_task_type == HISTORY_RETRIEVAL_TASK_TYPE
    )


def _should_seed_active_task_for_contract(
    *,
    active_task_snapshot: str,
    harness_profile: HarnessProfile,
    task_context_decision: TaskContextDecision | None,
) -> bool:
    profile_name = str(getattr(harness_profile, "name", "") or "").strip()
    if not is_chat_profile_name(profile_name):
        return True
    if has_current_active_task(active_task_snapshot):
        return True
    if task_context_decision is None:
        return False
    return bool(task_context_decision.should_seed_active_task or task_context_decision.should_replace_active_task)


def _effective_task_intent(
    task_intent: TaskIntent | None,
    task_objective_decision: TaskObjectiveDecision | None,
) -> TaskIntent | None:
    if task_intent is None:
        return None
    if not (task_objective_decision and task_objective_decision.should_use_resolved_objective):
        return task_intent
    resolved_objective = str(task_objective_decision.resolved_objective or "").strip()
    if not resolved_objective:
        return task_intent
    return replace(task_intent, objective=resolved_objective)


def _message_with_resolved_objective(
    current_message: str,
    task_objective_decision: TaskObjectiveDecision | None,
) -> str:
    if not (task_objective_decision and task_objective_decision.should_use_resolved_objective):
        return current_message
    resolved_objective = str(task_objective_decision.resolved_objective or "").strip()
    original_message = str(current_message or "").strip()
    if not resolved_objective or resolved_objective.lower() == original_message.lower():
        return current_message
    return (
        f"{current_message}\n\n"
        f"Resolved task objective: {resolved_objective}\n"
        "Use the resolved objective as the concrete task for this turn while preserving the original user wording above."
    )


def _build_task_contract_guidance(contract: TaskContract) -> str:
    if _should_answer_contract_without_tools(contract):
        return "\n".join([
            "## Runtime Task Contract",
            f"- Task type: {contract.task_type}",
            "- No tool evidence is required for this turn. Answer directly from general knowledge.",
            "- Do not call tools just to prepare a generic answer.",
        ])
    if not (contract.requirements or contract.acceptance_criteria or contract.selected_resources):
        return ""
    lines = [
        "## Runtime Task Contract",
        "Satisfy these runtime completion requirements before giving the final answer.",
        f"- Task type: {contract.task_type}",
    ]
    if contract.selected_resources:
        lines.append("- Required resources:")
        for resource in contract.selected_resources[:12]:
            label = f"{resource.id} ({resource.kind}, {resource.source})"
            if resource.path:
                label += f" path={resource.path}"
            lines.append(f"  - {label}")
        if len(contract.selected_resources) > 12:
            lines.append(f"  - ... {len(contract.selected_resources) - 12} more resource(s)")
    if contract.requirements:
        lines.append("- Required evidence:")
        for requirement in contract.requirements:
            detail = requirement.description or requirement.kind
            qualifiers = []
            if requirement.tool_group:
                qualifiers.append(f"tool_group={requirement.tool_group}")
            if requirement.coverage:
                qualifiers.append(f"coverage={requirement.coverage}")
            qualifiers.append(f"min_count={requirement.min_count}")
            lines.append(f"  - {detail} ({', '.join(qualifiers)})")
    if contract.acceptance_criteria:
        lines.append("- Final answer acceptance criteria:")
        for criterion in contract.acceptance_criteria:
            lines.append(f"  - {_format_acceptance_criterion(criterion)}")
    lines.extend([
        "- If a requirement cannot be satisfied, state the blocker clearly instead of claiming completion.",
        "- Do not answer with only an acknowledgement, plan, or promise of future work when tool evidence or artifacts are required.",
    ])
    return "\n".join(lines)


def _should_answer_contract_without_tools(contract: TaskContract) -> bool:
    return (
        bool(contract.allow_no_tool_final)
        and not contract.requirements
        and not contract.acceptance_criteria
        and not contract.selected_resources
    )


def _format_acceptance_criterion(criterion: Any) -> str:
    if is_itemized_output_criterion(criterion):
        return f"Provide at least {max(1, int(criterion.min_count or 1))} itemized result entries; do not answer with only a plan or acknowledgement."
    if is_substantive_final_answer_criterion(criterion):
        min_chars = max(1, int(getattr(criterion, "min_response_chars", 0) or 1))
        return f"Write a substantive final answer using the inspected media/tool results (minimum {min_chars} visible characters)."
    if is_source_artifact_criterion(criterion):
        return f"Produce at least {max(1, int(criterion.min_count or 1))} traceable source(s) from web/source tools before finalizing."
    if is_source_detail_criterion(criterion):
        return "Fetch or inspect at least one source page before finalizing; search result snippets alone are not sufficient."
    if is_source_reference_criterion(criterion):
        return "Reference at least one gathered source by URL, domain, or title in the final answer."
    if is_workspace_location_criterion(criterion):
        return (
            "Identify the relevant workspace file path, symbol, or configuration location in the final answer, "
            "using only names and locations shown by workspace tool output; verify uncertain symbol names before citing them."
        )
    if is_media_artifact_criterion(criterion):
        return "Produce the required media artifact before finalizing."
    if is_verification_or_gap_criterion(criterion):
        return "After code changes, run focused verification when possible; if not possible, state the verification gap explicitly."
    if is_operation_report_criterion(criterion):
        return "Report approval, validation, rollback, blocker, or residual risk for the operation."
    return criterion.description or criterion.kind
