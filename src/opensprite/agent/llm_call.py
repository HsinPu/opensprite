"""LLM prompt preparation and execution orchestration."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..config import AgentConfig
from ..llms import ChatMessage
from ..planning_mode import resolve_planning_mode
from ..tools import ToolRegistry
from ..utils.log import logger
from .execution import ExecutionResult
from .task_intent import TaskIntent


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
        llm_chat_max_tokens: Callable[[], int],
        sync_runtime_mcp_tools_context: Callable[[], None],
        build_messages: Callable[..., list[dict[str, Any]]],
        build_system_prompt: Callable[[str], str],
        log_prepared_messages: Callable[[str, list[dict[str, Any]]], None],
        get_work_state_summary: Callable[[str], Awaitable[str]],
        get_tool_registry: Callable[[], ToolRegistry],
        get_current_run_id: Callable[[], str | None],
        should_cancel_run: Callable[[str, str | None], bool],
        make_tool_progress_hook: Callable[..., Callable[[str, dict[str, Any]], Awaitable[None]] | None],
        make_tool_result_hook: Callable[..., Callable[[str, dict[str, Any], str], Awaitable[None]] | None],
        make_llm_status_hook: Callable[..., Callable[[str], Awaitable[None]] | None],
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
        self._llm_chat_max_tokens = llm_chat_max_tokens
        self._sync_runtime_mcp_tools_context = sync_runtime_mcp_tools_context
        self._build_messages = build_messages
        self._build_system_prompt = build_system_prompt
        self._log_prepared_messages = log_prepared_messages
        self._get_work_state_summary = get_work_state_summary
        self._get_tool_registry = get_tool_registry
        self._get_current_run_id = get_current_run_id
        self._should_cancel_run = should_cancel_run
        self._make_tool_progress_hook = make_tool_progress_hook
        self._make_tool_result_hook = make_tool_result_hook
        self._make_llm_status_hook = make_llm_status_hook
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
    ) -> ExecutionResult:
        """Prepare prompt messages and run the LLM/tool execution loop."""
        await self._maybe_seed_active_task(session_id, current_message, task_intent=task_intent)

        logger.info(f"[{session_id}] history.load | requested=true")
        history_messages = await self._load_history(session_id)

        # Tool results are only valid inside the turn where they were produced.
        filtered = []
        for m in history_messages:
            role = m.get("role", "?") if isinstance(m, dict) else getattr(m, "role", "?")
            if role != "tool":
                filtered.append(m)
        history_messages = filtered

        # The current user message is already passed explicitly to the context builder.
        # Drop the newest persisted user message for this turn to avoid duplicate/blank user entries.
        if history_messages:
            latest = history_messages[-1]
            latest_role = latest.get("role", "?") if isinstance(latest, dict) else getattr(latest, "role", "?")
            latest_content = latest.get("content", "") if isinstance(latest, dict) else getattr(latest, "content", "")
            if latest_role == "user" and latest_content == current_message:
                history_messages = history_messages[:-1]

        history_dicts = []
        for m in history_messages:
            if isinstance(m, dict):
                msg = {"role": m.get("role", "?"), "content": m.get("content", "")}
                if m.get("tool_call_id"):
                    msg["tool_call_id"] = m["tool_call_id"]
            else:
                msg = {"role": m.role, "content": m.content}
                if getattr(m, "tool_call_id", None):
                    msg["tool_call_id"] = m.tool_call_id
            history_dicts.append(msg)

        logger.info(
            f"[{session_id}] prompt.build | history={len(history_dicts)} channel={channel or '-'} images={len(user_images or [])}"
        )
        work_state_summary = await self._get_work_state_summary(session_id)
        if (
            work_state_summary
            and task_intent is not None
            and task_intent.objective.strip() != str(current_message or "").strip()
        ):
            current_message = (
                f"{current_message}\n\n"
                "Use the existing structured work state below as the source of truth for continuing the task.\n"
                f"{work_state_summary}"
            )
        current_audios = self._get_current_audios()
        current_videos = self._get_current_videos()
        prompt_message = self._augment_message_for_media(
            current_message,
            user_images,
            current_audios,
            current_videos,
            user_image_files=user_image_files,
            user_audio_files=user_audio_files,
            user_video_files=user_video_files,
        )
        planning_mode = resolve_planning_mode(current_message, base_registry=self._get_tool_registry())
        selected_tool_registry = planning_mode.tool_registry
        if planning_mode.enabled and selected_tool_registry is not None:
            logger.info(
                f"[{session_id}] prompt.mode | planning_mode=true allowed_tools={','.join(selected_tool_registry.tool_names)}"
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
        effective_context_budget = self._effective_context_token_budget()
        logger.info(
            f"[{session_id}] prompt.tokens | budget={effective_context_budget} "
            f"history_budget={self.config.history_token_budget} model_window={self._llm_context_window_tokens() or '-'} "
            f"output_reserve={self._llm_chat_max_tokens()} base={base_tokens} tools={tool_schema_tokens} "
            f"history={history_tokens} final_estimated={final_tokens}"
        )
        self._sync_runtime_mcp_tools_context()
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
            chat_messages.append(msg)

        self._log_prepared_messages(session_id, full_messages)
        run_id = self._get_current_run_id()
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
        execute_kwargs = {
            "allow_tools": allow_tools,
            "tool_result_session_id": session_id if allow_tools else None,
            "tool_registry": selected_tool_registry,
            "on_tool_before_execute": on_tool_before_execute,
            "on_llm_status": on_llm_status,
            "refresh_system_prompt": lambda: self._build_system_prompt(session_id),
            "should_cancel": lambda: self._should_cancel_run(session_id, run_id),
            "work_state_summary": work_state_summary,
        }
        if on_tool_after_execute is not None:
            execute_kwargs["on_tool_after_execute"] = on_tool_after_execute
        try:
            return await self._execute_messages(session_id, chat_messages, **execute_kwargs)
        except TypeError as exc:
            if "work_state_summary" not in str(exc) and "should_cancel" not in str(exc):
                raise
            execute_kwargs.pop("work_state_summary", None)
            execute_kwargs.pop("should_cancel", None)
            return await self._execute_messages(session_id, chat_messages, **execute_kwargs)
