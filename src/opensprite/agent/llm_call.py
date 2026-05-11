"""LLM prompt preparation and execution orchestration."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..config import AgentConfig
from ..llms import ChatMessage
from .planning_mode import resolve_planning_mode
from ..tools import ToolRegistry
from ..utils.log import logger
from .execution import ExecutionResult
from .task_contract import TaskContract, TaskContractService
from .task_context_resolver import TaskContextDecision
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
        read_active_task_snapshot: Callable[[str], str],
        resolve_task_context: Callable[..., Awaitable[TaskContextDecision]],
        build_proactive_retrieval_context: Callable[..., Awaitable[str]],
        get_tool_registry: Callable[[], ToolRegistry],
        get_current_run_id: Callable[[], str | None],
        should_cancel_run: Callable[[str, str | None], bool],
        make_tool_progress_hook: Callable[..., Callable[[str, dict[str, Any]], Awaitable[None]] | None],
        make_tool_result_hook: Callable[..., Callable[[str, dict[str, Any], str], Awaitable[None]] | None],
        make_llm_status_hook: Callable[..., Callable[[str], Awaitable[None]] | None],
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
        self._llm_chat_max_tokens = llm_chat_max_tokens
        self._sync_runtime_mcp_tools_context = sync_runtime_mcp_tools_context
        self._build_messages = build_messages
        self._build_system_prompt = build_system_prompt
        self._log_prepared_messages = log_prepared_messages
        self._get_work_state_summary = get_work_state_summary
        self._read_active_task_snapshot = read_active_task_snapshot
        self._resolve_task_context = resolve_task_context
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
    ) -> ExecutionResult:
        """Prepare prompt messages and run the LLM/tool execution loop."""
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
        work_state_summary = await self._get_work_state_summary(session_id)
        active_task_snapshot = self._read_active_task_snapshot(session_id)
        task_context_decision = None
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
        await self._maybe_seed_active_task(
            session_id,
            current_message,
            task_intent=task_intent,
            task_context_decision=task_context_decision,
        )
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
        task_contract = None
        if task_intent is not None:
            task_contract = TaskContractService.build(
                task_intent=task_intent,
                current_message=prompt_message,
                history=history_dicts,
                current_image_files=user_image_files,
                current_audio_files=user_audio_files,
                current_video_files=user_video_files,
                task_context_decision=task_context_decision,
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
        proactive_retrieval_context = await self._build_proactive_retrieval_context(
            session_id=session_id,
            current_message=current_message,
        )
        if proactive_retrieval_context:
            history_dicts = [{"role": "system", "content": proactive_retrieval_context}, *history_dicts]
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
            if m.get("reasoning_details"):
                msg.reasoning_details = m["reasoning_details"]
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
        try:
            result = await self._execute_messages(session_id, chat_messages, **execute_kwargs)
            result.task_contract = task_contract
            return result
        except TypeError as exc:
            message = str(exc)
            if (
                "work_state_summary" not in message
                and "should_cancel" not in message
                and "on_response_delta" not in message
                and "on_tool_input_delta" not in message
                and "on_reasoning_delta" not in message
            ):
                raise
            execute_kwargs.pop("work_state_summary", None)
            execute_kwargs.pop("should_cancel", None)
            execute_kwargs.pop("on_response_delta", None)
            execute_kwargs.pop("on_tool_input_delta", None)
            execute_kwargs.pop("on_reasoning_delta", None)
            result = await self._execute_messages(session_id, chat_messages, **execute_kwargs)
            result.task_contract = task_contract
            return result


def _build_task_contract_guidance(contract: TaskContract) -> str:
    if not (contract.requirements or contract.acceptance_criteria or contract.selected_resources):
        return ""
    lines = [
        "## Runtime Task Contract",
        "Satisfy these deterministic completion requirements before giving the final answer.",
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


def _format_acceptance_criterion(criterion: Any) -> str:
    if criterion.kind == "itemized_output":
        return f"Provide at least {max(1, int(criterion.min_count or 1))} itemized result entries; do not answer with only a plan or acknowledgement."
    if criterion.kind == "substantive_final_answer":
        min_chars = max(1, int(getattr(criterion, "min_response_chars", 0) or 1))
        return f"Write a substantive final answer using the inspected media/tool results (minimum {min_chars} visible characters)."
    if criterion.kind == "source_artifact":
        return f"Produce at least {max(1, int(criterion.min_count or 1))} traceable source(s) from web/source tools before finalizing."
    if criterion.kind == "source_detail":
        return "Fetch or inspect at least one source page before finalizing; search result snippets alone are not sufficient."
    if criterion.kind == "source_reference":
        return "Reference at least one gathered source by URL, domain, or title in the final answer."
    return criterion.description or criterion.kind
