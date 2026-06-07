"""Task and harness planning for one LLM-backed user turn."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from ..documents.active_task import has_current_active_task
from ..harness import HarnessPlan, HarnessPolicy, HarnessProfile, is_chat_profile_name
from ..runs.events import (
    HARNESS_POLICY_MERGE_RESOLVED_EVENT,
    HARNESS_POLICY_SELECTED_EVENT,
    HARNESS_PROFILE_SELECTED_EVENT,
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
from .task_contract import (
    PLANNER_VALIDATED_STATUS,
    TaskContextDecision,
    TaskContract,
    TaskIntent,
    TaskObjectiveDecision,
    task_planner_status,
)


RunEventEmitter = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class TurnPlanningResult:
    """Planning output consumed by the execution loop."""

    task_context_decision: TaskContextDecision | None
    task_objective_decision: TaskObjectiveDecision | None
    effective_task_intent: TaskIntent | None
    effective_current_message: str
    prompt_message: str
    task_contract: TaskContract | None
    harness_profile: HarnessProfile | None
    harness_policy: HarnessPolicy | None
    harness_tool_registry: ToolRegistry | None


class TurnPlanningService:
    """Resolve task contract and harness policy before prompt execution."""

    def __init__(
        self,
        *,
        resolve_task_context: Callable[..., Awaitable[TaskContextDecision]],
        resolve_task_objective: Callable[..., Awaitable[TaskObjectiveDecision]],
        plan_task: Callable[..., Awaitable[TaskContract]],
        plan_harness: Callable[[TaskContract, ToolRegistry], HarnessPlan],
        maybe_seed_active_task: Callable[..., Awaitable[None]],
        augment_message_for_media: Callable[..., str],
        emit_run_event: RunEventEmitter,
    ) -> None:
        self._resolve_task_context = resolve_task_context
        self._resolve_task_objective = resolve_task_objective
        self._plan_task = plan_task
        self._plan_harness = plan_harness
        self._maybe_seed_active_task = maybe_seed_active_task
        self._augment_message_for_media = augment_message_for_media
        self._emit_run_event = emit_run_event

    async def plan(
        self,
        *,
        session_id: str,
        run_id: str | None,
        channel: str | None,
        external_chat_id: str | None,
        current_message: str,
        history: list[dict[str, Any]],
        task_intent: TaskIntent | None,
        task_contract_override: TaskContract | None,
        active_task_snapshot: str,
        work_state_summary: str,
        user_images: list[str] | None,
        current_audios: list[str] | None,
        current_videos: list[str] | None,
        user_image_files: list[str] | None,
        user_audio_files: list[str] | None,
        user_video_files: list[str] | None,
        base_tool_registry: ToolRegistry,
    ) -> TurnPlanningResult:
        task_context_decision = None
        task_objective_decision = None
        if task_intent is not None:
            task_context_decision = await self._resolve_task_context(
                current_message=current_message,
                history=history,
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
                history=history,
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
        harness_profile = None
        harness_policy = None
        harness_tool_registry = None
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
                            "history_messages": len(history),
                        },
                        channel=channel,
                        external_chat_id=external_chat_id,
                    )
                task_contract = await self._plan_task(
                    tool_registry=base_tool_registry,
                    fallback_objective=getattr(effective_task_intent, "objective", ""),
                    current_message=prompt_message,
                    history=history,
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
            harness_plan = self._plan_harness(task_contract, base_tool_registry)
            task_contract = harness_plan.task_contract
            harness_profile = harness_plan.harness_profile
            harness_policy = harness_plan.harness_policy
            harness_tool_registry = harness_plan.tool_registry
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
        return TurnPlanningResult(
            task_context_decision=task_context_decision,
            task_objective_decision=task_objective_decision,
            effective_task_intent=effective_task_intent,
            effective_current_message=effective_current_message,
            prompt_message=prompt_message,
            task_contract=task_contract,
            harness_profile=harness_profile,
            harness_policy=harness_policy,
            harness_tool_registry=harness_tool_registry,
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
