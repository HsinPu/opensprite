import asyncio

from opensprite.agent.task_contract import (
    PLANNER_METADATA_STATUS_FIELD,
    PLANNER_VALIDATED_STATUS,
    EvidenceRequirement,
    TaskContextDecision,
    TaskContract,
    TaskIntent,
    TaskObjectiveDecision,
)
from opensprite.agent.turn_planning import TurnPlanningService
from opensprite.harness import HarnessPlanningService, HarnessPolicyService, HarnessProfileService
from opensprite.runs.events import (
    HARNESS_POLICY_SELECTED_EVENT,
    HARNESS_PROFILE_SELECTED_EVENT,
    TASK_CONTRACT_CREATED_EVENT,
    TASK_CONTRACT_PLANNED_EVENT,
    TASK_CONTRACT_PLANNING_STARTED_EVENT,
    TASK_CONTRACT_VALIDATED_EVENT,
    TASK_CONTEXT_RESOLVED_EVENT,
    TASK_OBJECTIVE_RESOLVED_EVENT,
)
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class DummyTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Dummy {self._name} tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs) -> str:
        return "ok"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("read_file", "edit_file", "verify"):
        registry.register(DummyTool(name))
    return registry


def test_turn_planning_resolves_task_contract_and_harness_policy():
    async def scenario():
        events: list[tuple[str, dict]] = []
        seed_calls: list[dict] = []
        plan_calls: list[dict] = []
        profile_service = HarnessProfileService()
        policy_service = HarnessPolicyService()

        async def resolve_task_context(**kwargs):
            return TaskContextDecision(method="test_context", confidence=1.0, reason="test")

        async def resolve_task_objective(**kwargs):
            return TaskObjectiveDecision(
                original_message=kwargs["current_message"],
                resolved_objective="Refactor task and harness planning modules.",
                should_use_resolved_objective=True,
                method="test_objective",
                confidence=1.0,
                reason="test",
            )

        async def plan_task(**kwargs):
            plan_calls.append(dict(kwargs))
            return TaskContract(
                objective=kwargs["fallback_objective"],
                task_type="code_change",
                requirements=(EvidenceRequirement(kind="file_change", min_count=1),),
                allow_no_tool_final=False,
                planner_metadata={PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS},
            )

        async def maybe_seed_active_task(session_id, message, **kwargs):
            seed_calls.append({"session_id": session_id, "message": message, **kwargs})

        def augment_message_for_media(current_message, user_images, current_audios, current_videos, **kwargs):
            return current_message

        async def emit_run_event(session_id, run_id, event_type, payload, **kwargs):
            events.append((event_type, dict(payload)))

        service = TurnPlanningService(
            resolve_task_context=resolve_task_context,
            resolve_task_objective=resolve_task_objective,
            plan_task=plan_task,
            plan_harness=HarnessPlanningService(
                profile_service=profile_service,
                policy_service=policy_service,
            ).plan,
            maybe_seed_active_task=maybe_seed_active_task,
            augment_message_for_media=augment_message_for_media,
            emit_run_event=emit_run_event,
        )

        result = await service.plan(
            session_id="session-1",
            run_id="run-1",
            channel="web",
            external_chat_id="browser-1",
            current_message="Please clean up the planning flow.",
            history=[],
            task_intent=TaskIntent(kind="task", objective="Please clean up the planning flow."),
            task_contract_override=None,
            active_task_snapshot="",
            work_state_summary="",
            user_images=None,
            current_audios=None,
            current_videos=None,
            user_image_files=None,
            user_audio_files=None,
            user_video_files=None,
            base_tool_registry=_registry(),
        )
        return result, events, seed_calls, plan_calls

    result, events, seed_calls, plan_calls = asyncio.run(scenario())

    assert result.effective_task_intent is not None
    assert result.effective_task_intent.objective == "Refactor task and harness planning modules."
    assert result.task_contract is not None
    assert result.task_contract.harness_profile is not None
    assert result.task_contract.harness_profile["name"] == "coding"
    assert result.harness_profile is not None
    assert result.harness_profile.name == "coding"
    assert result.harness_policy is not None
    assert result.harness_policy.name == "workspace_change_guidance_policy"
    assert result.harness_tool_registry is not None
    assert seed_calls[0]["task_intent"].objective == "Refactor task and harness planning modules."
    assert plan_calls[0]["fallback_objective"] == "Refactor task and harness planning modules."

    event_types = [event_type for event_type, _payload in events]
    assert event_types[:8] == [
        TASK_CONTEXT_RESOLVED_EVENT,
        TASK_OBJECTIVE_RESOLVED_EVENT,
        TASK_CONTRACT_PLANNING_STARTED_EVENT,
        TASK_CONTRACT_PLANNED_EVENT,
        TASK_CONTRACT_VALIDATED_EVENT,
        HARNESS_PROFILE_SELECTED_EVENT,
        TASK_CONTRACT_CREATED_EVENT,
        HARNESS_POLICY_SELECTED_EVENT,
    ]
