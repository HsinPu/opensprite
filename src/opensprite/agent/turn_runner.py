"""User turn orchestration for AgentLoop.process."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..bus.message import AssistantMessage, UserMessage
from ..utils.log import logger
from .audio_input import AudioInputPreprocessor
from .auto_continue import AutoContinueService
from .completion_gate import CompletionGateResult, CompletionGateService
from .execution import ExecutionResult
from .harness_profile import HarnessProfile, HarnessProfileService
from .harness_scorecard import HarnessScorecard, HarnessSensorResult
from .harness_sensors import evaluate_harness_sensors
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


@dataclass(frozen=True)
class TurnPassEvaluation:
    """Evaluation output for one normal-turn execution pass."""

    aggregate_result: ExecutionResult
    completion_result: CompletionGateResult
    work_progress: WorkProgressUpdate
    collected_delegated_tasks: tuple[StoredDelegatedTask, ...]
    collected_workflow_outcomes: tuple[dict[str, Any], ...]


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
        format_log_preview: Callable[..., str],
        set_session_overlay_id: Callable[[str, dict[str, Any] | None, str | None, str | None], None],
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
        self._format_log_preview = format_log_preview
        self._set_session_overlay_id = set_session_overlay_id
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
            "audio_input.transcribed",
            {
                "status": result.status,
                "audio_files": list(result.audio_files),
                "transcript_len": result.transcript_len,
            },
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
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
        for media_event in turn.media_events:
            await self._emit_run_event(
                turn.session_id,
                run_id,
                "inbound_media.persisted" if media_event.get("status") == "persisted" else "inbound_media." + str(media_event.get("status") or "unknown"),
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
                        harness_profile=None,
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
                "execution.stopped",
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
            harness_profile=harness_profile,
        )
        await self._emit_run_event(
            turn.session_id,
            run_id,
            "work_progress.updated",
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
            "harness_checkpoint.recorded",
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
            "harness_scorecard.recorded",
            harness_scorecard,
            channel=turn.channel,
            external_chat_id=turn.external_chat_id,
        )
        await self.run_trace.record_harness_scorecard_part(turn.session_id, run_id, harness_scorecard)
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
        return TurnPassEvaluation(
            aggregate_result=aggregate_result,
            completion_result=completion_result,
            work_progress=work_progress,
            collected_delegated_tasks=collected_delegated_tasks,
            collected_workflow_outcomes=collected_workflow_outcomes,
        )

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
        collected_workflow_outcomes: tuple[dict[str, Any], ...] = ()
        auto_continue_attempts = 0
        direct_actions_used = 0
        last_direct_workflow: str | None = None
        last_direct_start_step: str | None = None
        last_direct_verify_action: str | None = None
        last_direct_verify_path: str | None = None
        last_direct_verify_pytest_args: tuple[str, ...] = ()
        same_target_verify_attempts = 0
        pending_direct_verify: dict[str, Any] | None = self._extract_direct_verify_request(user_message.metadata)
        current_message = user_message.text
        current_allow_tools = True

        pending_direct_resume = self._extract_follow_up_resume_request(user_message.metadata)

        while True:
            self.turn_context.reset_work_progress()
            direct_resume_context: dict[str, str] | None = None
            if pending_direct_resume is not None:
                direct_resume_context = dict(pending_direct_resume)
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    "direct_workflow_resume.started",
                    {"schema_version": 1, **direct_resume_context},
                    channel=turn.channel,
                    external_chat_id=turn.external_chat_id,
                )
                response, exec_result, collected_delegated_tasks, collected_workflow_outcomes = await self._run_direct_workflow_resume(
                    run_id=run_id,
                    task_intent=task_intent,
                    direct_resume=pending_direct_resume,
                    collected_delegated_tasks=collected_delegated_tasks,
                    collected_workflow_outcomes=collected_workflow_outcomes,
                )
                pending_direct_resume = None
            elif pending_direct_verify is not None:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    "direct_verification.started",
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
                )
                exec_result = self._apply_runtime_progress(exec_result, self.turn_context.snapshot_work_progress())
                if exec_result.task_contract is not None:
                    harness_profile = self.harness_profiles.from_contract(exec_result.task_contract)
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

        response = _final_response_after_exhausted_continuation(
            response=response,
            completion_result=completion_result,
            auto_continue_attempts=auto_continue_attempts,
            execution_result=aggregate_result,
        )
        if response != aggregate_result.content:
            aggregate_result.content = response
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
                harness_profile=harness_profile,
            )
            await self._emit_run_event(
                turn.session_id,
                run_id,
                "work_progress.updated",
                work_progress.to_metadata(),
                channel=turn.channel,
                external_chat_id=turn.external_chat_id,
            )

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
        if aggregate_result.task_contract is not None:
            response_metadata["task_contract"] = aggregate_result.task_contract.to_metadata()
        if aggregate_result.tool_evidence:
            response_metadata["tool_evidence"] = [item.to_metadata() for item in aggregate_result.tool_evidence]
        if aggregate_result.task_artifacts:
            response_metadata["task_artifacts"] = [item.to_metadata() for item in aggregate_result.task_artifacts]
        status_metadata["completion_status"] = completion_result.status
        response_metadata["delegated_tasks"] = [task.to_payload() for task in aggregate_result.delegated_tasks]
        response_metadata["active_delegate_task_id"] = aggregate_result.active_delegate_task_id
        response_metadata["active_delegate_prompt_type"] = aggregate_result.active_delegate_prompt_type
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
        run_finish_status = "completed" if completion_result.status == "complete" else (completion_result.status or "incomplete")

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
            if aggregate_result.task_artifacts:
                await self._emit_run_event(
                    turn.session_id,
                    run_id,
                    "task_artifacts.recorded",
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
        if str(payload.get("quick_action") or "").strip() != "resume_follow_up":
            return None
        workflow = str(payload.get("follow_up_workflow") or "").strip()
        start_step = str(payload.get("follow_up_step_id") or "").strip()
        if not workflow or not start_step:
            return None
        return {
            "workflow": workflow,
            "start_step": start_step,
            "step_label": str(payload.get("follow_up_step_label") or start_step).strip() or start_step,
            "prompt_type": str(payload.get("follow_up_prompt_type") or "").strip(),
            "detail": str(payload.get("active_task_detail") or "").strip(),
            "previous_response": "continue",
        }

    @staticmethod
    def _extract_direct_verify_request(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        payload = dict(metadata or {}) if isinstance(metadata, dict) else {}
        if str(payload.get("quick_action") or "").strip() != "run_verification":
            return None
        action = str(payload.get("verification_action") or "").strip()
        if not action:
            return None
        path = str(payload.get("verification_path") or ".").strip() or "."
        pytest_args = tuple(
            str(item or "").strip()
            for item in (payload.get("verification_pytest_args") or payload.get("verificationPytestArgs") or ())
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
        direct_resume: dict[str, str],
        collected_delegated_tasks: tuple[StoredDelegatedTask, ...],
        collected_workflow_outcomes: tuple[dict[str, Any], ...],
    ) -> tuple[str, ExecutionResult, tuple[StoredDelegatedTask, ...], tuple[dict[str, Any], ...]]:
        workflow_result = await self._run_workflow(
            direct_resume["workflow"],
            task_intent.objective,
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
        """Keep a validated contract if a later retry only failed or fell back during planning."""
        fallback = next(
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
                    and _task_contract_planner_status(result.task_contract) == "validated"
                )
            ),
            None,
        )
        if validated is not None:
            return validated
        return next(
            (
                result.task_contract
                for result in reversed(results)
                if result.task_contract is not None and result.task_contract.task_type != "planning_error"
            ),
            fallback,
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
        "auto_continue_attempts": max(0, auto_continue_attempts),
        "harness_profile": profile_metadata,
        "harness_policy": dict(aggregate_result.harness_policy or {}),
        "task_contract": task_contract.to_metadata() if task_contract is not None else None,
        "completion": completion_result.to_metadata(),
        "work_progress": work_progress.to_metadata(),
        "next_action": work_progress.next_action,
        "tool_evidence_count": len(aggregate_result.tool_evidence),
        "task_artifact_count": len(aggregate_result.task_artifacts),
    }


def _final_response_after_exhausted_continuation(
    *,
    response: str,
    completion_result: CompletionGateResult,
    auto_continue_attempts: int,
    execution_result: ExecutionResult | None = None,
) -> str:
    source_fallback = _source_fallback_response(completion_result, execution_result)
    if source_fallback:
        return source_fallback
    if not _should_replace_nonfinal_response(
        response=response,
        completion_result=completion_result,
        auto_continue_attempts=auto_continue_attempts,
    ):
        return response
    return _completion_blocker_response(completion_result)


def _source_fallback_response(
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult | None,
) -> str:
    if execution_result is None:
        return ""
    if completion_result.reason not in {
        "assistant final answer did not reference gathered sources",
        "assistant final answer was too terse for the task",
        "assistant did not provide the requested itemized result",
        "tool execution reported an error without a clear blocker handoff",
    }:
        return ""
    sources = _substantive_web_sources(execution_result)
    if not sources:
        return ""
    objective = _execution_objective(execution_result)
    sources = _rank_web_sources_for_objective(sources, objective)
    if _objective_requests_market_quote(objective):
        snippets = " ".join(
            str(source.get("snippet") or source.get("content") or "")
            for source in sources[:4]
        )
        if not _text_contains_market_quote(snippets):
            return ""
    if completion_result.reason == "tool execution reported an error without a clear blocker handoff":
        top_score = _web_source_relevance_score(sources[0], objective) if sources else 0
        if top_score <= 0:
            return ""

    detail_lines: list[str] = []
    source_lines: list[str] = []
    for index, source in enumerate(sources[:4], start=1):
        title = str(source.get("title") or "").strip() or str(source.get("url") or "").strip()
        url = str(source.get("url") or "").strip()
        snippet = _clean_source_fallback_snippet(str(source.get("snippet") or source.get("content") or ""))
        if snippet:
            detail_lines.append(f"{index}. {title}: {snippet[:280]}")
        else:
            detail_lines.append(f"{index}. {title}")
        source_lines.append(f"{index}. {url}")

    return "\n\n".join(
        [
            "我已根據本輪已成功蒐集到的來源整理如下，避免停在只有進度句的狀態。",
            "重點摘要：\n" + "\n".join(detail_lines),
            "來源網址：\n" + "\n".join(source_lines),
        ]
    )


def _clean_source_fallback_snippet(snippet: str) -> str:
    cleaned = str(snippet or "")
    cleaned = re.sub(r"!\[[^\]]*]\([^)]+\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)]\((https?://[^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    return " ".join(cleaned.split())


def _objective_requests_market_quote(objective: str) -> bool:
    normalized = str(objective or "").lower()
    return any(
        marker in normalized
        for marker in (
            "stock price",
            "share price",
            "market price",
            "latest price",
            "current price",
            "quote",
            "股價",
            "報價",
        )
    )


def _text_contains_market_quote(text: str) -> bool:
    return bool(
        re.search(
            r"(?:\$|usd|twd|nt\$|美元|台幣)\s*\d+(?:[,.]\d+)*(?:\.\d+)?"
            r"|\d+(?:[,.]\d+)*(?:\.\d+)?\s*(?:usd|twd|美元|台幣)"
            r"|(?:股價|報價|收盤|quote|price)\D{0,24}\d+(?:[,.]\d+)*(?:\.\d+)?",
            str(text or ""),
            flags=re.IGNORECASE,
        )
    )


def _substantive_web_sources(execution_result: ExecutionResult) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or artifact.kind != "web_source":
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
            if raw_source.get("tool_name") == "web_fetch" and (content_chars >= 800 or has_main_content) and not is_too_short:
                seen_urls.add(url)
                sources.append(raw_source)
    return sources


def _execution_objective(execution_result: ExecutionResult) -> str:
    task_contract = getattr(execution_result, "task_contract", None)
    return str(getattr(task_contract, "objective", "") or "").strip()


def _rank_web_sources_for_objective(sources: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    if not objective:
        return sources
    return sorted(
        sources,
        key=lambda source: _web_source_relevance_score(source, objective),
        reverse=True,
    )


def _web_source_relevance_score(source: dict[str, Any], objective: str) -> int:
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
    keywords.update(item for item in re.findall(r"[a-z0-9.:-]{3,}", text))
    for cjk_text in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        keywords.add(cjk_text)
        for size in (2, 3, 4):
            for index in range(0, max(len(cjk_text) - size + 1, 0)):
                keywords.add(cjk_text[index : index + size])
    stop_words = {
        "please",
        "current",
        "latest",
        "\u5e6b\u6211",
        "\u76ee\u524d",
        "\u6700\u65b0",
        "\u8acb\u5217\u51fa",
        "\u4f86\u6e90\u7db2\u5740",
    }
    return {keyword for keyword in keywords if keyword not in stop_words}


def _objective_brand_tokens(objective: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b", str(objective or ""))
    }


def _domain_brand_label(domain: str) -> str:
    labels = str(domain or "").lower().removeprefix("www.").split(".")
    labels = [label for label in labels if label]
    if len(labels) < 2:
        return ""
    return labels[-2].replace("-", "")


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
    if completion_result.status == "complete":
        return False
    text = (response or "").strip()
    if not text:
        return True
    lower_text = text.lower()
    blocker_markers = (
        "無法",
        "不能",
        "不足",
        "缺少",
        "阻礙",
        "被封鎖",
        "未完成",
        "尚未完成",
        "cannot",
        "can't",
        "unable",
        "blocked",
        "insufficient",
        "missing",
        "incomplete",
        "not enough",
    )
    if any(marker in lower_text for marker in blocker_markers):
        return False
    fallback_markers = (
        "no visible reply",
        "try again",
        "沒有產生可顯示",
        "請再試一次",
    )
    if any(marker in lower_text for marker in fallback_markers):
        return True
    progress_markers = (
        "讓我",
        "我會",
        "我將",
        "正在",
        "進一步",
        "再查",
        "繼續",
        "稍等",
        "let me",
        "i will",
        "i'll",
        "working on",
        "one moment",
        "next i",
    )
    return len(text) <= 320 and any(marker in lower_text for marker in progress_markers)


def _completion_blocker_response(completion_result: CompletionGateResult) -> str:
    reason = (completion_result.reason or completion_result.status or "completion gate did not pass").strip()
    detail = (completion_result.active_task_detail or "").strip()
    missing = [item.strip() for item in completion_result.missing_evidence if str(item).strip()]
    sections = [
        "目前還不能可靠完成這次請求。",
        f"原因：{reason}",
    ]
    if detail:
        detail_lines = [line.strip("- ").strip() for line in detail.splitlines() if line.strip()]
        if detail_lines:
            sections.append("仍缺的部分：\n" + "\n".join(f"- {line}" for line in detail_lines))
    if missing:
        sections.append("缺少的證據：\n" + "\n".join(f"- {item}" for item in missing))
    sections.append("我已停止自動重試，避免用不足資訊硬回答。")
    return "\n\n".join(sections)


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


def _task_contract_planner_status(task_contract: Any) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get("planner_status") or "").strip()
    return ""
