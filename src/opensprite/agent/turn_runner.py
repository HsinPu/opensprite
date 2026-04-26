"""Normal LLM turn orchestration for AgentLoop.process."""

from __future__ import annotations

from typing import Awaitable, Callable

from ..bus.message import AssistantMessage, UserMessage
from ..utils.log import logger
from .execution import ExecutionResult
from .media import AgentMediaService
from .response_finalizer import AgentResponseFinalizer
from .run_trace import RunTraceRecorder
from .turn_input import PreparedTurnInput


class AgentTurnRunner:
    """Runs the normal LLM-backed user turn path."""

    def __init__(
        self,
        *,
        run_trace: RunTraceRecorder,
        response_finalizer: AgentResponseFinalizer,
        connect_mcp: Callable[[], Awaitable[None]],
        save_message: Callable[..., Awaitable[None]],
        emit_run_event: Callable[..., Awaitable[None]],
        call_llm: Callable[..., Awaitable[ExecutionResult]],
        get_queued_outbound_media: Callable[[], dict[str, list[str]]],
        media_saved_ack: Callable[[], str],
        llm_not_configured_message: Callable[[], str],
        apply_immediate_task_transition: Callable[[str, str, ExecutionResult], Awaitable[None]],
        schedule_post_response_maintenance: Callable[[str], None],
        maybe_schedule_skill_review: Callable[[str, ExecutionResult], None],
    ):
        self.run_trace = run_trace
        self.response_finalizer = response_finalizer
        self._connect_mcp = connect_mcp
        self._save_message = save_message
        self._emit_run_event = emit_run_event
        self._call_llm = call_llm
        self._get_queued_outbound_media = get_queued_outbound_media
        self._media_saved_ack = media_saved_ack
        self._llm_not_configured_message = llm_not_configured_message
        self._apply_immediate_task_transition = apply_immediate_task_transition
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
            "context_compactions": exec_result.context_compactions,
        }
        status_metadata = {
            "executed_tool_calls": exec_result.executed_tool_calls,
            "had_tool_error": exec_result.had_tool_error,
            "context_compactions": exec_result.context_compactions,
        }

        async def after_response_saved() -> None:
            await self._apply_immediate_task_transition(turn.session_chat_id, response, exec_result)
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
