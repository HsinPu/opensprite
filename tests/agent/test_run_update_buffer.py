from opensprite.agent.run_update_buffer import RunUpdateBuffer
from opensprite.storage import StoredDelegatedTask


def test_run_update_buffer_merges_delegated_task_updates_by_task_id():
    buffer = RunUpdateBuffer()

    buffer.record_delegated_task_update(
        "run-1",
        StoredDelegatedTask(
            task_id="task-1",
            prompt_type="coding",
            status="failed",
            selected=True,
            summary="first summary",
            error="first error",
            child_session_id="child-1",
            last_child_run_id="child-run-1",
            metadata={"first": True},
            created_at=10.0,
            updated_at=11.0,
        ),
    )
    buffer.record_delegated_task_update(
        "run-1",
        StoredDelegatedTask(
            task_id="task-1",
            status="completed",
            selected=False,
            summary="done",
            metadata={"second": True},
            updated_at=12.0,
        ),
    )

    updates = buffer.consume_delegated_task_updates("run-1")

    assert len(updates) == 1
    update = updates[0]
    assert update.task_id == "task-1"
    assert update.prompt_type == "coding"
    assert update.status == "completed"
    assert update.summary == "done"
    assert update.error == ""
    assert update.child_session_id == "child-1"
    assert update.last_child_run_id == "child-run-1"
    assert update.metadata == {"first": True, "second": True}
    assert update.created_at == 10.0
    assert update.updated_at == 12.0
    assert buffer.consume_delegated_task_updates("run-1") == ()


def test_run_update_buffer_keeps_latest_workflow_outcome_by_workflow_run_id():
    buffer = RunUpdateBuffer()

    buffer.record_workflow_outcome("run-1", {"workflow_run_id": "wf-1", "status": "running"})
    buffer.record_workflow_outcome("run-1", {"workflow_run_id": "wf-1", "status": "completed"})
    buffer.record_workflow_outcome("run-1", {"workflow_run_id": "wf-2", "status": "failed"})

    outcomes = buffer.consume_workflow_outcomes("run-1")

    assert outcomes == (
        {"workflow_run_id": "wf-1", "status": "completed"},
        {"workflow_run_id": "wf-2", "status": "failed"},
    )
    assert buffer.consume_workflow_outcomes("run-1") == ()
