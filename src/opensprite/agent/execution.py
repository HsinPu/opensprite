"""Shared execution loop for agent and subagent message runs."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from ..config import AgentConfig, DEFAULT_CONTEXT_OVERFLOW_ERROR_MARKERS, DocumentLlmConfig, LogConfig, ToolsConfig
from ..config.llm_presets import provider_profile_defaults
from ..context.runtime import build_runtime_context
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
from ..llms.registry import create_llm
from ..llms.routed import ModelRoutedProvider
from ..llms.runtime_provider import create_llm_from_runtime, resolve_provider_runtime
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
    SUBAGENT_CANCELLED_EVENT,
    SUBAGENT_COMPLETED_EVENT,
    SUBAGENT_FAILED_EVENT,
    SUBAGENT_GROUP_CANCELLED_EVENT,
    SUBAGENT_GROUP_COMPLETED_EVENT,
    SUBAGENT_GROUP_FAILED_EVENT,
    SUBAGENT_GROUP_STARTED_EVENT,
    SUBAGENT_STARTED_EVENT,
    WORKFLOW_COMPLETED_EVENT,
    WORKFLOW_FAILED_EVENT,
    WORKFLOW_STARTED_EVENT,
    WORKFLOW_STEP_COMPLETED_EVENT,
    WORKFLOW_STEP_FAILED_EVENT,
    WORKFLOW_STEP_STARTED_EVENT,
)
from ..context.builder import ContextBuilder
from ..runs.lifecycle import RUN_STARTED_EVENT
from ..skills import SkillsLoader
from ..documents.active_task import has_current_active_task
from ..storage import StorageProvider, StoredMessage
from ..storage.base import StoredDelegatedTask
from ..tool_names import (
    ANALYZE_IMAGE_TOOL_NAME,
    APPLY_PATCH_TOOL_NAME,
    ANALYZE_VIDEO_TOOL_NAME,
    CONFIGURE_SKILL_TOOL_NAME,
    CONFIGURE_SUBAGENT_TOOL_NAME,
    DELEGATE_MANY_TOOL_NAME,
    DELEGATE_TOOL_NAME,
    EDIT_FILE_TOOL_NAME,
    EXEC_TOOL_NAME,
    OCR_IMAGE_TOOL_NAME,
    RUN_WORKFLOW_TOOL_NAME,
    TRANSCRIBE_AUDIO_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)
from ..tools import ToolRegistry
from ..subagent_prompts import get_all_subagents, load_metadata, load_prompt
from ..subagent_prompts.profiles import (
    CODE_REVIEWER_PROMPT_TYPE,
    PARALLEL_SAFE_PROFILE_NAMES,
    RESEARCH_PROFILE,
    REVIEW_PROMPT_TYPES,
    WRITE_TOOLS,
    profile_for_subagent,
)
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
from ..tools.permissions import PermissionDecision, ToolPermissionPolicy
from ..tools.result_status import classify_tool_result_status, tool_error_result
from ..tools.verify import classify_verification_result
from ..utils.json_safe import json_safe_value
from ..utils import (
    count_messages_tokens,
    count_text_tokens,
    sanitize_assistant_visible_text,
    strip_assistant_internal_scaffolding,
)
from ..utils.log import logger
from ..utils.log_redaction import redact_log_preview
from ..runs.trace import RunCancelledError, RunHookService, RunTraceRecorder, mcp_tool_names as list_mcp_tool_names
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
from ..tools.access import ToolAccessResolver
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


# Delegated subagent task runner and workflows.
class WritePathPermissionPolicy(ToolPermissionPolicy):
    """Restrict filesystem write tools to an allowlist of workspace-relative paths."""

    def __init__(self, allowed_patterns: frozenset[str]):
        self.allowed_patterns = allowed_patterns

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of the write path guardrail."""
        return {
            "kind": "subagent_write_path",
            "allowed_patterns": sorted(self.allowed_patterns),
        }

    def is_tool_exposed(self, tool_name: str, tool_risk_levels: Any = None) -> bool:
        del tool_name, tool_risk_levels
        return True

    @staticmethod
    def _normalize_path(value: Any) -> str:
        return str(value or "").replace("\\", "/").lstrip("./")

    def _path_allowed(self, path: str) -> bool:
        normalized = self._normalize_path(path)
        if not normalized:
            return False
        return any(fnmatch.fnmatch(normalized, pattern) for pattern in self.allowed_patterns)

    @staticmethod
    def _write_paths(tool_name: str, params: Any) -> list[str]:
        if not isinstance(params, dict):
            return []
        if tool_name in {WRITE_FILE_TOOL_NAME, EDIT_FILE_TOOL_NAME}:
            return [str(params.get("path") or "")]
        if tool_name != APPLY_PATCH_TOOL_NAME:
            return []
        changes = params.get("changes")
        if not isinstance(changes, list):
            return []
        return [str(change.get("path") or "") for change in changes if isinstance(change, dict)]

    def check(self, tool_name: str, params: Any, tool_risk_levels: Any = None) -> PermissionDecision:
        del tool_risk_levels
        if tool_name not in WRITE_TOOLS or not self.allowed_patterns:
            return PermissionDecision(True)
        for path in self._write_paths(tool_name, params):
            if not self._path_allowed(path):
                allowed = ", ".join(sorted(self.allowed_patterns))
                return PermissionDecision(
                    False,
                    f"path '{path}' is outside allowed subagent write paths ({allowed})",
                )
        return PermissionDecision(True)


def build_subagent_tool_registry(
    base_registry: ToolRegistry,
    prompt_type: str,
    *,
    app_home: Any = None,
    session_workspace: Any = None,
) -> ToolRegistry:
    """Return a child registry constrained by the subagent capability profile."""
    profile = profile_for_subagent(
        prompt_type,
        app_home=app_home,
        session_workspace=session_workspace,
    )
    overlay_policy = ToolPermissionPolicy(allowed_tools=sorted(profile.allowed_tools))
    extra_policies: tuple[ToolPermissionPolicy, ...] = (
        (WritePathPermissionPolicy(profile.write_path_patterns),)
        if profile.write_path_patterns
        else ()
    )
    resolution = ToolAccessResolver().resolve_overlay(
        base_registry,
        overlay_policy=overlay_policy,
        include_names=profile.allowed_tools,
        extra_policies=extra_policies,
        metadata_kind=f"subagent:{profile.name}",
    )
    return resolution.registry


SUBAGENT_TASK_ID_LABEL = "Task ID"
SUBAGENT_PROMPT_TYPE_LABEL = "Subagent"
STRUCTURED_SUBAGENT_SCHEMA_VERSION = 1
READONLY_SUBAGENT_RESULT_CONTRACT = "readonly_subagent_result"
STRUCTURED_SUBAGENT_SCHEMA_VERSION_FIELD = "schema_version"
STRUCTURED_SUBAGENT_CONTRACT_FIELD = "contract"
STRUCTURED_SUBAGENT_PROMPT_TYPE_FIELD = "prompt_type"
STRUCTURED_SUBAGENT_STATUS_FIELD = "status"
STRUCTURED_SUBAGENT_SUMMARY_FIELD = "summary"
STRUCTURED_SUBAGENT_SECTIONS_FIELD = "sections"
STRUCTURED_SUBAGENT_SECTION_TYPE_FIELD = "type"
STRUCTURED_SUBAGENT_ITEMS_FIELD = "items"
STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD = "section_count"
STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD = "item_count"
STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD = "finding_count"
STRUCTURED_SUBAGENT_QUESTIONS_FIELD = "questions"
STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD = "question_count"
STRUCTURED_SUBAGENT_RESIDUAL_RISKS_FIELD = "residual_risks"
STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD = "residual_risk_count"
STRUCTURED_SUBAGENT_SOURCES_FIELD = "sources"
STRUCTURED_SUBAGENT_SOURCE_COUNT_FIELD = "source_count"
STRUCTURED_SUBAGENT_TRUNCATED_FIELD = "truncated"
STRUCTURED_SUBAGENT_OK_STATUS = "ok"
STRUCTURED_SUBAGENT_NEEDS_INPUT_STATUS = "needs_input"
STRUCTURED_SUBAGENT_INCONCLUSIVE_STATUS = "inconclusive"
ALLOWED_STRUCTURED_SUBAGENT_STATUSES = frozenset(
    {
        STRUCTURED_SUBAGENT_OK_STATUS,
        STRUCTURED_SUBAGENT_NEEDS_INPUT_STATUS,
        STRUCTURED_SUBAGENT_INCONCLUSIVE_STATUS,
    }
)
MAX_STRUCTURED_SUBAGENT_SUMMARY_CHARS = 280
MAX_STRUCTURED_SUBAGENT_TEXT_CHARS = 500
MAX_STRUCTURED_SUBAGENT_SECTIONS = 8
MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION = 12
MAX_STRUCTURED_SUBAGENT_QUESTIONS = 8
MAX_STRUCTURED_SUBAGENT_RESIDUAL_RISKS = 8
MAX_STRUCTURED_SUBAGENT_SOURCES = 12
_JSON_FENCE_RE = re.compile(r"```json\s*(?P<body>.*?)\s*```", re.IGNORECASE | re.DOTALL)


def subagent_result_line(label: str, value: object) -> str:
    return f"{label}: {value}"


def parse_subagent_result_line(line: str | None, label: str) -> str | None:
    prefix = f"{label}: "
    text = str(line or "")
    if not text.startswith(prefix):
        return None
    return text[len(prefix) :].strip() or None


def is_clean_structured_subagent_status(status: str | None) -> bool:
    """Return whether a structured subagent status represents a clean result."""
    return str(status or "").strip() == STRUCTURED_SUBAGENT_OK_STATUS


