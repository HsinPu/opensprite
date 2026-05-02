"""User turn orchestration for AgentLoop.process."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from ..bus.message import AssistantMessage, UserMessage
from ..utils.log import logger
from .auto_continue import AutoContinueService
from .completion_gate import CompletionGateResult, CompletionGateService
from .execution import ExecutionResult
from .media import AgentMediaService
from .response_finalizer import AgentResponseFinalizer
from .run_state import AgentRunStateService
from .run_trace import RunTraceRecorder
from ..storage import StoredDelegatedTask, StoredWorkState
from ..storage.base import selected_delegated_task
from .task_intent import TaskIntent, TaskIntentService
from .turn_context import TurnContextService
from .turn_input import PreparedTurnInput
from .work_progress import WorkPlan, WorkProgressService, WorkProgressUpdate
from .worktree import WorktreeSandboxInspector


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
        completion_gate: CompletionGateService,
        auto_continue: AutoContinueService,
        work_progress: WorkProgressService,
        connect_mcp: Callable[[], Awaitable[None]],
        save_message: Callable[..., Awaitable[None]],
        emit_run_event: Callable[..., Awaitable[None]],
        call_llm: Callable[..., Awaitable[ExecutionResult]],
        get_queued_outbound_media: Callable[[], dict[str, list[str]]],
        media_saved_ack: Callable[[], str],
        llm_not_configured_message: Callable[[], str],
        format_log_preview: Callable[..., str],
        get_work_state: Callable[[str], Awaitable[StoredWorkState | None]],
        save_work_state: Callable[[StoredWorkState | None], Awaitable[None]],
        apply_completion_gate_result: Callable[[str, CompletionGateResult], Awaitable[None]],
        apply_work_progress: Callable[[str, WorkProgressUpdate, StoredWorkState | None], Awaitable[None]],
        schedule_curator: Callable[[str, str, str | None, str | None, ExecutionResult], None],
        finalize_learning_reuse: Callable[[str, str, bool], None],
        consume_delegated_task_updates: Callable[[str], tuple[StoredDelegatedTask, ...]],
        clear_delegated_task_updates: Callable[[str], None],
        worktree_sandbox_enabled: Callable[[], bool],
        workspace_root: Callable[[], Path],
    ):
        self.run_trace = run_trace
        self.response_finalizer = response_finalizer
        self.turn_context = turn_context
        self.run_state = run_state
        self.task_intents = task_intents
        self.completion_gate = completion_gate
        self.auto_continue = auto_continue
        self.work_progress = work_progress
        self._connect_mcp = connect_mcp
        self._save_message = save_message
        self._emit_run_event = emit_run_event
        self._call_llm = call_llm
        self._get_queued_outbound_media = get_queued_outbound_media
        self._media_saved_ack = media_saved_ack
        self._llm_not_configured_message = llm_not_configured_message
        self._format_log_preview = format_log_preview
        self._get_work_state = get_work_state
        self._save_work_state = save_work_state
        self._apply_completion_gate_result = apply_completion_gate_result
        self._apply_work_progress = apply_work_progress
        self._schedule_curator = schedule_curator
        self._finalize_learning_reuse = finalize_learning_reuse
        self._consume_delegated_task_updates = consume_delegated_task_updates
        self._clear_delegated_task_updates = clear_delegated_task_updates
        self._worktree_sandbox_enabled = worktree_sandbox_enabled
        self._workspace_root = workspace_root

    @staticmethod
    def is_media_only_message(user_message: UserMessage) -> bool:
        """Return whether a turn only carries media without user instructions."""
        return AgentMediaService.is_media_only_message(
            text=user_message.text,
            images=user_message.images,
            audios=user_message.audios,
            videos=user_message.videos,
        )

    async def _maybe_record_worktree_sandbox(self, session_id: str, run_id: str, task_intent: TaskIntent) -> None:
        enabled = self._worktree_sandbox_enabled()
        if not enabled and not task_intent.expects_code_change:
            return
        metadata = WorktreeSandboxInspector(
            enabled=enabled,
            workspace_root=self._workspace_root(),
        ).create(session_id=session_id, run_id=run_id).to_payload()
        metadata["task_kind"] = task_intent.kind
        metadata["expects_code_change"] = task_intent.expects_code_change
        await self.run_trace.record_worktree_sandbox_part(session_id, run_id, metadata)

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
        task_intent = self.task_intents.classify(
            user_message.text,
            images=user_message.images,
            audios=user_message.audios,
            videos=user_message.videos,
            metadata=user_message.metadata,
        )
        existing_work_state = await self._get_work_state(turn.session_id)
        task_intent = self.work_progress.resolve_intent(task_intent, existing_work_state)
        await self._maybe_record_worktree_sandbox(turn.session_id, run_id, task_intent)
        await self._emit_run_event(
            turn.session_id,
            run_id,
            "task_intent.detected",
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
        )
        await self._save_work_state(current_work_state)
        if work_plan is not None:
            await self._emit_run_event(
                turn.session_id,
                run_id,
                "work_plan.created",
                work_plan.to_metadata(),
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
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
                        work_plan=work_plan,
                        current_work_state=current_work_state,
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
            run_part_metadata={"reason": "media_only", "response_len": len(response or "")},
            run_event_payload={
                "status": "completed",
                "reason": "media_only",
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
        logger.warning("[{}] agent.skip | reason=llm-not-configured", turn.session_id)
        await self._save_message(turn.session_id, "user", user_message.text, metadata=turn.user_metadata)
        response = self._llm_not_configured_message()
        return await self.response_finalizer.finalize(
            session_id=turn.session_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
            assistant_metadata=turn.assistant_metadata,
            run_part_metadata={"reason": "llm_not_configured", "response_len": len(response or "")},
            run_event_payload={
                "status": "completed",
                "reason": "llm_not_configured",
                "response_len": len(response or ""),
            },
            log_before_record=True,
        )

    async def run_normal_turn(
        self,
        *,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        run_id: str,
        task_intent: TaskIntent,
        work_plan: WorkPlan | None,
        current_work_state: StoredWorkState | None,
    ) -> AssistantMessage:
        """Execute the normal turn path after special-case early exits are ruled out."""
        await self._connect_mcp()

        # The current user message is persisted before building the prompt so history/search stay current.
        await self._save_message(turn.session_id, "user", user_message.text, metadata=turn.user_metadata)

        logger.info(f"[{turn.session_id}] agent.run | status=processing")
        await self._emit_run_event(
            turn.session_id,
            run_id,
            "llm_status",
            {"message": "processing"},
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        execution_results: list[ExecutionResult] = []
        collected_delegated_tasks: tuple[StoredDelegatedTask, ...] = ()
        auto_continue_attempts = 0
        current_message = user_message.text

        while True:
            self.turn_context.reset_work_progress()
            exec_result = await self._call_llm(
                turn.session_id,
                current_message=current_message,
                channel=turn.channel,
                user_images=user_message.images,
                user_image_files=turn.image_files,
                user_audio_files=turn.audio_files,
                user_video_files=turn.video_files,
                external_chat_id=turn.external_chat_id,
                emit_tool_progress=True,
                task_intent=task_intent,
            )
            exec_result = self._apply_runtime_progress(exec_result, self.turn_context.snapshot_work_progress())
            response = exec_result.content
            execution_results.append(exec_result)

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
            aggregate_result = self._aggregate_execution_results(execution_results, content=response)
            delegated_task_updates = self._consume_delegated_task_updates(run_id)
            if delegated_task_updates:
                collected_delegated_tasks = self._merge_delegated_task_updates(
                    collected_delegated_tasks,
                    delegated_task_updates,
                )
            if collected_delegated_tasks:
                aggregate_result = self._with_delegated_tasks(aggregate_result, collected_delegated_tasks)
            completion_result = self.completion_gate.evaluate(
                task_intent=task_intent,
                response_text=response,
                execution_result=aggregate_result,
            )
            completion_metadata = completion_result.to_metadata()
            completion_metadata["auto_continue_attempts"] = auto_continue_attempts
            await self._emit_run_event(
                turn.session_id,
                run_id,
                "completion_gate.evaluated",
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
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                "work_progress.updated",
                work_progress.to_metadata(),
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )
            if auto_continue_attempts > 0:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    "auto_continue.completed",
                    {
                        "attempt": auto_continue_attempts,
                        "completion_status": completion_result.status,
                        "completion_reason": completion_result.reason,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )

            decision = self.auto_continue.decide(
                task_intent=task_intent,
                completion_result=completion_result,
                execution_result=aggregate_result,
                attempts_used=auto_continue_attempts,
                previous_response=response,
                work_progress=work_progress,
            )
            if decision.should_continue and decision.prompt:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    "auto_continue.scheduled",
                    {
                        **decision.to_metadata(),
                        "completion_status": completion_result.status,
                        "completion_reason": completion_result.reason,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
                auto_continue_attempts += 1
                current_message = decision.prompt
                continue

            if decision.emit_skipped_event:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    "auto_continue.skipped",
                    {
                        **decision.to_metadata(),
                        "completion_status": completion_result.status,
                        "completion_reason": completion_result.reason,
                    },
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
            break

        outbound_media = self._get_queued_outbound_media()

        response_metadata = {
            "response_len": len(response or ""),
            "executed_tool_calls": aggregate_result.executed_tool_calls,
            "had_tool_error": aggregate_result.had_tool_error,
            "verification_attempted": aggregate_result.verification_attempted,
            "verification_passed": aggregate_result.verification_passed,
            "context_compactions": aggregate_result.context_compactions,
            "auto_continue_attempts": auto_continue_attempts,
            "work_progress": work_progress.to_metadata(),
        }
        status_metadata = {
            "executed_tool_calls": aggregate_result.executed_tool_calls,
            "had_tool_error": aggregate_result.had_tool_error,
            "verification_attempted": aggregate_result.verification_attempted,
            "verification_passed": aggregate_result.verification_passed,
            "context_compactions": aggregate_result.context_compactions,
            "auto_continue_attempts": auto_continue_attempts,
        }
        completion_metadata = completion_result.to_metadata()
        completion_metadata["auto_continue_attempts"] = auto_continue_attempts
        response_metadata["completion_gate"] = completion_metadata
        status_metadata["completion_status"] = completion_result.status
        response_metadata["delegated_tasks"] = [task.to_payload() for task in aggregate_result.delegated_tasks]
        response_metadata["active_delegate_task_id"] = aggregate_result.active_delegate_task_id
        response_metadata["active_delegate_prompt_type"] = aggregate_result.active_delegate_prompt_type

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

        async def after_response_saved() -> None:
            await self._save_work_state(updated_work_state)
            if updated_work_state is not None:
                todos = await self.run_trace.record_task_checklist_part(turn.session_id, run_id, updated_work_state)
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    "task_checklist.updated",
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
            self._finalize_learning_reuse(turn.session_id, run_id, True)
            self._schedule_curator(
                turn.session_id,
                run_id,
                turn.channel,
                turn.external_chat_id,
                aggregate_result,
            )

        return await self.response_finalizer.finalize(
            session_id=turn.session_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
            assistant_metadata=turn.assistant_metadata,
            run_part_metadata=response_metadata,
            run_event_payload={"status": "completed", **response_metadata},
            status_metadata=status_metadata,
            images=outbound_media["images"] or None,
            voices=outbound_media["voices"] or None,
            audios=outbound_media["audios"] or None,
            videos=outbound_media["videos"] or None,
            after_save=after_response_saved,
        )

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
                    if update.status and update.status != "failed"
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
    def _aggregate_execution_results(results: list[ExecutionResult], *, content: str) -> ExecutionResult:
        """Aggregate multi-pass execution telemetry while keeping the final response."""
        delegated_tasks = tuple(task for result in results for task in result.delegated_tasks)
        selected_task = selected_delegated_task(delegated_tasks)
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
