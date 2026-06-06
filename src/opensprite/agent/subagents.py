"""Delegated subagent task runner for AgentLoop."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from typing import Any, Awaitable, Callable

from ..context.runtime import build_runtime_context
from ..llms import ChatMessage
from ..llms.routed import ModelRoutedProvider
from ..llms.registry import create_llm
from ..llms.runtime_provider import create_llm_from_runtime, resolve_provider_runtime
from ..config.llm_presets import provider_profile_defaults
from ..runs.events import (
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
from ..runs.lifecycle import RUN_STARTED_EVENT
from ..skills import SkillsLoader
from ..storage import StorageProvider, StoredMessage
from ..storage.base import StoredDelegatedTask
from ..tool_names import DELEGATE_MANY_TOOL_NAME, DELEGATE_TOOL_NAME, RUN_WORKFLOW_TOOL_NAME
from .subagent_profiles import (
    CODE_REVIEWER_PROMPT_TYPE,
    PARALLEL_SAFE_PROFILE_NAMES,
    READONLY_SUBAGENT_RESULT_CONTRACT,
    REVIEW_PROMPT_TYPES,
    STRUCTURED_SUBAGENT_CONTRACT_FIELD,
    STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD,
    STRUCTURED_SUBAGENT_ITEM_COUNT_FIELD,
    STRUCTURED_SUBAGENT_ITEMS_FIELD,
    STRUCTURED_SUBAGENT_PROMPT_TYPE_FIELD,
    STRUCTURED_SUBAGENT_QUESTION_COUNT_FIELD,
    STRUCTURED_SUBAGENT_QUESTIONS_FIELD,
    STRUCTURED_SUBAGENT_RESIDUAL_RISK_COUNT_FIELD,
    STRUCTURED_SUBAGENT_RESIDUAL_RISKS_FIELD,
    STRUCTURED_SUBAGENT_SCHEMA_VERSION_FIELD,
    STRUCTURED_SUBAGENT_SECTION_COUNT_FIELD,
    STRUCTURED_SUBAGENT_SECTIONS_FIELD,
    STRUCTURED_SUBAGENT_SOURCES_FIELD,
    STRUCTURED_SUBAGENT_STATUS_FIELD,
    STRUCTURED_SUBAGENT_SUMMARY_FIELD,
    STRUCTURED_SUBAGENT_TRUNCATED_FIELD,
    SUBAGENT_PROMPT_TYPE_LABEL,
    SUBAGENT_TASK_ID_LABEL,
    build_subagent_tool_registry,
    build_structured_subagent_contract_instructions,
    is_clean_structured_subagent_status,
    parse_structured_subagent_output,
    profile_for_subagent,
    subagent_result_line,
)
from ..subagent_prompts import get_all_subagents, load_metadata, load_prompt
from ..tools import ToolRegistry
from ..tools.result_status import classify_tool_result_status, tool_error_result
from ..utils.log import logger
from .run_trace import RunCancelledError, RunHookService
from .run_trace import RunTraceRecorder
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


def _format_review_finding(item: dict[str, Any]) -> str:
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


def _first_structured_review_finding(structured_output: dict[str, Any] | None) -> str:
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
                detail = _format_review_finding(item)
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
                first_finding = _first_structured_review_finding(outcome.structured_output)
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
