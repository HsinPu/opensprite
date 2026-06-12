from opensprite.agent.task.contract import TaskContract
from opensprite.agent.task.planning_mode import resolve_planning_mode


def test_planning_mode_uses_task_contract_task_type():
    state = resolve_planning_mode(
        task_contract=TaskContract(
            objective="Propose the next cleanup slice.",
            task_type="planning",
        ),
    )

    assert state.enabled is True
    assert "read-only planning mode" in state.overlay


def test_planning_mode_does_not_enable_for_non_planning_contract_without_explicit_request():
    state = resolve_planning_mode(
        task_contract=TaskContract(
            objective="Inspect the project.",
            task_type="workspace_read",
        ),
    )

    assert state.enabled is False
