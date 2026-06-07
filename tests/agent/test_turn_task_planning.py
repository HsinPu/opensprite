from opensprite.agent.task_contract import TaskIntentService
from opensprite.agent.turn_runner import WorkProgressService
from opensprite.agent.turn_task_planning import TurnTaskPlanningService
from opensprite.bus.message import UserMessage


def test_turn_task_planning_builds_intent_context_and_initial_work_state():
    runtime_messages: list[tuple[str, dict | None]] = []
    service = TurnTaskPlanningService(
        task_intents=TaskIntentService(),
        work_progress=WorkProgressService(),
        read_active_task_snapshot=lambda session_id: "",
        build_runtime_message=lambda message, metadata: _record_runtime_message(
            runtime_messages,
            message,
            metadata,
        ),
    )

    result = service.plan(
        user_message=UserMessage(
            text="Please refactor the agent and run tests.",
            metadata={"source": "cli_via_web"},
        ),
        session_id="web:browser-1",
        user_metadata={"source": "cli_via_web"},
        existing_work_state=None,
    )

    assert runtime_messages == [("Please refactor the agent and run tests.", {"source": "cli_via_web"})]
    assert result.task_intent.objective == "Please refactor the agent and run tests."
    assert result.task_intent.kind == "task"
    assert result.task_context_decision.method == "deterministic"
    assert result.work_plan is not None
    assert result.current_work_state is not None
    assert result.current_work_state.objective == result.task_intent.objective


def _record_runtime_message(
    calls: list[tuple[str, dict | None]],
    message: str,
    metadata: dict | None,
) -> str:
    calls.append((message, metadata))
    return f"{message}\n\n[Runtime context]\n- test"
