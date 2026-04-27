from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task_intent import TaskIntentService


def test_completion_gate_requires_requested_verification_before_completion():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Completed the refactor.",
        execution_result=ExecutionResult(content="Completed the refactor."),
    )

    assert result.status == "needs_verification"
    assert result.reason == "required verification was not recorded"
    assert result.should_update_active_task is False


def test_completion_gate_marks_blocked_when_tool_error_reports_blocker():
    intent = TaskIntentService().classify("繼續驗證")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="目前無法繼續，測試環境失敗。",
        execution_result=ExecutionResult(
            content="目前無法繼續，測試環境失敗。",
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "blocked"
    assert result.active_task_status == "blocked"
    assert result.should_update_active_task is True


def test_completion_gate_marks_waiting_when_response_asks_for_input():
    intent = TaskIntentService().classify("繼續做")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="請問你要用哪個 target branch？",
        execution_result=ExecutionResult(content="請問你要用哪個 target branch？"),
    )

    assert result.status == "waiting_user"
    assert result.active_task_status == "waiting_user"
    assert result.should_update_active_task is True


def test_completion_gate_marks_explicit_task_completion_done():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(content="Implemented the final cleanup successfully."),
    )

    assert result.status == "complete"
    assert result.active_task_status == "done"
    assert result.should_update_active_task is True
