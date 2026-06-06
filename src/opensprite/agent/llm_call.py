"""LLM prompt preparation and execution orchestration."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable

from ..config import AgentConfig
from ..llms import CHAT_ROLE_SYSTEM, CHAT_ROLE_TOOL, CHAT_ROLE_USER, ChatMessage
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
from ..tools import ToolRegistry
from ..utils.log import logger
from ..documents.active_task import has_current_active_task
from .execution import ExecutionResult
from .harness_policy import HarnessPolicy
from .harness_profile import (
    HISTORY_RETRIEVAL_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP,
    HarnessProfile,
    is_chat_profile_name,
)
from .mcp_lifecycle import mcp_tool_names as list_mcp_tool_names
from .task_contract import (
    PLANNER_METADATA_STATUS_FIELD,
    PLANNER_VALIDATED_STATUS,
    TaskContract,
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
)
from .task_resolver import TaskContextDecision, TaskObjectiveDecision
from .task_resolver import TaskIntent


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
                        if _task_planner_status(task_contract) == PLANNER_VALIDATED_STATUS
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


def _task_planner_status(contract: TaskContract | None) -> str:
    metadata = getattr(contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get(PLANNER_METADATA_STATUS_FIELD) or "").strip()
    return ""
