import asyncio

from opensprite.documents.active_task import create_active_task_store
from opensprite.tools.active_task import TaskUpdateTool
from opensprite.tools.result_status import classify_tool_result_status


def _make_tool(tmp_path, session_id="telegram:user-a", message_count=7):
    store = create_active_task_store(tmp_path / "home", session_id)

    async def get_message_count(current_session_id: str) -> int:
        assert current_session_id == session_id
        return message_count

    tool = TaskUpdateTool(
        get_session_id=lambda: session_id,
        active_task_store_factory=lambda current_session_id: store if current_session_id == session_id else None,
        get_message_count=get_message_count,
    )
    return tool, store


def test_task_update_set_creates_active_task_and_event(tmp_path):
    tool, store = _make_tool(tmp_path)

    result = asyncio.run(
        tool.execute(action="set", task="Implement task_update tool and verify tests", note="starting")
    )

    assert result.startswith("Task set.")
    assert store.read_status() == "active"
    assert "Implement task_update tool" in store.read_managed_block()
    assert store.get_processed_index("telegram:user-a") == 7
    events = store.read_events()
    assert events[-1]["event_type"] == "set"
    assert events[-1]["source"] == "tool"
    assert events[-1]["details"]["note"] == "starting"


def test_task_update_update_fields_and_show(tmp_path):
    tool, store = _make_tool(tmp_path)
    asyncio.run(tool.execute(action="set", task="Refactor the agent in small safe steps"))

    result = asyncio.run(
        tool.execute(
            action="update",
            status="blocked",
            current_step="run focused tests",
            next_step="fix failing test",
            completed_step="inspect current implementation",
            open_questions=["pytest failed on test_execution"],
        )
    )
    shown = asyncio.run(tool.execute(action="show"))

    assert result.startswith("Task updated.")
    assert "- Status: blocked" in shown
    assert "- Current step: run focused tests" in shown
    assert "- Next step: fix failing test" in shown
    assert "  - inspect current implementation" in shown
    assert "  - pytest failed on test_execution" in shown
    assert store.read_events()[-1]["event_type"] == "update"


def test_task_update_complete_step_marks_done_without_next_step(tmp_path):
    tool, store = _make_tool(tmp_path)
    asyncio.run(tool.execute(action="set", task="Verify final behavior"))
    asyncio.run(
        tool.execute(
            action="update",
            current_step="run full test suite",
            next_step="not set",
        )
    )

    result = asyncio.run(tool.execute(action="complete_step", note="tests passed"))

    assert result.startswith("Task step completed.")
    assert store.read_status() == "done"
    assert "  - run full test suite" in store.read_managed_block()
    assert store.read_events()[-1]["details"]["note"] == "tests passed"


def test_task_update_advance_requires_next_step(tmp_path):
    tool, _store = _make_tool(tmp_path)
    asyncio.run(tool.execute(action="set", task="Finish a planned implementation"))
    asyncio.run(tool.execute(action="update", next_step="not set"))

    result = asyncio.run(tool.execute(action="advance"))

    status = classify_tool_result_status(result)
    assert status.ok is False
    assert status.error_type == "TaskUpdateToolError"
    assert status.category == "active_task_next_step_missing"
    assert "Next step is not set" in status.error


def test_task_update_reset_clears_active_task(tmp_path):
    tool, store = _make_tool(tmp_path)
    asyncio.run(tool.execute(action="set", task="Refactor something"))

    result = asyncio.run(tool.execute(action="reset"))

    assert result.startswith("Task reset.")
    assert store.read_status() == "inactive"
    assert store.get_processed_index("telegram:user-a") == 7
    assert store.read_events()[-1]["event_type"] == "reset"


def test_task_update_requires_active_session_context():
    tool = TaskUpdateTool(get_session_id=lambda: None)

    result = asyncio.run(tool.execute(action="show"))

    status = classify_tool_result_status(result)
    assert status.ok is False
    assert status.error_type == "ToolValidationError"
    assert status.category == "session_unavailable"
    assert status.invalid_arguments is True
    assert "requires an active session context" in status.error


def test_task_update_rejects_update_without_active_task(tmp_path):
    tool, _store = _make_tool(tmp_path)

    result = asyncio.run(tool.execute(action="update", status="active"))

    status = classify_tool_result_status(result)
    assert status.ok is False
    assert status.error_type == "TaskUpdateToolError"
    assert status.category == "active_task_missing"
    assert "Use action='set' first" in status.error


def test_task_update_rejects_update_without_fields(tmp_path):
    tool, _store = _make_tool(tmp_path)
    asyncio.run(tool.execute(action="set", task="Keep the task explicit"))

    result = asyncio.run(tool.execute(action="update"))
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.error_type == "ToolValidationError"
    assert status.category == "invalid_arguments"
    assert status.invalid_arguments is True
    assert "requires at least one field" in status.error
