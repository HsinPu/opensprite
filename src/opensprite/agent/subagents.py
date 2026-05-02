"""Delegated subagent task runner for AgentLoop."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from typing import Any, Awaitable, Callable

from ..llms import ChatMessage
from ..storage import StorageProvider
from ..storage.base import StoredDelegatedTask
from ..subagent_prompts import get_all_subagents
from ..subagent_session import (
    build_child_subagent_session_id,
    extract_subagent_prompt_type,
    new_subagent_task_id,
    validate_subagent_task_id,
)
from ..tools import ToolRegistry
from ..utils.log import logger
from .run_hooks import RunHookService
from .run_state import RunCancelledError
from .run_trace import RunTraceRecorder
from .subagent_builder import SubagentMessageBuilder
from .subagent_policy import PARALLEL_SAFE_PROFILE_NAMES, build_subagent_tool_registry, profile_for_subagent

DEFAULT_MAX_PARALLEL_SUBAGENTS = 2
MAX_PARALLEL_SUBAGENTS = 4


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
            return "Error: subagent task must be a non-empty string."

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
                return validation_error
            child_task_id = resume_task_id
        else:
            child_task_id = new_subagent_task_id()

        child_session_id = build_child_subagent_session_id(parent_session_id, child_task_id)
        child_run_id = self._new_run_id()
        existing_child_messages = await self.storage.get_messages(child_session_id)
        if is_resume and not existing_child_messages:
            return f"Error: unknown task_id '{child_task_id}' for current session. Start a new delegate task instead."

        stored_prompt_type = extract_subagent_prompt_type(existing_child_messages)
        requested_prompt_type = str(prompt_type).strip() if prompt_type is not None else ""
        effective_prompt_type = requested_prompt_type or stored_prompt_type or "writer"
        if stored_prompt_type and requested_prompt_type and requested_prompt_type != stored_prompt_type:
            return (
                f"Error: task_id '{child_task_id}' was created with prompt_type '{stored_prompt_type}', "
                f"not '{requested_prompt_type}'. Omit prompt_type or use the original prompt_type to resume."
            )
        if effective_prompt_type not in subagents:
            available = ", ".join(subagents)
            return f"Error: unknown subagent type '{effective_prompt_type}'. Available: {available}"

        try:
            subagent_tools = self.build_tools(effective_prompt_type, workspace=workspace)
            subagent_profile = profile_for_subagent(
                effective_prompt_type,
                app_home=app_home,
                session_workspace=workspace,
            )
        except ValueError as e:
            return f"Error: {str(e)}"

        if group_id is not None and subagent_profile.name not in PARALLEL_SAFE_PROFILE_NAMES:
            allowed = ", ".join(sorted(PARALLEL_SAFE_PROFILE_NAMES))
            return (
                "Error: parallel delegation only supports read-only or research subagents. "
                f"'{effective_prompt_type}' uses profile '{subagent_profile.name}', not one of: {allowed}."
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
        created_at: float = 0.0,
        updated_at: float = 0.0,
    ) -> None:
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
                metadata=self._delegation_metadata(prepared),
                created_at=created_at,
                updated_at=updated_at,
            ),
        )

    async def _execute_prepared_task(
        self,
        prepared: PreparedSubagentTask,
        *,
        should_cancel: Callable[[], bool] | None,
        raise_on_failure: bool,
    ) -> SubagentTaskOutcome:
        run_metadata = {
            "kind": "subagent",
            "objective": prepared.task_preview,
            "task_id": prepared.task_id,
            "prompt_type": prepared.prompt_type,
            "parent_session_id": prepared.parent_session_id,
            "parent_run_id": prepared.parent_run_id,
            "resume": prepared.is_resume,
            **self._delegation_metadata(prepared),
        }
        started_at = time.time()
        lifecycle_payload = {
            "status": "running",
            "task_id": prepared.task_id,
            "prompt_type": prepared.prompt_type,
            "child_session_id": prepared.child_session_id,
            "child_run_id": prepared.child_run_id,
            "parent_session_id": prepared.parent_session_id,
            "parent_run_id": prepared.parent_run_id,
            "resume": prepared.is_resume,
            "task_preview": prepared.task_preview,
            "message": f"Started {prepared.prompt_type} subagent task {prepared.task_id}.",
            **self._delegation_metadata(prepared),
        }
        await self.run_trace.create_run(
            prepared.child_session_id,
            prepared.child_run_id,
            status="running",
            metadata=run_metadata,
        )
        await self.run_trace.emit_event(
            prepared.child_session_id,
            prepared.child_run_id,
            "run_started",
            lifecycle_payload,
        )
        self._record_task_update(
            prepared,
            status="running",
            created_at=started_at,
            updated_at=started_at,
        )
        await self._emit_parent_event(
            parent_session_id=prepared.parent_session_id,
            parent_run_id=prepared.parent_run_id,
            event_type="subagent.started",
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
                tool_result_session_id=prepared.child_session_id,
                tool_registry=prepared.subagent_tools,
                on_tool_before_execute=tool_progress_hook,
                on_tool_after_execute=tool_result_hook,
                on_llm_status=llm_status_hook,
                on_response_delta=llm_delta_hook,
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
            result_summary = self._format_log_preview(sub_result.content, max_chars=240)
            result_metadata = {
                "kind": "subagent_result",
                "task_id": prepared.task_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "prompt_type": prepared.prompt_type,
                "run_id": prepared.child_run_id,
                "summary": result_summary,
                **self._delegation_metadata(prepared),
            }
            await self.run_trace.record_assistant_message_part(
                prepared.child_session_id,
                prepared.child_run_id,
                sub_result.content,
                metadata={
                    **result_metadata,
                    "response_len": len(sub_result.content or ""),
                    "executed_tool_calls": sub_result.executed_tool_calls,
                    "had_tool_error": sub_result.had_tool_error,
                    "verification_attempted": sub_result.verification_attempted,
                    "verification_passed": sub_result.verification_passed,
                },
            )
            await self._save_message(
                prepared.child_session_id,
                "assistant",
                sub_result.content,
                None,
                result_metadata,
            )
            completion_payload = {
                "status": "completed",
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
                **self._delegation_metadata(prepared),
            }
            await self.run_trace.complete_run(
                prepared.child_session_id,
                prepared.child_run_id,
                event_payload=completion_payload,
                status_metadata=completion_payload,
            )
            self._record_task_update(
                prepared,
                status="completed",
                summary=result_summary,
                created_at=started_at,
                updated_at=time.time(),
            )
            await self._emit_parent_event(
                parent_session_id=prepared.parent_session_id,
                parent_run_id=prepared.parent_run_id,
                event_type="subagent.completed",
                payload=completion_payload,
            )
            return SubagentTaskOutcome(
                task_id=prepared.task_id,
                prompt_type=prepared.prompt_type,
                child_session_id=prepared.child_session_id,
                child_run_id=prepared.child_run_id,
                status="completed",
                content=sub_result.content,
                summary=result_summary,
                executed_tool_calls=sub_result.executed_tool_calls,
                had_tool_error=sub_result.had_tool_error,
                verification_attempted=sub_result.verification_attempted,
                verification_passed=sub_result.verification_passed,
                is_resume=prepared.is_resume,
                group_id=prepared.group_id,
                group_index=prepared.group_index,
                group_total=prepared.group_total,
            )
        except asyncio.CancelledError:
            cancellation_payload = {
                "status": "cancelled",
                "task_id": prepared.task_id,
                "prompt_type": prepared.prompt_type,
                "child_session_id": prepared.child_session_id,
                "child_run_id": prepared.child_run_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "resume": prepared.is_resume,
                "error": "cancelled",
                **self._delegation_metadata(prepared),
            }
            await self.run_trace.fail_run(
                prepared.child_session_id,
                prepared.child_run_id,
                status="cancelled",
                event_payload=cancellation_payload,
            )
            self._record_task_update(
                prepared,
                status="cancelled",
                error="cancelled",
                created_at=started_at,
                updated_at=time.time(),
            )
            await self._emit_parent_event(
                parent_session_id=prepared.parent_session_id,
                parent_run_id=prepared.parent_run_id,
                event_type="subagent.cancelled",
                payload=cancellation_payload,
            )
            raise
        except Exception as exc:
            error_preview = self._format_log_preview(str(exc), max_chars=240)
            failure_payload = {
                "status": "failed",
                "task_id": prepared.task_id,
                "prompt_type": prepared.prompt_type,
                "child_session_id": prepared.child_session_id,
                "child_run_id": prepared.child_run_id,
                "parent_session_id": prepared.parent_session_id,
                "parent_run_id": prepared.parent_run_id,
                "resume": prepared.is_resume,
                "error": error_preview,
                **self._delegation_metadata(prepared),
            }
            await self.run_trace.fail_run(
                prepared.child_session_id,
                prepared.child_run_id,
                status="failed",
                event_payload=failure_payload,
            )
            self._record_task_update(
                prepared,
                status="failed",
                error=error_preview,
                created_at=started_at,
                updated_at=time.time(),
            )
            await self._emit_parent_event(
                parent_session_id=prepared.parent_session_id,
                parent_run_id=prepared.parent_run_id,
                event_type="subagent.failed",
                payload=failure_payload,
            )
            if raise_on_failure:
                raise
            return SubagentTaskOutcome(
                task_id=prepared.task_id,
                prompt_type=prepared.prompt_type,
                child_session_id=prepared.child_session_id,
                child_run_id=prepared.child_run_id,
                status="failed",
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
        failed = sum(1 for outcome in outcomes if outcome.status != "completed")
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
            if outcome.status == "completed":
                lines.extend(["Result:", outcome.content])
            else:
                lines.extend(["Error:", outcome.error or outcome.summary or "unknown failure"])
        return "\n".join(lines)

    async def run(
        self,
        task: str,
        prompt_type: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Run or resume a delegated subagent task through a child storage session."""
        prepared = await self._prepare_task(task, prompt_type=prompt_type, task_id=task_id)
        if isinstance(prepared, str):
            return prepared

        outcome = await self._execute_prepared_task(
            prepared,
            should_cancel=lambda: self._cancel_requested(prepared.parent_session_id, prepared.parent_run_id),
            raise_on_failure=True,
        )
        return (
            f"Task ID: {outcome.task_id}\n"
            f"Subagent: {outcome.prompt_type}\n\n"
            f"Result:\n{outcome.content}"
        )

    async def run_many(self, tasks: list[dict[str, Any]], max_parallel: int | None = None) -> str:
        """Run multiple safe read-only or research child tasks concurrently."""
        if not isinstance(tasks, list):
            return "Error: tasks must be an array of {task, prompt_type} objects."
        if not tasks:
            return "Error: tasks must contain at least one child task."
        if len(tasks) > MAX_PARALLEL_SUBAGENTS:
            return f"Error: delegate_many supports at most {MAX_PARALLEL_SUBAGENTS} tasks."

        group_id = self._new_group_id()
        prepared_tasks: list[PreparedSubagentTask] = []
        total = len(tasks)
        for index, item in enumerate(tasks, start=1):
            if not isinstance(item, dict):
                return f"Error: task[{index}] must be an object with task and prompt_type."
            task_text = str(item.get("task") or "").strip()
            prompt_type = str(item.get("prompt_type") or item.get("promptType") or "").strip()
            if not prompt_type:
                return f"Error: task[{index}] prompt_type is required for parallel delegation."
            prepared = await self._prepare_task(
                task_text,
                prompt_type=prompt_type,
                group_id=group_id,
                group_index=index,
                group_total=total,
            )
            if isinstance(prepared, str):
                prefix = prepared.removeprefix("Error: ") if prepared.startswith("Error: ") else prepared
                return f"Error: task[{index}] {prefix}"
            prepared_tasks.append(prepared)

        try:
            requested_parallel = int(max_parallel or DEFAULT_MAX_PARALLEL_SUBAGENTS)
        except (TypeError, ValueError):
            return "Error: max_parallel must be an integer."
        concurrency = max(1, min(requested_parallel, len(prepared_tasks), MAX_PARALLEL_SUBAGENTS))
        semaphore = asyncio.Semaphore(concurrency)
        parent_session_id = prepared_tasks[0].parent_session_id
        parent_run_id = prepared_tasks[0].parent_run_id

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
                        status="failed",
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
        return self._format_parallel_results(ordered_outcomes, group_id=group_id, max_parallel=concurrency)
