"""User turn orchestration for AgentLoop.process."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable
from uuid import uuid4

from ..bus.message import AssistantMessage, UserMessage
from ..utils.log import logger
from .completion_gate import CompletionGateResult, CompletionGateService
from .execution import ExecutionResult
from .media import AgentMediaService
from .response_finalizer import AgentResponseFinalizer
from .run_trace import RunTraceRecorder
from .task_intent import TaskIntent, TaskIntentService
from .turn_context import TurnContextService
from .turn_input import PreparedTurnInput


class AgentTurnRunner:
    """Runs user-turn branches after inbound turn input is prepared."""

    def __init__(
        self,
        *,
        run_trace: RunTraceRecorder,
        response_finalizer: AgentResponseFinalizer,
        turn_context: TurnContextService,
        task_intents: TaskIntentService,
        completion_gate: CompletionGateService,
        connect_mcp: Callable[[], Awaitable[None]],
        save_message: Callable[..., Awaitable[None]],
        emit_run_event: Callable[..., Awaitable[None]],
        call_llm: Callable[..., Awaitable[ExecutionResult]],
        get_queued_outbound_media: Callable[[], dict[str, list[str]]],
        media_saved_ack: Callable[[], str],
        llm_not_configured_message: Callable[[], str],
        format_log_preview: Callable[..., str],
        apply_completion_gate_result: Callable[[str, CompletionGateResult], Awaitable[None]],
        schedule_post_response_maintenance: Callable[[str], None],
        maybe_schedule_skill_review: Callable[[str, ExecutionResult], None],
    ):
        self.run_trace = run_trace
        self.response_finalizer = response_finalizer
        self.turn_context = turn_context
        self.task_intents = task_intents
        self.completion_gate = completion_gate
        self._connect_mcp = connect_mcp
        self._save_message = save_message
        self._emit_run_event = emit_run_event
        self._call_llm = call_llm
        self._get_queued_outbound_media = get_queued_outbound_media
        self._media_saved_ack = media_saved_ack
        self._llm_not_configured_message = llm_not_configured_message
        self._format_log_preview = format_log_preview
        self._apply_completion_gate_result = apply_completion_gate_result
        self._schedule_post_response_maintenance = schedule_post_response_maintenance
        self._maybe_schedule_skill_review = maybe_schedule_skill_review

    @staticmethod
    def is_media_only_message(user_message: UserMessage) -> bool:
        """Return whether a turn only carries media without user instructions."""
        return AgentMediaService.is_media_only_message(
            text=user_message.text,
            images=user_message.images,
            audios=user_message.audios,
            videos=user_message.videos,
        )

    async def run_user_turn(
        self,
        *,
        user_message: UserMessage,
        turn: PreparedTurnInput,
        llm_configured: bool,
    ) -> AssistantMessage:
        """Start run telemetry and dispatch one prepared user turn."""
        run_id = f"run_{uuid4().hex}"
        await self.run_trace.start_turn_run(
            turn.session_chat_id,
            run_id,
            channel=turn.channel,
            transport_chat_id=turn.transport_chat_id,
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
        await self._emit_run_event(
            turn.session_chat_id,
            run_id,
            "task_intent.detected",
            task_intent.to_metadata(),
            channel=turn.channel,
            transport_chat_id=turn.transport_chat_id,
        )

        if self.is_media_only_message(user_message):
            return await self.run_media_only_turn(
                user_message=user_message,
                turn=turn,
                run_id=run_id,
            )

        with self.turn_context.activate(
            chat_id=turn.session_chat_id,
            channel=turn.channel,
            transport_chat_id=turn.transport_chat_id,
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
                )
            except asyncio.CancelledError:
                await self.run_trace.fail_run(
                    turn.session_chat_id,
                    run_id,
                    status="cancelled",
                    event_payload={"status": "cancelled", "error": "cancelled"},
                    channel=turn.channel,
                    transport_chat_id=turn.transport_chat_id,
                )
                raise
            except Exception as exc:
                logger.exception(
                    f"[{turn.session_chat_id}] Agent.process failed: channel={turn.channel}, "
                    f"text_len={len(user_message.text or '')}, images={len(user_message.images or [])}, audios={len(user_message.audios or [])}, videos={len(user_message.videos or [])}"
                )
                await self.run_trace.fail_run(
                    turn.session_chat_id,
                    run_id,
                    status="failed",
                    event_payload={
                        "status": "failed",
                        "error": self._format_log_preview(f"{type(exc).__name__}: {exc}", max_chars=240),
                    },
                    channel=turn.channel,
                    transport_chat_id=turn.transport_chat_id,
                )
                raise

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
        await self._save_message(turn.session_chat_id, "user", media_history_content, metadata=turn.user_metadata)
        response = self._media_saved_ack()
        return await self.response_finalizer.finalize(
            session_chat_id=turn.session_chat_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            chat_id=user_message.chat_id,
            transport_chat_id=turn.transport_chat_id,
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
        logger.warning("[{}] agent.skip | reason=llm-not-configured", turn.session_chat_id)
        await self._save_message(turn.session_chat_id, "user", user_message.text, metadata=turn.user_metadata)
        response = self._llm_not_configured_message()
        return await self.response_finalizer.finalize(
            session_chat_id=turn.session_chat_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            chat_id=user_message.chat_id,
            transport_chat_id=turn.transport_chat_id,
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
    ) -> AssistantMessage:
        """Execute the normal turn path after special-case early exits are ruled out."""
        await self._connect_mcp()

        # The current user message is persisted before building the prompt so history/search stay current.
        await self._save_message(turn.session_chat_id, "user", user_message.text, metadata=turn.user_metadata)

        logger.info(f"[{turn.session_chat_id}] agent.run | status=processing")
        await self._emit_run_event(
            turn.session_chat_id,
            run_id,
            "llm_status",
            {"message": "processing"},
            channel=turn.channel,
            transport_chat_id=turn.transport_chat_id,
        )
        exec_result = await self._call_llm(
            turn.session_chat_id,
            current_message=user_message.text,
            channel=turn.channel,
            user_images=user_message.images,
            user_image_files=turn.image_files,
            user_audio_files=turn.audio_files,
            user_video_files=turn.video_files,
            transport_chat_id=turn.transport_chat_id,
            emit_tool_progress=True,
            task_intent=task_intent,
        )
        response = exec_result.content
        outbound_media = self._get_queued_outbound_media()

        await self.run_trace.record_context_compaction_parts(
            turn.session_chat_id,
            run_id,
            exec_result.context_compaction_events,
        )

        response_metadata = {
            "response_len": len(response or ""),
            "executed_tool_calls": exec_result.executed_tool_calls,
            "had_tool_error": exec_result.had_tool_error,
            "verification_attempted": exec_result.verification_attempted,
            "verification_passed": exec_result.verification_passed,
            "context_compactions": exec_result.context_compactions,
        }
        status_metadata = {
            "executed_tool_calls": exec_result.executed_tool_calls,
            "had_tool_error": exec_result.had_tool_error,
            "verification_attempted": exec_result.verification_attempted,
            "verification_passed": exec_result.verification_passed,
            "context_compactions": exec_result.context_compactions,
        }
        completion_result = self.completion_gate.evaluate(
            task_intent=task_intent,
            response_text=response,
            execution_result=exec_result,
        )
        completion_metadata = completion_result.to_metadata()
        response_metadata["completion_gate"] = completion_metadata
        status_metadata["completion_status"] = completion_result.status
        await self._emit_run_event(
            turn.session_chat_id,
            run_id,
            "completion_gate.evaluated",
            completion_metadata,
            channel=turn.channel,
            transport_chat_id=turn.transport_chat_id,
        )

        async def after_response_saved() -> None:
            await self._apply_completion_gate_result(turn.session_chat_id, completion_result)
            self._schedule_post_response_maintenance(turn.session_chat_id)
            self._maybe_schedule_skill_review(turn.session_chat_id, exec_result)

        return await self.response_finalizer.finalize(
            session_chat_id=turn.session_chat_id,
            run_id=run_id,
            response=response,
            channel=turn.channel,
            chat_id=user_message.chat_id,
            transport_chat_id=turn.transport_chat_id,
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