def parse_structured_subagent_output(
    text: str,
    *,
    prompt_type: str,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Return visible text plus optional structured payload parsed from a trailing JSON block."""
    raw_text = str(text or "")
    visible_text, raw_json = _split_trailing_json_block(raw_text)
    if raw_json is None:
        return visible_text, None, None

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return _fallback_visible_text(visible_text, raw_text), None, f"invalid_json: {exc.msg}"

    normalized, error = _normalize_structured_payload(payload, prompt_type=prompt_type, fallback_text=visible_text)
    if normalized is None:
        return _fallback_visible_text(visible_text, raw_text), None, error
    return _fallback_visible_text(visible_text, raw_text) or normalized[STRUCTURED_SUBAGENT_SUMMARY_FIELD], normalized, None


def build_structured_subagent_contract_instructions(prompt_type: str) -> str:
    """Return shared prompt instructions for the structured readonly subagent contract."""
    normalized_prompt_type = str(prompt_type or "subagent").strip() or "subagent"
    return (
        "## Structured Output Contract\n\n"
        "After your normal human-readable answer, append one final fenced `json` block and do not output anything after it. "
        "Keep the human-readable answer useful on its own because the JSON block is optional machine-readable metadata.\n\n"
        "Rules:\n"
        f"- The JSON block must be one object with `schema_version: 1`, `contract: \"{READONLY_SUBAGENT_RESULT_CONTRACT}\"`, and `prompt_type: \"{normalized_prompt_type}\"`.\n"
        "- `status` must be one of `ok`, `needs_input`, or `inconclusive`.\n"
        "- `summary` must be one concise conclusion sentence.\n"
        "- Put main structured content in `sections`, using stable keys and one of these `type` values when applicable: `finding_list`, `bullet_list`, `outline`, `api_surface`, `pattern_matches`, `fact_check`.\n"
        "- Use `questions` only for concrete missing-input questions.\n"
        "- Use `residual_risks` only for unverified assumptions, blind spots, or remaining uncertainty.\n"
        "- Use `sources` only for concrete evidence you actually inspected.\n"
        "- Do not wrap the whole answer in JSON. Only the final fenced block should be JSON.\n\n"
        "Template:\n"
        "```json\n"
        "{\n"
        '  "schema_version": 1,\n'
        f'  "contract": "{READONLY_SUBAGENT_RESULT_CONTRACT}",\n'
        f'  "prompt_type": "{normalized_prompt_type}",\n'
        '  "status": "ok",\n'
        '  "summary": "...",\n'
        '  "sections": [\n'
        '    {\n'
        '      "key": "main",\n'
        '      "title": "Main Results",\n'
        '      "type": "bullet_list",\n'
        '      "items": ["..."]\n'
        '    }\n'
        '  ],\n'
        '  "questions": [],\n'
        '  "residual_risks": [],\n'
        '  "sources": []\n'
        "}\n"
        "```"
    )


def _split_trailing_json_block(text: str) -> tuple[str, str | None]:
    last_match = None
    for match in _JSON_FENCE_RE.finditer(str(text or "")):
        last_match = match
    if last_match is None:
        return str(text or "").strip(), None
    visible = (str(text or "")[: last_match.start()] + str(text or "")[last_match.end():]).strip()
    return visible, last_match.group("body").strip()


def _fallback_visible_text(visible_text: str, raw_text: str) -> str:
    text = str(visible_text or "").strip() or str(raw_text or "").strip()
    return _bounded_text(text, MAX_STRUCTURED_SUBAGENT_TEXT_CHARS)


def _normalize_structured_payload(
    payload: Any,
    *,
    prompt_type: str,
    fallback_text: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "payload_must_be_object"
    if int(payload.get(STRUCTURED_SUBAGENT_SCHEMA_VERSION_FIELD) or 0) != STRUCTURED_SUBAGENT_SCHEMA_VERSION:
        return None, "schema_version_mismatch"
    if str(payload.get(STRUCTURED_SUBAGENT_CONTRACT_FIELD) or "").strip() != READONLY_SUBAGENT_RESULT_CONTRACT:
        return None, "contract_mismatch"

    payload_prompt_type = str(payload.get(STRUCTURED_SUBAGENT_PROMPT_TYPE_FIELD) or "").strip()
    if payload_prompt_type and payload_prompt_type != str(prompt_type or "").strip():
        return None, "prompt_type_mismatch"

    truncated = False
    status = str(payload.get(STRUCTURED_SUBAGENT_STATUS_FIELD) or STRUCTURED_SUBAGENT_INCONCLUSIVE_STATUS).strip() or STRUCTURED_SUBAGENT_INCONCLUSIVE_STATUS
    if status not in ALLOWED_STRUCTURED_SUBAGENT_STATUSES:
        status = STRUCTURED_SUBAGENT_INCONCLUSIVE_STATUS
        truncated = True

    summary = _bounded_text(str(payload.get(STRUCTURED_SUBAGENT_SUMMARY_FIELD) or "").strip() or _first_nonempty_line(fallback_text), MAX_STRUCTURED_SUBAGENT_SUMMARY_CHARS)
    if summary != str(payload.get(STRUCTURED_SUBAGENT_SUMMARY_FIELD) or "").strip():
        truncated = truncated or bool(str(payload.get(STRUCTURED_SUBAGENT_SUMMARY_FIELD) or "").strip())

    sections, sections_truncated = _normalize_sections(payload.get(STRUCTURED_SUBAGENT_SECTIONS_FIELD))
    questions, questions_truncated = _normalize_string_list(payload.get(STRUCTURED_SUBAGENT_QUESTIONS_FIELD), limit=MAX_STRUCTURED_SUBAGENT_QUESTIONS)
    residual_risks, residual_risks_truncated = _normalize_string_list(
        payload.get(STRUCTURED_SUBAGENT_RESIDUAL_RISKS_FIELD) or payload.get("residualRisks"),
        limit=MAX_STRUCTURED_SUBAGENT_RESIDUAL_RISKS,
    )
    sources, sources_truncated = _normalize_sources(payload.get(STRUCTURED_SUBAGENT_SOURCES_FIELD))
    truncated = truncated or sections_truncated or questions_truncated or residual_risks_truncated or sources_truncated

    item_count = sum(len(section.get(STRUCTURED_SUBAGENT_ITEMS_FIELD, [])) for section in sections)
    finding_count = sum(len(section.get(STRUCTURED_SUBAGENT_ITEMS_FIELD, [])) for section in sections if section.get(STRUCTURED_SUBAGENT_SECTION_TYPE_FIELD) == "finding_list")
    return {
        STRUCTURED_SUBAGENT_SCHEMA_VERSION_FIELD: STRUCTURED_SUBAGENT_SCHEMA_VERSION,
        STRUCTURED_SUBAGENT_CONTRACT_FIELD: READONLY_SUBAGENT_RESULT_CONTRACT,
        STRUCTURED_SUBAGENT_PROMPT_TYPE_FIELD: str(prompt_type or "").strip() or None,
        STRUCTURED_SUBAGENT_STATUS_FIELD: status,
        STRUCTURED_SUBAGENT_SUMMARY_FIELD: summary,
        STRUCTURED_SUBAGENT_SECTIONS_FIELD: sections,
        STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD: len(sections),
        STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD: item_count,
        STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD: finding_count,
        STRUCTURED_SUBAGENT_QUESTIONS_FIELD: questions,
        STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD: len(questions),
        STRUCTURED_SUBAGENT_RESIDUAL_RISKS_FIELD: residual_risks,
        STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD: len(residual_risks),
        STRUCTURED_SUBAGENT_SOURCES_FIELD: sources,
        STRUCTURED_SUBAGENT_SOURCE_COUNT_FIELD: len(sources),
        STRUCTURED_SUBAGENT_TRUNCATED_FIELD: truncated,
    }, None


def _normalize_sections(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    truncated = len(value) > MAX_STRUCTURED_SUBAGENT_SECTIONS
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(value[:MAX_STRUCTURED_SUBAGENT_SECTIONS], start=1):
        if not isinstance(section, dict):
            truncated = True
            continue
        key = _bounded_text(str(section.get("key") or f"section_{index}"), 64)
        title = _bounded_text(str(section.get("title") or key), 120)
        section_type = _bounded_text(str(section.get(STRUCTURED_SUBAGENT_SECTION_TYPE_FIELD) or "bullet_list"), 64)
        items_value = section.get(STRUCTURED_SUBAGENT_ITEMS_FIELD)
        items: list[Any] = []
        if isinstance(items_value, list):
            truncated = truncated or len(items_value) > MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION
            for item in items_value[:MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION]:
                normalized = _bounded_json_value(item)
                if normalized in (None, "", [], {}):
                    continue
                items.append(normalized)
        elif items_value not in (None, ""):
            truncated = True
        sections.append(
            {
                "key": key,
                "title": title,
                STRUCTURED_SUBAGENT_SECTION_TYPE_FIELD: section_type,
                STRUCTURED_SUBAGENT_ITEMS_FIELD: items,
            }
        )
    return sections, truncated


def _normalize_string_list(value: Any, *, limit: int) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    truncated = len(value) > limit
    items = [
        _bounded_text(str(item or "").strip(), MAX_STRUCTURED_SUBAGENT_TEXT_CHARS)
        for item in value[:limit]
        if str(item or "").strip()
    ]
    return items, truncated


def _normalize_sources(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    truncated = len(value) > MAX_STRUCTURED_SUBAGENT_SOURCES
    items: list[dict[str, Any]] = []
    for source in value[:MAX_STRUCTURED_SUBAGENT_SOURCES]:
        if not isinstance(source, dict):
            truncated = True
            continue
        normalized = {
            "kind": _bounded_text(str(source.get("kind") or "unknown"), 32),
            "path": _bounded_text(str(source.get("path") or ""), 240),
            "title": _bounded_text(str(source.get("title") or ""), 160),
            "url": _bounded_text(str(source.get("url") or ""), 240),
            "start_line": _non_negative_int(source.get("start_line") or source.get("startLine")),
            "end_line": _non_negative_int(source.get("end_line") or source.get("endLine")),
        }
        items.append({key: value for key, value in normalized.items() if value not in (None, "", 0)})
    return items, truncated


def _bounded_json_value(value: Any) -> Any:
    safe = json_safe_value(value)
    if isinstance(safe, str):
        return _bounded_text(safe, MAX_STRUCTURED_SUBAGENT_TEXT_CHARS)
    if isinstance(safe, list):
        return [_bounded_json_value(item) for item in safe[:MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION]]
    if isinstance(safe, dict):
        limited: dict[str, Any] = {}
        for index, (key, item) in enumerate(safe.items()):
            if index >= 12:
                break
            limited[_bounded_text(str(key), 64)] = _bounded_json_value(item)
        return limited
    return safe


def _bounded_text(text: str, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def _first_nonempty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        candidate = str(line or "").strip()
        if candidate:
            return candidate
    return ""


def _non_negative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)

DEFAULT_MAX_PARALLEL_SUBAGENTS = 2
MAX_PARALLEL_SUBAGENTS = 4
DEFAULT_SUBAGENT_MAX_TOOL_ITERATIONS = 100
SUBAGENT_TASK_ID_PATTERN = r"^task_[A-Za-z0-9_-]{8,64}$"
_TASK_ID_RE = re.compile(SUBAGENT_TASK_ID_PATTERN)


def new_subagent_task_id() -> str:
    """Return a compact id that can be shown to the model/user and reused later."""
    return f"task_{uuid4().hex[:12]}"


def validate_subagent_task_id(task_id: str) -> str | None:
    """Return an error message when a task id is malformed."""
    value = str(task_id or "").strip()
    if _TASK_ID_RE.fullmatch(value):
        return None
    return "task_id must match pattern task_[A-Za-z0-9_-]{8,64}."


def build_child_subagent_session_id(parent_session_id: str, task_id: str) -> str:
    """Build the storage session id for one child subagent task session."""
    return f"{parent_session_id}:subagent:{task_id}"


def extract_subagent_prompt_type(messages: list[StoredMessage]) -> str | None:
    """Return the prompt type stored on the first child task message, if available."""
    for message in messages:
        metadata = getattr(message, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            continue
        if metadata.get("kind") != "subagent_task":
            continue
        prompt_type = metadata.get("prompt_type")
        if isinstance(prompt_type, str) and prompt_type.strip():
            return prompt_type.strip()
    return None


class SubagentMessageBuilder:
    """Build prompt/messages for delegated subagent work."""

    def __init__(self, prompt_loader=load_prompt, skills_loader: SkillsLoader | None = None):
        self.prompt_loader = prompt_loader
        self.skills_loader = skills_loader

    def build_system_prompt(
        self,
        prompt_type: str = "writer",
        workspace: str | Path | None = None,
        app_home: Path | None = None,
    ) -> str:
        prompt_body = self.prompt_loader(
            prompt_type,
            app_home=app_home,
            session_workspace=workspace,
        )
        prompt_metadata = load_metadata(
            prompt_type,
            app_home=app_home,
            session_workspace=workspace,
        )
        runtime_context = build_runtime_context(workspace=workspace)
        workspace_path = Path(workspace) if workspace is not None else None
        skills_summary = ""
        if self.skills_loader is not None:
            personal_skills_dir = workspace_path / "skills" if workspace_path is not None else None
            skills_summary = self.skills_loader.build_skills_summary(personal_skills_dir)

        sections = []
        if prompt_body:
            sections.append(prompt_body)
        else:
            sections.append(
                "## 角色（Role）\n"
                f"你是專注於單一任務的 `{prompt_type}` 助手。\n\n"
                "## 任務（Task）\n"
                "1. 先理解目前任務。\n"
                "2. 根據已提供資訊完成內容。\n"
                "3. 若資訊不足，只提出必要問題。\n\n"
                "## 規範（Constraints）\n"
                "- 聚焦當前任務\n"
                "- 不要虛構事實\n"
                "- 直接輸出可交付內容\n\n"
                "## 輸出（Output）\n"
                "- 若資訊足夠：直接輸出完成內容。\n"
                "- 若資訊不足：列出需要補充的問題。"
            )

        if str(prompt_metadata.get("structured_output_contract") or "").strip() == READONLY_SUBAGENT_RESULT_CONTRACT:
            sections.extend(["", build_structured_subagent_contract_instructions(prompt_type)])

        if skills_summary:
            sections.extend([
                "",
                "If a listed skill is relevant, read it before using other non-trivial tools so you can follow its workflow first.",
                "",
                skills_summary,
            ])
        sections.extend(["", runtime_context])
        return "\n".join(sections).strip()

    def build_messages(
        self,
        task: str,
        prompt_type: str = "writer",
        workspace: str | Path | None = None,
        app_home: Path | None = None,
    ) -> list[ChatMessage]:
        system_prompt = self.build_system_prompt(prompt_type, workspace=workspace, app_home=app_home)
        return [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=task),
        ]


def _subagent_error_result(
    message: str,
    *,
    category: str,
    error_type: str = "DelegateToolError",
    invalid_arguments: bool = False,
    tool_name: str = DELEGATE_TOOL_NAME,
) -> str:
    error = str(message or "").strip()
    return tool_error_result(
        error,
        error_type=error_type,
        category=category,
        repeated_error_key=error if invalid_arguments else None,
        invalid_arguments=invalid_arguments,
        metadata={"tool_name": tool_name},
    )


def _subagent_validation_error(message: str, *, tool_name: str = DELEGATE_TOOL_NAME) -> str:
    return _subagent_error_result(
        message,
        category="invalid_arguments",
        error_type="ToolValidationError",
        invalid_arguments=True,
        tool_name=tool_name,
    )


def _subagent_preparation_error_detail(message: str) -> str:
    status = classify_tool_result_status(message)
    if not status.ok and status.error:
        return status.error
    return str(message or "").strip()


@dataclass(frozen=True)
class PreparedSubagentTask:
    """Resolved child-task execution inputs after validation and profile selection."""

    task_text: str
    task_preview: str
    prompt_type: str
    task_id: str
    child_session_id: str
    child_run_id: str
    parent_session_id: str
    parent_run_id: str | None
    is_resume: bool
    app_home: Path | None
    workspace: Path
    subagent_tools: ToolRegistry
    subagent_profile_name: str
    provider_override: Any | None = None
    group_id: str | None = None
    group_index: int | None = None
    group_total: int | None = None


@dataclass(frozen=True)
class SubagentTaskOutcome:
    """Structured result for one delegated child task."""

    task_id: str
    prompt_type: str
    child_session_id: str
    child_run_id: str
    status: str
    content: str = ""
    error: str = ""
    summary: str = ""
    executed_tool_calls: int = 0
    had_tool_error: bool = False
    verification_attempted: bool = False
    verification_passed: bool = False
    is_resume: bool = False
    structured_output: dict[str, Any] | None = None
    group_id: str | None = None
    group_index: int | None = None
    group_total: int | None = None


class SubagentRunService:
    """Runs and resumes delegated subagent sessions."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        tools: ToolRegistry,
        max_history_getter: Callable[[], int],
        app_home_getter: Callable[[], Path | None],
        workspace_getter: Callable[[], Path],
        current_session_id_getter: Callable[[], str | None],
        current_run_id_getter: Callable[[], str | None],
        current_channel_getter: Callable[[], str | None],
        current_external_chat_id_getter: Callable[[], str | None],
        max_tool_iterations_getter: Callable[[], int],
        provider_getter: Callable[[], Any],
        llm_config_getter: Callable[[], Any | None],
        should_cancel_parent_run: Callable[[str, str | None], bool],
        skills_loader_getter: Callable[[], Any],
        save_message: Callable[[str, str, str, str | None, dict[str, Any] | None], Awaitable[None]],
        execute_messages: Callable[..., Awaitable[Any]],
        log_prepared_messages: Callable[[str, list[dict[str, Any]]], None],
        format_log_preview: Callable[..., str],
        run_trace: RunTraceRecorder,
        run_hooks: RunHookService,
        record_delegated_task_update: Callable[[str | None, StoredDelegatedTask], None],
    ):
        self.storage = storage
        self.tools = tools
        self._max_history_getter = max_history_getter
        self._app_home_getter = app_home_getter
        self._workspace_getter = workspace_getter
        self._current_session_id_getter = current_session_id_getter
        self._current_run_id_getter = current_run_id_getter
        self._current_channel_getter = current_channel_getter
        self._current_external_chat_id_getter = current_external_chat_id_getter
        self._max_tool_iterations_getter = max_tool_iterations_getter
        self._provider_getter = provider_getter
        self._llm_config_getter = llm_config_getter
        self._should_cancel_parent_run = should_cancel_parent_run
        self._skills_loader_getter = skills_loader_getter
        self._save_message = save_message
        self._execute_messages = execute_messages
        self._log_prepared_messages = log_prepared_messages
        self._format_log_preview = format_log_preview
        self.run_trace = run_trace
        self.run_hooks = run_hooks
        self._record_delegated_task_update = record_delegated_task_update

    def build_tools(self, prompt_type: str, *, workspace: Path | None = None) -> ToolRegistry:
        """Build the tool registry exposed to one subagent profile."""
        return build_subagent_tool_registry(
            self.tools,
            prompt_type,
            app_home=self._app_home_getter(),
            session_workspace=workspace or self._workspace_getter(),
        )

    @staticmethod
    def _message_role_and_content(message: Any) -> tuple[str, str]:
        if isinstance(message, dict):
            return message.get("role", "?"), message.get("content", "")
        return getattr(message, "role", "?"), getattr(message, "content", "")

    @staticmethod
    def _new_run_id() -> str:
        return f"run_{uuid4().hex}"

    @staticmethod
    def _new_group_id() -> str:
        return f"fanout_{uuid4().hex[:12]}"

    @staticmethod
    def _delegation_mode(prepared: PreparedSubagentTask) -> str:
        return "parallel" if prepared.group_id else "serial"

    @classmethod
    def _delegation_metadata(cls, prepared: PreparedSubagentTask) -> dict[str, Any]:
        payload: dict[str, Any] = {"delegation_mode": cls._delegation_mode(prepared)}
        if prepared.group_id is not None:
            payload.update(
                {
                    "fanout_group_id": prepared.group_id,
                    "fanout_index": prepared.group_index,
                    "fanout_total": prepared.group_total,
                }
            )
        return payload

    @staticmethod
    def _selected_for_task(prepared: PreparedSubagentTask) -> bool:
        return prepared.group_id is None

    async def _emit_parent_event(
        self,
        *,
        parent_session_id: str,
        parent_run_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if parent_run_id is None:
            return
        await self.run_trace.emit_event(
            parent_session_id,
            parent_run_id,
            event_type,
            payload,
            channel=self._current_channel_getter(),
            external_chat_id=self._current_external_chat_id_getter(),
        )

    def _cancel_requested(self, parent_session_id: str, parent_run_id: str | None) -> bool:
        if parent_run_id is None:
            return False
        return self._should_cancel_parent_run(parent_session_id, parent_run_id)

    def _max_tool_iterations(self) -> int:
        try:
            return max(1, int(self._max_tool_iterations_getter()))
        except (TypeError, ValueError):
            return DEFAULT_SUBAGENT_MAX_TOOL_ITERATIONS

    def _resolve_provider_override(
        self,
        prompt_type: str,
        *,
        app_home: Path | None,
        workspace: Path,
    ) -> Any | None:
        metadata = load_metadata(prompt_type, app_home=app_home, session_workspace=workspace)
        llm_provider = str(metadata.get("llm_provider") or "").strip()
        llm_model = str(metadata.get("llm_model") or "").strip()
        if not llm_provider and not llm_model:
            return None

        base_provider = self._provider_getter()
        provider_override = base_provider
        if llm_provider:
            llm_config = self._llm_config_getter()
            providers = getattr(llm_config, "providers", {}) if llm_config is not None else {}
            provider_config = providers.get(llm_provider) if isinstance(providers, dict) else None
            if provider_config is not None and getattr(provider_config, "enabled", True):
                provider_id = str(getattr(provider_config, "provider", None) or llm_provider or "").strip()
                defaults = provider_profile_defaults(
                    provider_id,
                    auth_type=getattr(provider_config, "auth_type", "api_key"),
                    api_mode=getattr(provider_config, "api_mode", None),
                )
                if defaults.api_mode or defaults.auth_type != "api_key":
                    provider_override = create_llm_from_runtime(
                        resolve_provider_runtime(
                            provider_config,
                            provider_name=defaults.provider_id,
                            app_home=self._app_home_getter(),
                        )
                    )
                else:
                    provider_override = create_llm(
                        api_key=getattr(provider_config, "api_key", ""),
                        model=getattr(provider_config, "model", ""),
                        base_url=getattr(provider_config, "base_url", "") or "",
                        provider_name=defaults.provider_id,
                        enabled=getattr(provider_config, "enabled", True),
                        reasoning_enabled=getattr(provider_config, "reasoning_enabled", False),
                        reasoning_effort=getattr(provider_config, "reasoning_effort", None),
                        reasoning_max_tokens=getattr(provider_config, "reasoning_max_tokens", None),
                        reasoning_exclude=getattr(provider_config, "reasoning_exclude", False),
                        provider_sort=getattr(provider_config, "provider_sort", None),
                        require_parameters=getattr(provider_config, "require_parameters", False),
                    )
            else:
                return None

        try:
            current_model = str(provider_override.get_default_model() or "").strip()
        except Exception:
            current_model = ""
        if not llm_model:
            llm_model = current_model

        if llm_model == current_model:
            return None
        return ModelRoutedProvider(provider_override, model=llm_model)

    async def _prepare_task(
        self,
        task: str,
        prompt_type: str | None = None,
        task_id: str | None = None,
        *,
        group_id: str | None = None,
        group_index: int | None = None,
        group_total: int | None = None,
    ) -> PreparedSubagentTask | str:
        task_text = str(task or "").strip()
        if not task_text:
            return _subagent_validation_error("subagent task must be a non-empty string.")

        app_home = self._app_home_getter()
        workspace = self._workspace_getter()
        subagents = get_all_subagents(app_home, session_workspace=workspace)
        parent_session_id = self._current_session_id_getter() or "default"
        parent_run_id = self._current_run_id_getter()

        resume_task_id = str(task_id or "").strip() or None
        is_resume = resume_task_id is not None
        if resume_task_id:
            validation_error = validate_subagent_task_id(resume_task_id)
            if validation_error:
                return _subagent_validation_error(validation_error)
            child_task_id = resume_task_id
        else:
            child_task_id = new_subagent_task_id()

        child_session_id = build_child_subagent_session_id(parent_session_id, child_task_id)
        child_run_id = self._new_run_id()
        existing_child_messages = await self.storage.get_messages(child_session_id)
        if is_resume and not existing_child_messages:
            return _subagent_error_result(
                f"unknown task_id '{child_task_id}' for current session. Start a new delegate task instead.",
                category="task_not_found",
            )

        stored_prompt_type = extract_subagent_prompt_type(existing_child_messages)
        requested_prompt_type = str(prompt_type).strip() if prompt_type is not None else ""
        effective_prompt_type = requested_prompt_type or stored_prompt_type or "writer"
        if stored_prompt_type and requested_prompt_type and requested_prompt_type != stored_prompt_type:
            return _subagent_error_result(
                f"task_id '{child_task_id}' was created with prompt_type '{stored_prompt_type}', "
                f"not '{requested_prompt_type}'. Omit prompt_type or use the original prompt_type to resume.",
                category="task_prompt_mismatch",
            )
        if effective_prompt_type not in subagents:
            available = ", ".join(subagents)
            return _subagent_error_result(
                f"unknown subagent type '{effective_prompt_type}'. Available: {available}",
                category="unknown_subagent",
            )

        try:
            subagent_tools = self.build_tools(effective_prompt_type, workspace=workspace)
            subagent_profile = profile_for_subagent(
                effective_prompt_type,
                app_home=app_home,
                session_workspace=workspace,
            )
        except ValueError as e:
            return _subagent_error_result(str(e), category="subagent_profile")

        if group_id is not None and subagent_profile.name not in PARALLEL_SAFE_PROFILE_NAMES:
            allowed = ", ".join(sorted(PARALLEL_SAFE_PROFILE_NAMES))
            return _subagent_error_result(
                "parallel delegation only supports read-only or research subagents. "
                f"'{effective_prompt_type}' uses profile '{subagent_profile.name}', not one of: {allowed}.",
                category="parallel_profile_not_supported",
                error_type="DelegateManyToolError",
                tool_name=DELEGATE_MANY_TOOL_NAME,
            )

        return PreparedSubagentTask(
            task_text=task_text,
            task_preview=self._format_log_preview(task_text, max_chars=240),
            prompt_type=effective_prompt_type,
            task_id=child_task_id,
            child_session_id=child_session_id,
            child_run_id=child_run_id,
            parent_session_id=parent_session_id,
            parent_run_id=parent_run_id,
            is_resume=is_resume,
            app_home=app_home,
            workspace=workspace,
            subagent_tools=subagent_tools,
            subagent_profile_name=subagent_profile.name,
            provider_override=self._resolve_provider_override(
                effective_prompt_type,
                app_home=app_home,
                workspace=workspace,
            ),
            group_id=group_id,
            group_index=group_index,
            group_total=group_total,
        )

    async def _build_chat_messages(self, prepared: PreparedSubagentTask) -> list[ChatMessage]:
        subagent_builder = SubagentMessageBuilder(skills_loader=self._skills_loader_getter())
        chat_messages = [
            ChatMessage(
                role="system",
                content=subagent_builder.build_system_prompt(
                    prepared.prompt_type,
                    workspace=prepared.workspace,
                    app_home=prepared.app_home,
                ),
            )
        ]
        stored_child_messages = await self.storage.get_messages(
            prepared.child_session_id,
            limit=self._max_history_getter(),
        )
        for message in stored_child_messages:
            role, content = self._message_role_and_content(message)
            if role == "tool":
                continue
            chat_messages.append(ChatMessage(role=role, content=content))
        return chat_messages

    def _record_task_update(
        self,
        prepared: PreparedSubagentTask,
        *,
        status: str,
        summary: str = "",
        error: str = "",
        structured_output: dict[str, Any] | None = None,
        created_at: float = 0.0,
        updated_at: float = 0.0,
    ) -> None:
        metadata = self._delegation_metadata(prepared)
        if structured_output is not None:
            metadata = {**metadata, "structured_output": structured_output}
        self._record_delegated_task_update(
            prepared.parent_run_id,
            StoredDelegatedTask(
                task_id=prepared.task_id,
                prompt_type=prepared.prompt_type,
                status=status,
                selected=self._selected_for_task(prepared),
                summary=summary,
                error=error,
                child_session_id=prepared.child_session_id,
                last_child_run_id=prepared.child_run_id,
                metadata=metadata,
                created_at=created_at,
                updated_at=updated_at,
            ),
        )

    @staticmethod
    def _compact_structured_output(structured_output: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(structured_output, dict):
            return None
        return {
            STRUCTURED_SUBAGENT_SCHEMA_VERSION_FIELD: structured_output.get(STRUCTURED_SUBAGENT_SCHEMA_VERSION_FIELD),
            STRUCTURED_SUBAGENT_CONTRACT_FIELD: structured_output.get(STRUCTURED_SUBAGENT_CONTRACT_FIELD),
            STRUCTURED_SUBAGENT_PROMPT_TYPE_FIELD: structured_output.get(STRUCTURED_SUBAGENT_PROMPT_TYPE_FIELD),
            STRUCTURED_SUBAGENT_STATUS_FIELD: structured_output.get(STRUCTURED_SUBAGENT_STATUS_FIELD),
            STRUCTURED_SUBAGENT_SUMMARY_FIELD: structured_output.get(STRUCTURED_SUBAGENT_SUMMARY_FIELD),
            STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD: structured_output.get(STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD, 0),
            STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD: structured_output.get(STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD, 0),
            STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD: structured_output.get(STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD, 0),
            STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD: structured_output.get(STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD, 0),
            STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD: structured_output.get(STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD, 0),
            STRUCTURED_SUBAGENT_SECTIONS_FIELD: structured_output.get(STRUCTURED_SUBAGENT_SECTIONS_FIELD, []),
            STRUCTURED_SUBAGENT_QUESTIONS_FIELD: structured_output.get(STRUCTURED_SUBAGENT_QUESTIONS_FIELD, []),
            STRUCTURED_SUBAGENT_RESIDUAL_RISKS_FIELD: structured_output.get(STRUCTURED_SUBAGENT_RESIDUAL_RISKS_FIELD, []),
            STRUCTURED_SUBAGENT_SOURCES_FIELD: structured_output.get(STRUCTURED_SUBAGENT_SOURCES_FIELD, []),
            STRUCTURED_SUBAGENT_TRUNCATED_FIELD: bool(structured_output.get(STRUCTURED_SUBAGENT_TRUNCATED_FIELD)),
        }

    @staticmethod
    def _group_status_counts(statuses: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for status in statuses:
            key = str(status or "unknown").strip() or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return counts

    @classmethod
    def _group_summary(cls, status: str, *, total: int, counts: dict[str, int]) -> str:
        completed = counts.get(WORKFLOW_COMPLETED_STATUS, 0)
        failed = counts.get(WORKFLOW_FAILED_STATUS, 0) + counts.get(WORKFLOW_ERROR_STATUS, 0)
        cancelled = counts.get(WORKFLOW_CANCELLED_STATUS, 0)
        if is_workflow_running_status(status):
            return f"Queued {total} parallel subagent task(s)."
        if is_workflow_completed_status(status):
            return f"Completed {completed}/{total} parallel subagent task(s)."
        if is_workflow_failed_status(status):
            tail = f"; {cancelled} cancelled." if cancelled else "."
            return f"Completed {completed}/{total} parallel subagent task(s); {failed} failed{tail}"
        if is_workflow_cancelled_status(status):
            settled = completed + failed + cancelled
            return f"Cancelled parallel subagent group after {settled}/{total} task(s) settled."
        return f"Parallel subagent group status: {status}."

    @classmethod
    def _build_group_payload(
        cls,
        prepared_tasks: list[PreparedSubagentTask],
        *,
        group_id: str,
        max_parallel: int,
        status: str,
        outcomes_by_task_id: dict[str, SubagentTaskOutcome] | None = None,
        default_missing_status: str | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        outcome_map = outcomes_by_task_id or {}
        tasks_payload: list[dict[str, Any]] = []
        statuses: list[str] = []
        for prepared in prepared_tasks:
            outcome = outcome_map.get(prepared.task_id)
            child_status = (
                outcome.status
                if outcome is not None
                else str(default_missing_status or status or "unknown").strip() or "unknown"
            )
            statuses.append(child_status)
            item = {
                "task_id": prepared.task_id,
                "prompt_type": prepared.prompt_type,
                "status": child_status,
                "child_session_id": prepared.child_session_id,
                "child_run_id": prepared.child_run_id,
                "fanout_index": prepared.group_index,
            }
            if outcome is not None:
                if outcome.summary:
                    item["summary"] = outcome.summary
                if outcome.error:
                    item["error"] = outcome.error
                compact_structured_output = cls._compact_structured_output(outcome.structured_output)
                if compact_structured_output is not None:
                    item["structured_output"] = {
                        STRUCTURED_SUBAGENT_STATUS_FIELD: compact_structured_output.get(STRUCTURED_SUBAGENT_STATUS_FIELD),
                        STRUCTURED_SUBAGENT_SUMMARY_FIELD: compact_structured_output.get(STRUCTURED_SUBAGENT_SUMMARY_FIELD),
                        STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD: compact_structured_output.get(STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD, 0),
                        STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD: compact_structured_output.get(STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD, 0),
                        STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD: compact_structured_output.get(STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD, 0),
                        STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD: compact_structured_output.get(STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD, 0),
                        STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD: compact_structured_output.get(STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD, 0),
                    }
            tasks_payload.append(item)

        counts = cls._group_status_counts(statuses)
        return {
            "status": status,
            "group_id": group_id,
            "total_tasks": len(prepared_tasks),
            "max_parallel": max_parallel,
            "completed_count": counts.get(WORKFLOW_COMPLETED_STATUS, 0),
            "failed_count": counts.get(WORKFLOW_FAILED_STATUS, 0) + counts.get(WORKFLOW_ERROR_STATUS, 0),
            "cancelled_count": counts.get(WORKFLOW_CANCELLED_STATUS, 0),
            "task_ids": [prepared.task_id for prepared in prepared_tasks],
            "tasks": tasks_payload,
            "summary": cls._group_summary(status, total=len(prepared_tasks), counts=counts),
            **({"error": error} if error else {}),
        }

    async def _execute_prepared_task(
        self,
        prepared: PreparedSubagentTask,
        *,
        should_cancel: Callable[[], bool] | None,
        raise_on_failure: bool,
    ) -> SubagentTaskOutcome:
        max_tool_iterations = self._max_tool_iterations()
        run_metadata = {
            "kind": "subagent",
            "objective": prepared.task_preview,
            "task_id": prepared.task_id,
            "prompt_type": prepared.prompt_type,
            "parent_session_id": prepared.parent_session_id,
            "parent_run_id": prepared.parent_run_id,
            "resume": prepared.is_resume,
            "max_tool_iterations": max_tool_iterations,
            **self._delegation_metadata(prepared),
        }
        started_at = time.time()
        lifecycle_payload = {
            "status": WORKFLOW_RUNNING_STATUS,
            "task_id": prepared.task_id,
            "prompt_type": prepared.prompt_type,
            "child_session_id": prepared.child_session_id,
            "child_run_id": prepared.child_run_id,
            "parent_session_id": prepared.parent_session_id,
            "parent_run_id": prepared.parent_run_id,
            "resume": prepared.is_resume,
            "max_tool_iterations": max_tool_iterations,
            "task_preview": prepared.task_preview,
            "message": f"Started {prepared.prompt_type} subagent task {prepared.task_id}.",
            **self._delegation_metadata(prepared),
        }
        await self.run_trace.create_run(
            prepared.child_session_id,
            prepared.child_run_id,
            status=WORKFLOW_RUNNING_STATUS,
            metadata=run_metadata,
        )
        await self.run_trace.emit_event(
            prepared.child_session_id,
            prepared.child_run_id,
            RUN_STARTED_EVENT,
            lifecycle_payload,
        )
        self._record_task_update(
            prepared,
            status=WORKFLOW_RUNNING_STATUS,
            created_at=started_at,
            updated_at=started_at,
        )
        await self._emit_parent_event(
            parent_session_id=prepared.parent_session_id,
            parent_run_id=prepared.parent_run_id,
            event_type=SUBAGENT_STARTED_EVENT,
            payload=lifecycle_payload,
        )

        await self._save_message(
            prepared.child_session_id,
            "user",
            prepared.task_text,
            None,
            {
                "kind": "subagent_task",
                "task_id": prepared.task_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "prompt_type": prepared.prompt_type,
                "run_id": prepared.child_run_id,
                "resume": prepared.is_resume,
                **self._delegation_metadata(prepared),
            },
        )

        log_id = f"{prepared.parent_session_id}:subagent:{prepared.prompt_type}:{prepared.task_id}"
        chat_messages = await self._build_chat_messages(prepared)
        self._log_prepared_messages(
            log_id,
            [{"role": msg.role, "content": msg.content} for msg in chat_messages],
        )
        logger.info(
            f"[{log_id}] subagent.run | child_session_id={prepared.child_session_id} resume={prepared.is_resume} "
            f"workspace={prepared.workspace} task={self._format_log_preview(prepared.task_text, max_chars=200)}"
        )
        logger.info(
            f"[{log_id}] subagent.tools | profile={prepared.subagent_profile_name} "
            f"names={', '.join(prepared.subagent_tools.tool_names) or '<none>'}"
        )

        tool_progress_hook = self.run_hooks.make_tool_progress_hook(
            channel=None,
            external_chat_id=None,
            session_id=prepared.child_session_id,
            run_id=prepared.child_run_id,
            enabled=True,
        )
        tool_result_hook = self.run_hooks.make_tool_result_hook(
            channel=None,
            external_chat_id=None,
            session_id=prepared.child_session_id,
            run_id=prepared.child_run_id,
            enabled=True,
        )
        llm_status_hook = self.run_hooks.make_llm_status_hook(
            channel=None,
            external_chat_id=None,
            session_id=prepared.child_session_id,
            run_id=prepared.child_run_id,
            enabled=True,
        )
        llm_delta_hook = self.run_hooks.make_llm_delta_hook(
            channel=None,
            external_chat_id=None,
            session_id=prepared.child_session_id,
            run_id=prepared.child_run_id,
            enabled=True,
        )

        try:
            sub_result = await self._execute_messages(
                log_id,
                chat_messages,
                allow_tools=bool(prepared.subagent_tools.tool_names),
                provider_override=prepared.provider_override,
                tool_result_session_id=prepared.child_session_id,
                tool_registry=prepared.subagent_tools,
                on_tool_before_execute=tool_progress_hook,
                on_tool_after_execute=tool_result_hook,
                on_llm_status=llm_status_hook,
                on_response_delta=llm_delta_hook,
                max_tool_iterations=max_tool_iterations,
                should_cancel=should_cancel,
            )
            await self.run_trace.record_context_compaction_parts(
                prepared.child_session_id,
                prepared.child_run_id,
                sub_result.context_compaction_events,
            )
            await self.run_trace.record_llm_step_parts(
                prepared.child_session_id,
                prepared.child_run_id,
                sub_result.llm_step_events,
            )
            display_content, structured_output, parse_error = parse_structured_subagent_output(
                sub_result.content,
                prompt_type=prepared.prompt_type,
            )
            compact_structured_output = self._compact_structured_output(structured_output)
            result_summary = self._format_log_preview(
                (compact_structured_output or {}).get(STRUCTURED_SUBAGENT_SUMMARY_FIELD) or display_content,
                max_chars=240,
            )
            result_metadata = {
                "kind": "subagent_result",
                "task_id": prepared.task_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "prompt_type": prepared.prompt_type,
                "run_id": prepared.child_run_id,
                "summary": result_summary,
                "max_tool_iterations": max_tool_iterations,
                **self._delegation_metadata(prepared),
            }
            if compact_structured_output is not None:
                result_metadata["structured_output"] = compact_structured_output
            if parse_error is not None:
                result_metadata["structured_output_parse_error"] = parse_error
            await self.run_trace.record_assistant_message_part(
                prepared.child_session_id,
                prepared.child_run_id,
                display_content,
                metadata={
                    **result_metadata,
                    "response_len": len(display_content or ""),
                    "executed_tool_calls": sub_result.executed_tool_calls,
                    "had_tool_error": sub_result.had_tool_error,
                    "verification_attempted": sub_result.verification_attempted,
                    "verification_passed": sub_result.verification_passed,
                },
            )
            await self._save_message(
                prepared.child_session_id,
                "assistant",
                display_content,
                None,
                result_metadata,
            )
            completion_payload = {
                "status": WORKFLOW_COMPLETED_STATUS,
                "task_id": prepared.task_id,
                "prompt_type": prepared.prompt_type,
                "child_session_id": prepared.child_session_id,
                "child_run_id": prepared.child_run_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "resume": prepared.is_resume,
                "summary": result_summary,
                "executed_tool_calls": sub_result.executed_tool_calls,
                "had_tool_error": sub_result.had_tool_error,
                "verification_attempted": sub_result.verification_attempted,
                "verification_passed": sub_result.verification_passed,
                "max_tool_iterations": max_tool_iterations,
                **self._delegation_metadata(prepared),
            }
            if compact_structured_output is not None:
                completion_payload["structured_output"] = compact_structured_output
            if parse_error is not None:
                completion_payload["structured_output_parse_error"] = parse_error
            await self.run_trace.complete_run(
                prepared.child_session_id,
                prepared.child_run_id,
                event_payload=completion_payload,
                status_metadata=completion_payload,
            )
            self._record_task_update(
                prepared,
                status=WORKFLOW_COMPLETED_STATUS,
                summary=result_summary,
                structured_output=compact_structured_output,
                created_at=started_at,
                updated_at=time.time(),
            )
            await self._emit_parent_event(
                parent_session_id=prepared.parent_session_id,
                parent_run_id=prepared.parent_run_id,
                event_type=SUBAGENT_COMPLETED_EVENT,
                payload=completion_payload,
            )
            return SubagentTaskOutcome(
                task_id=prepared.task_id,
                prompt_type=prepared.prompt_type,
                child_session_id=prepared.child_session_id,
                child_run_id=prepared.child_run_id,
                status=WORKFLOW_COMPLETED_STATUS,
                content=display_content,
                summary=result_summary,
                executed_tool_calls=sub_result.executed_tool_calls,
                had_tool_error=sub_result.had_tool_error,
                verification_attempted=sub_result.verification_attempted,
                verification_passed=sub_result.verification_passed,
                is_resume=prepared.is_resume,
                structured_output=compact_structured_output,
                group_id=prepared.group_id,
                group_index=prepared.group_index,
                group_total=prepared.group_total,
            )
        except asyncio.CancelledError:
            cancellation_payload = {
                "status": WORKFLOW_CANCELLED_STATUS,
                "task_id": prepared.task_id,
                "prompt_type": prepared.prompt_type,
                "child_session_id": prepared.child_session_id,
                "child_run_id": prepared.child_run_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "resume": prepared.is_resume,
                "error": WORKFLOW_CANCELLED_STATUS,
                "max_tool_iterations": max_tool_iterations,
                **self._delegation_metadata(prepared),
            }
            await self.run_trace.fail_run(
                prepared.child_session_id,
                prepared.child_run_id,
                status=WORKFLOW_CANCELLED_STATUS,
                event_payload=cancellation_payload,
            )
            self._record_task_update(
                prepared,
                status=WORKFLOW_CANCELLED_STATUS,
                error=WORKFLOW_CANCELLED_STATUS,
                created_at=started_at,
                updated_at=time.time(),
            )
            await self._emit_parent_event(
                parent_session_id=prepared.parent_session_id,
                parent_run_id=prepared.parent_run_id,
                event_type=SUBAGENT_CANCELLED_EVENT,
                payload=cancellation_payload,
            )
            raise
        except Exception as exc:
            error_preview = self._format_log_preview(str(exc), max_chars=240)
            failure_payload = {
                "status": WORKFLOW_FAILED_STATUS,
                "task_id": prepared.task_id,
                "prompt_type": prepared.prompt_type,
                "child_session_id": prepared.child_session_id,
                "child_run_id": prepared.child_run_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "resume": prepared.is_resume,
                "error": error_preview,
                "max_tool_iterations": max_tool_iterations,
                **self._delegation_metadata(prepared),
            }
            await self.run_trace.fail_run(
                prepared.child_session_id,
                prepared.child_run_id,
                status=WORKFLOW_FAILED_STATUS,
                event_payload=failure_payload,
            )
            self._record_task_update(
                prepared,
                status=WORKFLOW_FAILED_STATUS,
                error=error_preview,
                created_at=started_at,
                updated_at=time.time(),
            )
            await self._emit_parent_event(
                parent_session_id=prepared.parent_session_id,
                parent_run_id=prepared.parent_run_id,
                event_type=SUBAGENT_FAILED_EVENT,
                payload=failure_payload,
            )
            if raise_on_failure:
                raise
            return SubagentTaskOutcome(
                task_id=prepared.task_id,
                prompt_type=prepared.prompt_type,
                child_session_id=prepared.child_session_id,
                child_run_id=prepared.child_run_id,
                status=WORKFLOW_FAILED_STATUS,
                error=error_preview,
                summary=error_preview,
                is_resume=prepared.is_resume,
                group_id=prepared.group_id,
                group_index=prepared.group_index,
                group_total=prepared.group_total,
            )

    def _format_parallel_results(
        self,
        outcomes: list[SubagentTaskOutcome],
        *,
        group_id: str,
        max_parallel: int,
    ) -> str:
        failed = sum(1 for outcome in outcomes if not is_workflow_completed_status(outcome.status))
        lines = [
            f"Parallel delegation completed: {len(outcomes)} task(s), {failed} failed.",
            f"Group ID: {group_id}",
            f"Max parallel: {max_parallel}",
        ]
        for index, outcome in enumerate(outcomes, start=1):
            lines.extend(
                [
                    "",
                    f"[{index}] {outcome.prompt_type} | {outcome.task_id} | {outcome.status}",
                    f"Run ID: {outcome.child_run_id}",
                ]
            )
            if is_workflow_completed_status(outcome.status):
                lines.extend(["Result:", outcome.content])
            else:
                lines.extend(["Failure:", outcome.error or outcome.summary or "unknown failure"])
        return "\n".join(lines)

    async def run_task(
        self,
        task: str,
        prompt_type: str,
        *,
        should_cancel: Callable[[], bool] | None = None,
        raise_on_failure: bool = True,
    ) -> SubagentTaskOutcome:
        """Run one child task and return the structured outcome for workflow orchestration."""
        prepared = await self._prepare_task(task, prompt_type=prompt_type)
        if isinstance(prepared, str):
            raise ValueError(_subagent_preparation_error_detail(prepared))
        return await self._execute_prepared_task(
            prepared,
            should_cancel=should_cancel or (lambda: self._cancel_requested(prepared.parent_session_id, prepared.parent_run_id)),
            raise_on_failure=raise_on_failure,
        )

    async def run(
        self,
        task: str,
        prompt_type: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Run or resume a delegated subagent task through a child storage session."""
        if task_id is not None:
            prepared = await self._prepare_task(task, prompt_type=prompt_type, task_id=task_id)
            if isinstance(prepared, str):
                return prepared
            outcome = await self._execute_prepared_task(
                prepared,
                should_cancel=lambda: self._cancel_requested(prepared.parent_session_id, prepared.parent_run_id),
                raise_on_failure=True,
            )
        else:
            try:
                outcome = await self.run_task(
                    task,
                    str(prompt_type or "").strip() or "writer",
                )
            except ValueError as exc:
                return _subagent_error_result(str(exc), category="subagent_execution_error")
        return (
            f"{subagent_result_line(SUBAGENT_TASK_ID_LABEL, outcome.task_id)}\n"
            f"{subagent_result_line(SUBAGENT_PROMPT_TYPE_LABEL, outcome.prompt_type)}\n\n"
            f"Result:\n{outcome.content}"
        )

    async def run_many(self, tasks: list[dict[str, Any]], max_parallel: int | None = None) -> str:
        """Run multiple safe read-only or research child tasks concurrently."""
        if not isinstance(tasks, list):
            return _subagent_validation_error(
                "tasks must be an array of {task, prompt_type} objects.",
                tool_name=DELEGATE_MANY_TOOL_NAME,
            )
        if not tasks:
            return _subagent_validation_error(
                "tasks must contain at least one child task.",
                tool_name=DELEGATE_MANY_TOOL_NAME,
            )
        if len(tasks) > MAX_PARALLEL_SUBAGENTS:
            return _subagent_validation_error(
                f"delegate_many supports at most {MAX_PARALLEL_SUBAGENTS} tasks.",
                tool_name=DELEGATE_MANY_TOOL_NAME,
            )

        group_id = self._new_group_id()
        prepared_tasks: list[PreparedSubagentTask] = []
        total = len(tasks)
        for index, item in enumerate(tasks, start=1):
            if not isinstance(item, dict):
                return _subagent_validation_error(
                    f"task[{index}] must be an object with task and prompt_type.",
                    tool_name=DELEGATE_MANY_TOOL_NAME,
                )
            task_text = str(item.get("task") or "").strip()
            prompt_type = str(item.get("prompt_type") or item.get("promptType") or "").strip()
            if not prompt_type:
                return _subagent_validation_error(
                    f"task[{index}] prompt_type is required for parallel delegation.",
                    tool_name=DELEGATE_MANY_TOOL_NAME,
                )
            prepared = await self._prepare_task(
                task_text,
                prompt_type=prompt_type,
                group_id=group_id,
                group_index=index,
                group_total=total,
            )
            if isinstance(prepared, str):
                status = classify_tool_result_status(prepared)
                prefix = _subagent_preparation_error_detail(prepared)
                return _subagent_error_result(
                    f"task[{index}] {prefix}",
                    category=status.category or "subagent_preparation_failed",
                    error_type=status.error_type or "DelegateManyToolError",
                    invalid_arguments=status.invalid_arguments,
                    tool_name=DELEGATE_MANY_TOOL_NAME,
                )
            prepared_tasks.append(prepared)

        try:
            requested_parallel = int(max_parallel or DEFAULT_MAX_PARALLEL_SUBAGENTS)
        except (TypeError, ValueError):
            return _subagent_validation_error("max_parallel must be an integer.", tool_name=DELEGATE_MANY_TOOL_NAME)
        concurrency = max(1, min(requested_parallel, len(prepared_tasks), MAX_PARALLEL_SUBAGENTS))
        semaphore = asyncio.Semaphore(concurrency)
        parent_session_id = prepared_tasks[0].parent_session_id
        parent_run_id = prepared_tasks[0].parent_run_id

        if self._cancel_requested(parent_session_id, parent_run_id):
            raise RunCancelledError("parallel delegated tasks cancelled")

        await self._emit_parent_event(
            parent_session_id=parent_session_id,
            parent_run_id=parent_run_id,
            event_type=SUBAGENT_GROUP_STARTED_EVENT,
            payload=self._build_group_payload(
                prepared_tasks,
                group_id=group_id,
                max_parallel=concurrency,
                status=WORKFLOW_RUNNING_STATUS,
            ),
        )

        async def worker(prepared: PreparedSubagentTask) -> SubagentTaskOutcome:
            async with semaphore:
                return await self._execute_prepared_task(
                    prepared,
                    should_cancel=lambda: self._cancel_requested(parent_session_id, parent_run_id),
                    raise_on_failure=False,
                )

        task_map = {
            asyncio.create_task(worker(prepared)): prepared
            for prepared in prepared_tasks
        }
        pending = set(task_map)
        outcomes_by_task_id: dict[str, SubagentTaskOutcome] = {}

        while pending:
            done, pending = await asyncio.wait(pending, timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
            if self._cancel_requested(parent_session_id, parent_run_id):
                for task in pending:
                    task.cancel()
                for task in done | pending:
                    prepared = task_map[task]
                    try:
                        outcome = await task
                    except asyncio.CancelledError:
                        continue
                    except Exception as exc:  # pragma: no cover - defensive fallback
                        logger.warning(
                            "[%s] subagent.parallel.unexpected-error | task_id=%s error=%s",
                            parent_session_id,
                            prepared.task_id,
                            exc,
                        )
                    else:
                        outcomes_by_task_id[outcome.task_id] = outcome
                await self._emit_parent_event(
                    parent_session_id=parent_session_id,
                    parent_run_id=parent_run_id,
                    event_type=SUBAGENT_GROUP_CANCELLED_EVENT,
                    payload=self._build_group_payload(
                        prepared_tasks,
                        group_id=group_id,
                        max_parallel=concurrency,
                        status=WORKFLOW_CANCELLED_STATUS,
                        outcomes_by_task_id=outcomes_by_task_id,
                        default_missing_status=WORKFLOW_CANCELLED_STATUS,
                    ),
                )
                raise RunCancelledError("parallel delegated tasks cancelled")

            for task in done:
                prepared = task_map[task]
                try:
                    outcome = task.result()
                except asyncio.CancelledError:
                    raise RunCancelledError("parallel delegated tasks cancelled") from None
                except Exception as exc:  # pragma: no cover - defensive fallback
                    error_preview = self._format_log_preview(f"{type(exc).__name__}: {exc}", max_chars=240)
                    outcomes_by_task_id[prepared.task_id] = SubagentTaskOutcome(
                        task_id=prepared.task_id,
                        prompt_type=prepared.prompt_type,
                        child_session_id=prepared.child_session_id,
                        child_run_id=prepared.child_run_id,
                        status=WORKFLOW_FAILED_STATUS,
                        error=error_preview,
                        summary=error_preview,
                        is_resume=prepared.is_resume,
                        group_id=prepared.group_id,
                        group_index=prepared.group_index,
                        group_total=prepared.group_total,
                    )
                else:
                    outcomes_by_task_id[outcome.task_id] = outcome

        ordered_outcomes = [
            outcomes_by_task_id[prepared.task_id]
            for prepared in prepared_tasks
            if prepared.task_id in outcomes_by_task_id
        ]
        any_failed = any(is_workflow_failed_status(outcome.status) for outcome in ordered_outcomes)
        await self._emit_parent_event(
            parent_session_id=parent_session_id,
            parent_run_id=parent_run_id,
            event_type=SUBAGENT_GROUP_FAILED_EVENT if any_failed else SUBAGENT_GROUP_COMPLETED_EVENT,
            payload=self._build_group_payload(
                prepared_tasks,
                group_id=group_id,
                max_parallel=concurrency,
                status=WORKFLOW_FAILED_STATUS if any_failed else WORKFLOW_COMPLETED_STATUS,
                outcomes_by_task_id=outcomes_by_task_id,
            ),
        )
        return self._format_parallel_results(ordered_outcomes, group_id=group_id, max_parallel=concurrency)

WORKFLOW_COMPLETED_STATUS = "completed"
WORKFLOW_FAILED_STATUS = "failed"
WORKFLOW_ERROR_STATUS = "error"
WORKFLOW_CANCELLED_STATUS = "cancelled"
WORKFLOW_RUNNING_STATUS = "running"
WORKFLOW_FAILURE_STATUSES = frozenset({WORKFLOW_FAILED_STATUS, WORKFLOW_ERROR_STATUS})
WORKFLOW_UNSUCCESSFUL_STATUSES = WORKFLOW_FAILURE_STATUSES | frozenset({WORKFLOW_CANCELLED_STATUS})


def is_workflow_running_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status is running."""
    return str(status or "").strip().lower() == WORKFLOW_RUNNING_STATUS


def is_workflow_completed_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status is completed."""
    return str(status or "").strip().lower() == WORKFLOW_COMPLETED_STATUS


def is_workflow_failed_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status represents failure."""
    return str(status or "").strip().lower() in WORKFLOW_FAILURE_STATUSES


def is_workflow_cancelled_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status represents cancellation."""
    return str(status or "").strip().lower() == WORKFLOW_CANCELLED_STATUS


def is_workflow_unsuccessful_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status is failed, errored, or cancelled."""
    return str(status or "").strip().lower() in WORKFLOW_UNSUCCESSFUL_STATUSES

IMPLEMENT_THEN_REVIEW_WORKFLOW_ID = "implement_then_review"
RESEARCH_THEN_OUTLINE_WORKFLOW_ID = "research_then_outline"
BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID = "bugfix_then_test_then_review"
REVIEW_WORKFLOW_IDS = frozenset(
    {
        IMPLEMENT_THEN_REVIEW_WORKFLOW_ID,
        BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID,
    }
)
WORKFLOW_ID_FIELD = "workflow"
WORKFLOW_STATUS_FIELD = "status"
WORKFLOW_SUMMARY_FIELD = "summary"
WORKFLOW_ERROR_FIELD = "error"
WORKFLOW_NEXT_STEP_ID_FIELD = "next_step_id"
WORKFLOW_NEXT_STEP_LABEL_FIELD = "next_step_label"
WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD = "next_step_prompt_type"
WORKFLOW_LAST_COMPLETED_STEP_ID_FIELD = "last_completed_step_id"
WORKFLOW_LAST_COMPLETED_STEP_LABEL_FIELD = "last_completed_step_label"
WORKFLOW_LAST_COMPLETED_PROMPT_TYPE_FIELD = "last_completed_prompt_type"
WORKFLOW_REVIEW_ATTEMPTED_FIELD = "review_attempted"
WORKFLOW_REVIEW_PASSED_FIELD = "review_passed"
WORKFLOW_REVIEW_FINDING_COUNT_FIELD = "review_finding_count"
WORKFLOW_REVIEW_SUMMARY_FIELD = "review_summary"
WORKFLOW_REVIEW_FIRST_FINDING_FIELD = "review_first_finding"
WORKFLOW_VERIFICATION_ATTEMPTED_FIELD = "verification_attempted"
WORKFLOW_VERIFICATION_PASSED_FIELD = "verification_passed"


@dataclass(frozen=True)
class WorkflowStepSpec:
    """One fixed child-step inside a workflow."""

    step_id: str
    label: str
    prompt_type: str
    task_builder: Callable[[str, list[SubagentTaskOutcome]], str]
    resume_task_builder: Callable[[str], str] | None = None


@dataclass(frozen=True)
class WorkflowSpec:
    """One supported multi-step orchestration workflow."""

    workflow_id: str
    description: str
    steps: tuple[WorkflowStepSpec, ...]


def _workflow_error_result(
    message: str,
    *,
    category: str,
    error_type: str = "RunWorkflowToolError",
    invalid_arguments: bool = False,
) -> str:
    error = str(message or "").strip()
    return tool_error_result(
        error,
        error_type=error_type,
        category=category,
        repeated_error_key=error if invalid_arguments else None,
        invalid_arguments=invalid_arguments,
        metadata={"tool_name": RUN_WORKFLOW_TOOL_NAME},
    )


def _workflow_validation_error(message: str, *, category: str = "invalid_arguments") -> str:
    return _workflow_error_result(
        message,
        category=category,
        error_type="ToolValidationError",
        invalid_arguments=True,
    )


def _result_summary(outcome: SubagentTaskOutcome) -> str:
    if outcome.summary:
        return outcome.summary
    if outcome.error:
        return outcome.error
    return outcome.content


def format_review_finding(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    path = str(item.get("path") or "").strip()
    fix = str(item.get("fix") or "").strip()
    why = str(item.get("why") or "").strip()
    subject = f"{path}: {title}" if path and title else title or path
    if fix:
        return f"{subject}: {fix}" if subject else fix
    if why:
        return f"{subject}: {why}" if subject else why
    return subject


def first_structured_review_finding(structured_output: dict[str, Any] | None) -> str:
    sections = structured_output.get(STRUCTURED_SUBAGENT_SECTIONS_FIELD) if isinstance(structured_output, dict) else None
    if not isinstance(sections, list):
        return ""
    for section in sections:
        if not isinstance(section, dict):
            continue
        items = section.get(STRUCTURED_SUBAGENT_ITEMS_FIELD)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                detail = format_review_finding(item)
                if detail:
                    return detail
            elif isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _workflow_progress_fields(
    steps: tuple[WorkflowStepSpec, ...],
    outcomes: list[SubagentTaskOutcome],
    *,
    status: str,
    start_index: int = 0,
) -> dict[str, Any]:
    completed_prefix = start_index
    for outcome in outcomes[: len(steps)]:
        if not is_workflow_completed_status(outcome.status):
            break
        completed_prefix += 1

    payload: dict[str, Any] = {}
    if completed_prefix > 0:
        last_completed = steps[completed_prefix - 1]
        payload.update(
            {
                WORKFLOW_LAST_COMPLETED_STEP_ID_FIELD: last_completed.step_id,
                WORKFLOW_LAST_COMPLETED_STEP_LABEL_FIELD: last_completed.label,
                WORKFLOW_LAST_COMPLETED_PROMPT_TYPE_FIELD: last_completed.prompt_type,
            }
        )
    if not is_workflow_completed_status(status) and completed_prefix < len(steps):
        next_step = steps[completed_prefix]
        payload.update(
            {
                WORKFLOW_NEXT_STEP_ID_FIELD: next_step.step_id,
                WORKFLOW_NEXT_STEP_LABEL_FIELD: next_step.label,
                WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD: next_step.prompt_type,
            }
        )
    return payload


def _completed_outcome_count(outcomes: list[SubagentTaskOutcome]) -> int:
    return sum(1 for outcome in outcomes if is_workflow_completed_status(outcome.status))


def _failed_outcome_count(outcomes: list[SubagentTaskOutcome]) -> int:
    return sum(1 for outcome in outcomes if is_workflow_failed_status(outcome.status))


def _unsuccessful_outcome_count(outcomes: list[SubagentTaskOutcome]) -> int:
    return sum(1 for outcome in outcomes if is_workflow_unsuccessful_status(outcome.status))


def _resolve_start_index(spec: WorkflowSpec, start_step: str | None) -> tuple[int, WorkflowStepSpec | None, str | None]:
    normalized = str(start_step or "").strip()
    if not normalized:
        return 0, None, None
    for index, step in enumerate(spec.steps):
        if step.step_id == normalized:
            return index, step, None
    available = ", ".join(step.step_id for step in spec.steps)
    return 0, None, _workflow_validation_error(
        f"unknown start_step '{normalized}' for workflow '{spec.workflow_id}'. Available: {available}",
        category="unknown_start_step",
    )


def _build_step_task(
    step: WorkflowStepSpec,
    *,
    task_text: str,
    outcomes: list[SubagentTaskOutcome],
    resumed: bool,
) -> str:
    if resumed and not outcomes:
        builder = step.resume_task_builder
        if builder is None:
            return step.task_builder(task_text, outcomes).strip()
        return builder(task_text).strip()
    return step.task_builder(task_text, outcomes).strip()


def _implement_review_steps() -> tuple[WorkflowStepSpec, ...]:
    return (
        WorkflowStepSpec(
            step_id="implement",
            label="Implement",
            prompt_type="implementer",
            task_builder=lambda task, _: task,
            resume_task_builder=lambda task: task,
        ),
        WorkflowStepSpec(
            step_id="review",
            label="Code review",
            prompt_type=CODE_REVIEWER_PROMPT_TYPE,
            task_builder=lambda task, results: (
                "Review the current workspace changes for correctness, regressions, and missing tests. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Implementation result:\n{_result_summary(results[0])}"
            ),
            resume_task_builder=lambda task: (
                "Resume the code review step for the current workspace changes. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
    )


def _research_outline_steps() -> tuple[WorkflowStepSpec, ...]:
    return (
        WorkflowStepSpec(
            step_id="research",
            label="Research",
            prompt_type="researcher",
            task_builder=lambda task, _: task,
            resume_task_builder=lambda task: task,
        ),
        WorkflowStepSpec(
            step_id="outline",
            label="Outline",
            prompt_type="outliner",
            task_builder=lambda task, results: (
                "Create a clear outline based on the research summary below.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Research summary:\n{results[0].content}"
            ),
            resume_task_builder=lambda task: (
                "Resume the outline step for the original objective below. "
                "Use any already gathered research context available in the current session or workspace, "
                "and clearly state missing inputs if the research context is insufficient.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
    )


def _bugfix_test_review_steps() -> tuple[WorkflowStepSpec, ...]:
    return (
        WorkflowStepSpec(
            step_id="bugfix",
            label="Bug fix",
            prompt_type="bug-fixer",
            task_builder=lambda task, _: task,
            resume_task_builder=lambda task: task,
        ),
        WorkflowStepSpec(
            step_id="tests",
            label="Tests",
            prompt_type="test-writer",
            task_builder=lambda task, results: (
                "Add the minimal effective tests for the bug fix below. Inspect the current workspace changes first.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Bug-fix result:\n{_result_summary(results[0])}"
            ),
            resume_task_builder=lambda task: (
                "Resume the tests step for the current workspace changes related to the bug fix below. "
                "Inspect the actual files first and add the minimal effective tests.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
        WorkflowStepSpec(
            step_id="review",
            label="Code review",
            prompt_type=CODE_REVIEWER_PROMPT_TYPE,
            task_builder=lambda task, results: (
                "Review the current workspace changes after the bug fix and test additions. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Bug-fix result:\n{_result_summary(results[0])}\n\n"
                f"Test result:\n{_result_summary(results[1])}"
            ),
            resume_task_builder=lambda task: (
                "Resume the code review step for the current workspace changes after the bug fix and test additions. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
    )


WORKFLOW_SPECS: dict[str, WorkflowSpec] = {
    IMPLEMENT_THEN_REVIEW_WORKFLOW_ID: WorkflowSpec(
        workflow_id=IMPLEMENT_THEN_REVIEW_WORKFLOW_ID,
        description="Run implementer first, then inspect the workspace with code-reviewer.",
        steps=_implement_review_steps(),
    ),
    RESEARCH_THEN_OUTLINE_WORKFLOW_ID: WorkflowSpec(
        workflow_id=RESEARCH_THEN_OUTLINE_WORKFLOW_ID,
        description="Gather research context first, then turn it into an outline.",
        steps=_research_outline_steps(),
    ),
    BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID: WorkflowSpec(
        workflow_id=BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID,
        description="Fix the bug, add focused tests, then run a code review pass.",
        steps=_bugfix_test_review_steps(),
    ),
}


class SubagentWorkflowService:
    """Runs fixed orchestration workflows on top of delegated subagents."""

    def __init__(
        self,
        *,
        current_session_id_getter: Callable[[], str | None],
        current_run_id_getter: Callable[[], str | None],
        current_channel_getter: Callable[[], str | None],
        current_external_chat_id_getter: Callable[[], str | None],
        run_subagent_task: Callable[[str, str], Awaitable[SubagentTaskOutcome]],
        emit_run_event: Callable[..., Awaitable[None]],
        format_log_preview: Callable[..., str],
        record_workflow_outcome: Callable[[str | None, dict[str, Any]], None],
    ):
        self._current_session_id_getter = current_session_id_getter
        self._current_run_id_getter = current_run_id_getter
        self._current_channel_getter = current_channel_getter
        self._current_external_chat_id_getter = current_external_chat_id_getter
        self._run_subagent_task = run_subagent_task
        self._emit_run_event = emit_run_event
        self._format_log_preview = format_log_preview
        self._record_workflow_outcome = record_workflow_outcome

    @staticmethod
    def catalog() -> dict[str, str]:
        """Return workflow ids and user-facing descriptions."""
        return {workflow_id: spec.description for workflow_id, spec in WORKFLOW_SPECS.items()}

    @staticmethod
    def _new_workflow_run_id() -> str:
        return f"workflow_{uuid4().hex[:12]}"

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        session_id = self._current_session_id_getter()
        run_id = self._current_run_id_getter()
        if session_id is None or run_id is None:
            return
        await self._emit_run_event(
            session_id,
            run_id,
            event_type,
            payload,
            channel=self._current_channel_getter(),
            external_chat_id=self._current_external_chat_id_getter(),
        )

    @staticmethod
    def _step_payload(
        *,
        workflow_run_id: str,
        workflow_id: str,
        spec: WorkflowStepSpec,
        step_index: int,
        total_steps: int,
        outcome: SubagentTaskOutcome | None = None,
        task_preview: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        payload = {
            "workflow_run_id": workflow_run_id,
            WORKFLOW_ID_FIELD: workflow_id,
            "step_id": spec.step_id,
            "label": spec.label,
            "prompt_type": spec.prompt_type,
            "step_index": step_index,
            "total_steps": total_steps,
            "task_preview": task_preview,
        }
        if outcome is not None:
            payload.update(
                {
                    WORKFLOW_STATUS_FIELD: outcome.status,
                    "task_id": outcome.task_id,
                    "child_session_id": outcome.child_session_id,
                    "child_run_id": outcome.child_run_id,
                    WORKFLOW_SUMMARY_FIELD: outcome.summary,
                    WORKFLOW_ERROR_FIELD: outcome.error,
                }
            )
            if outcome.structured_output is not None:
                payload["structured_output"] = {
                    STRUCTURED_SUBAGENT_STATUS_FIELD: outcome.structured_output.get(STRUCTURED_SUBAGENT_STATUS_FIELD),
                    STRUCTURED_SUBAGENT_SUMMARY_FIELD: outcome.structured_output.get(STRUCTURED_SUBAGENT_SUMMARY_FIELD),
                    STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD: outcome.structured_output.get(STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD, 0),
                    STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD: outcome.structured_output.get(STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD, 0),
                    STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD: outcome.structured_output.get(STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD, 0),
                }
        if error:
            payload[WORKFLOW_ERROR_FIELD] = error
        return payload

    @staticmethod
    def _workflow_payload(
        *,
        workflow_run_id: str,
        workflow_id: str,
        task_preview: str,
        steps: tuple[WorkflowStepSpec, ...],
        outcomes: list[SubagentTaskOutcome],
        status: str,
        start_index: int = 0,
        error: str = "",
    ) -> dict[str, Any]:
        completed_steps = start_index + _completed_outcome_count(outcomes)
        failed_steps = _failed_outcome_count(outcomes)
        summary = (
            f"Completed {completed_steps}/{len(steps)} workflow step(s)."
            if is_workflow_completed_status(status)
            else f"Workflow stopped after {completed_steps}/{len(steps)} completed step(s)."
        )
        payload = {
            "workflow_run_id": workflow_run_id,
            WORKFLOW_ID_FIELD: workflow_id,
            WORKFLOW_STATUS_FIELD: status,
            "task_preview": task_preview,
            "total_steps": len(steps),
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            WORKFLOW_SUMMARY_FIELD: summary,
            "steps": [
                {
                    "step_id": spec.step_id,
                    "label": spec.label,
                    "prompt_type": spec.prompt_type,
                    WORKFLOW_STATUS_FIELD: outcome.status,
                    "task_id": outcome.task_id,
                    "child_session_id": outcome.child_session_id,
                    "child_run_id": outcome.child_run_id,
                    WORKFLOW_SUMMARY_FIELD: outcome.summary,
                    WORKFLOW_ERROR_FIELD: outcome.error,
                }
                for spec, outcome in zip(steps[start_index:], outcomes)
            ],
            **_workflow_progress_fields(steps, outcomes, status=status, start_index=start_index),
        }
        if start_index > 0:
            start_step = steps[start_index]
            payload.update(
                {
                    "resumed": True,
                    "start_step_id": start_step.step_id,
                    "start_step_label": start_step.label,
                }
            )
        if error:
            payload[WORKFLOW_ERROR_FIELD] = error
        return payload

    @staticmethod
    def _format_result(workflow_id: str, outcomes: list[SubagentTaskOutcome], *, status: str, start_index: int = 0) -> str:
        lines = [
            f"Workflow: {workflow_id}",
            f"Status: {status}",
        ]
        if start_index > 0:
            lines.append(f"Resumed from step: {start_index + 1}")
        for index, outcome in enumerate(outcomes, start=start_index + 1):
            lines.extend(
                [
                    "",
                    f"[{index}] {outcome.prompt_type} | {outcome.status}",
                    subagent_result_line(SUBAGENT_TASK_ID_LABEL, outcome.task_id),
                    f"Run ID: {outcome.child_run_id}",
                ]
            )
            if outcome.summary:
                lines.append(f"Summary: {outcome.summary}")
            if outcome.error:
                lines.append(f"Failure: {outcome.error}")
            if outcome.content:
                lines.extend(["Result:", outcome.content])
        return "\n".join(lines)

    @staticmethod
    def _review_outcome(outcomes: list[SubagentTaskOutcome]) -> dict[str, Any]:
        review_outcomes = [
            outcome
            for outcome in outcomes
            if outcome.prompt_type in REVIEW_PROMPT_TYPES
        ]
        finding_count = sum(
            int((outcome.structured_output or {}).get(STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD) or 0)
            for outcome in review_outcomes
        )
        attempted = any(is_workflow_completed_status(outcome.status) for outcome in review_outcomes)
        passed = False
        summary = ""
        first_finding = ""
        for outcome in review_outcomes:
            if outcome.summary and not summary:
                summary = outcome.summary
            if not first_finding:
                first_finding = first_structured_review_finding(outcome.structured_output)
            if not is_workflow_completed_status(outcome.status):
                continue
            structured = outcome.structured_output or {}
            if is_clean_structured_subagent_status(structured.get(STRUCTURED_SUBAGENT_STATUS_FIELD)) and int(structured.get(STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD) or 0) == 0:
                passed = True
                continue
        return {
            WORKFLOW_REVIEW_ATTEMPTED_FIELD: attempted,
            WORKFLOW_REVIEW_PASSED_FIELD: attempted and passed and finding_count == 0,
            WORKFLOW_REVIEW_FINDING_COUNT_FIELD: finding_count,
            WORKFLOW_REVIEW_SUMMARY_FIELD: summary,
            WORKFLOW_REVIEW_FIRST_FINDING_FIELD: first_finding,
        }

    @staticmethod
    def _verification_outcome(outcomes: list[SubagentTaskOutcome]) -> dict[str, Any]:
        attempted = any(outcome.verification_attempted for outcome in outcomes)
        passed = any(outcome.verification_passed for outcome in outcomes)
        return {
            WORKFLOW_VERIFICATION_ATTEMPTED_FIELD: attempted,
            WORKFLOW_VERIFICATION_PASSED_FIELD: passed,
        }

    def _build_workflow_outcome(
        self,
        *,
        workflow_run_id: str,
        spec: WorkflowSpec,
        task_preview: str,
        outcomes: list[SubagentTaskOutcome],
        status: str,
        start_index: int = 0,
        error: str = "",
    ) -> dict[str, Any]:
        review = self._review_outcome(outcomes)
        verification = self._verification_outcome(outcomes)
        completed_steps = start_index + _completed_outcome_count(outcomes)
        return {
            "workflow_run_id": workflow_run_id,
            WORKFLOW_ID_FIELD: spec.workflow_id,
            WORKFLOW_STATUS_FIELD: status,
            "task_preview": task_preview,
            "total_steps": len(spec.steps),
            "completed_steps": completed_steps,
            "failed_steps": _unsuccessful_outcome_count(outcomes),
            WORKFLOW_SUMMARY_FIELD: (
                f"Completed {completed_steps}/{len(spec.steps)} workflow step(s)."
                if is_workflow_completed_status(status)
                else f"Workflow stopped after {completed_steps}/{len(spec.steps)} completed step(s)."
            ),
            WORKFLOW_REVIEW_ATTEMPTED_FIELD: review[WORKFLOW_REVIEW_ATTEMPTED_FIELD],
            WORKFLOW_REVIEW_PASSED_FIELD: review[WORKFLOW_REVIEW_PASSED_FIELD],
            WORKFLOW_REVIEW_FINDING_COUNT_FIELD: review[WORKFLOW_REVIEW_FINDING_COUNT_FIELD],
            WORKFLOW_REVIEW_SUMMARY_FIELD: review[WORKFLOW_REVIEW_SUMMARY_FIELD],
            WORKFLOW_REVIEW_FIRST_FINDING_FIELD: review[WORKFLOW_REVIEW_FIRST_FINDING_FIELD],
            WORKFLOW_VERIFICATION_ATTEMPTED_FIELD: verification[WORKFLOW_VERIFICATION_ATTEMPTED_FIELD],
            WORKFLOW_VERIFICATION_PASSED_FIELD: verification[WORKFLOW_VERIFICATION_PASSED_FIELD],
            **_workflow_progress_fields(spec.steps, outcomes, status=status, start_index=start_index),
            **(
                {
                    "resumed": True,
                    "start_step_id": spec.steps[start_index].step_id,
                    "start_step_label": spec.steps[start_index].label,
                }
                if start_index > 0
                else {}
            ),
            **({WORKFLOW_ERROR_FIELD: error} if error else {}),
        }

    async def run(self, workflow_id: str, task: str) -> str:
        workflow_key = str(workflow_id or "").strip()
        spec = WORKFLOW_SPECS.get(workflow_key)
        if spec is None:
            available = ", ".join(sorted(WORKFLOW_SPECS))
            return _workflow_error_result(
                f"unknown workflow '{workflow_key}'. Available: {available}",
                category="unknown_workflow",
            )

        task_text = str(task or "").strip()
        if not task_text:
            return _workflow_validation_error("workflow task must be a non-empty string.")

        return await self.run_from_step(workflow_key, task_text)

    async def run_from_step(self, workflow_id: str, task: str, start_step: str | None = None) -> str:
        workflow_key = str(workflow_id or "").strip()
        spec = WORKFLOW_SPECS.get(workflow_key)
        if spec is None:
            available = ", ".join(sorted(WORKFLOW_SPECS))
            return _workflow_error_result(
                f"unknown workflow '{workflow_key}'. Available: {available}",
                category="unknown_workflow",
            )

        task_text = str(task or "").strip()
        if not task_text:
            return _workflow_validation_error("workflow task must be a non-empty string.")

        start_index, start_spec, start_error = _resolve_start_index(spec, start_step)
        if start_error:
            return start_error

        workflow_run_id = self._new_workflow_run_id()
        task_preview = self._format_log_preview(task_text, max_chars=240)
        start_step_summary = (
            f"Resumed workflow {spec.workflow_id} from step {start_spec.step_id} ({start_spec.label})."
            if start_spec is not None
            else f"Started workflow {spec.workflow_id} with {len(spec.steps)} step(s)."
        )
        await self._emit_event(
            WORKFLOW_STARTED_EVENT,
            {
                "workflow_run_id": workflow_run_id,
                WORKFLOW_ID_FIELD: spec.workflow_id,
                WORKFLOW_STATUS_FIELD: WORKFLOW_RUNNING_STATUS,
                "task_preview": task_preview,
                "total_steps": len(spec.steps),
                WORKFLOW_SUMMARY_FIELD: start_step_summary,
                **(
                    {
                        "resumed": True,
                        "start_step_id": start_spec.step_id,
                        "start_step_label": start_spec.label,
                        "start_step_prompt_type": start_spec.prompt_type,
                    }
                    if start_spec is not None
                    else {}
                ),
            },
        )

        outcomes: list[SubagentTaskOutcome] = []
        for index, step in enumerate(spec.steps[start_index:], start=start_index + 1):
            step_task = _build_step_task(
                step,
                task_text=task_text,
                outcomes=outcomes,
                resumed=start_spec is not None and index == start_index + 1,
            )
            step_preview = self._format_log_preview(step_task, max_chars=240)
            await self._emit_event(
                WORKFLOW_STEP_STARTED_EVENT,
                self._step_payload(
                    workflow_run_id=workflow_run_id,
                    workflow_id=spec.workflow_id,
                    spec=step,
                    step_index=index,
                    total_steps=len(spec.steps),
                    task_preview=step_preview,
                ),
            )
            try:
                outcome = await self._run_subagent_task(step_task, step.prompt_type)
            except RunCancelledError:
                self._record_workflow_outcome(
                    self._current_run_id_getter(),
                    self._build_workflow_outcome(
                        workflow_run_id=workflow_run_id,
                        spec=spec,
                        task_preview=task_preview,
                        outcomes=outcomes,
                        status=WORKFLOW_CANCELLED_STATUS,
                        start_index=start_index,
                        error=WORKFLOW_CANCELLED_STATUS,
                    ),
                )
                await self._emit_event(
                    WORKFLOW_FAILED_EVENT,
                    self._workflow_payload(
                        workflow_run_id=workflow_run_id,
                        workflow_id=spec.workflow_id,
                        task_preview=task_preview,
                        steps=spec.steps,
                        outcomes=outcomes,
                        status=WORKFLOW_CANCELLED_STATUS,
                        start_index=start_index,
                        error=WORKFLOW_CANCELLED_STATUS,
                    ),
                )
                raise
            except Exception as exc:  # pragma: no cover - defensive guard
                error_preview = self._format_log_preview(f"{type(exc).__name__}: {exc}", max_chars=240)
                logger.warning("workflow.run.failed | workflow={} step={} error={}", spec.workflow_id, step.step_id, error_preview)
                self._record_workflow_outcome(
                    self._current_run_id_getter(),
                    self._build_workflow_outcome(
                        workflow_run_id=workflow_run_id,
                        spec=spec,
                        task_preview=task_preview,
                        outcomes=outcomes,
                        status=WORKFLOW_FAILED_STATUS,
                        start_index=start_index,
                        error=error_preview,
                    ),
                )
                await self._emit_event(
                    WORKFLOW_STEP_FAILED_EVENT,
                    self._step_payload(
                        workflow_run_id=workflow_run_id,
                        workflow_id=spec.workflow_id,
                        spec=step,
                        step_index=index,
                        total_steps=len(spec.steps),
                        task_preview=step_preview,
                        error=error_preview,
                    ),
                )
                await self._emit_event(
                    WORKFLOW_FAILED_EVENT,
                    self._workflow_payload(
                        workflow_run_id=workflow_run_id,
                        workflow_id=spec.workflow_id,
                        task_preview=task_preview,
                        steps=spec.steps,
                        outcomes=outcomes,
                        status=WORKFLOW_FAILED_STATUS,
                        start_index=start_index,
                        error=error_preview,
                    ),
                )
                return _workflow_error_result(
                    f"workflow step '{step.step_id}' failed: {error_preview}",
                    category="workflow_step_failed",
                    error_type="WorkflowExecutionError",
                )

            outcomes.append(outcome)
            await self._emit_event(
                WORKFLOW_STEP_COMPLETED_EVENT,
                self._step_payload(
                    workflow_run_id=workflow_run_id,
                    workflow_id=spec.workflow_id,
                    spec=step,
                    step_index=index,
                    total_steps=len(spec.steps),
                    outcome=outcome,
                ),
            )

        await self._emit_event(
            WORKFLOW_COMPLETED_EVENT,
            self._workflow_payload(
                workflow_run_id=workflow_run_id,
                workflow_id=spec.workflow_id,
                task_preview=task_preview,
                steps=spec.steps,
                outcomes=outcomes,
                status=WORKFLOW_COMPLETED_STATUS,
                start_index=start_index,
            ),
        )
        self._record_workflow_outcome(
            self._current_run_id_getter(),
            self._build_workflow_outcome(
                workflow_run_id=workflow_run_id,
                spec=spec,
                task_preview=task_preview,
                outcomes=outcomes,
                status=WORKFLOW_COMPLETED_STATUS,
                start_index=start_index,
            ),
        )
        return self._format_result(spec.workflow_id, outcomes, status=WORKFLOW_COMPLETED_STATUS, start_index=start_index)
