from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task_intent import TaskIntentService
from opensprite.storage.base import StoredDelegatedTask


def test_completion_gate_requires_requested_verification_before_completion():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Completed the refactor.",
        execution_result=ExecutionResult(
            content="Completed the refactor.",
            file_change_count=1,
            touched_paths=("src/agent.py",),
        ),
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
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="No major findings.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "No major findings.",
                            "finding_count": 0,
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "complete"
    assert result.active_task_status == "done"
    assert result.should_update_active_task is True


def test_completion_gate_requires_recorded_code_changes_for_implementation():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(content="Implemented the final cleanup successfully."),
    )

    assert result.status == "incomplete"
    assert result.reason == "expected code changes were not recorded"


def test_completion_gate_requires_review_for_code_changes_without_review_evidence():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "delegated review was not recorded for code changes"
    assert result.review_required is True
    assert result.review_attempted is False


def test_completion_gate_requires_follow_up_when_review_reports_findings():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="One correctness risk found.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "One correctness risk found.",
                            "finding_count": 1,
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "delegated review reported findings that require follow-up"
    assert result.review_attempted is True
    assert result.review_passed is False
    assert result.review_finding_count == 1


def test_completion_gate_allows_review_without_code_changes():
    intent = TaskIntentService().classify("Please review the recent changes for regressions.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Found two regressions tied to src/app.py and tests/test_app.py.",
        execution_result=ExecutionResult(content="Found two regressions tied to src/app.py and tests/test_app.py."),
    )

    assert result.status == "complete"
    assert result.reason == "analysis-style task returned a substantive response"


def test_completion_gate_allows_debug_diagnosis_without_code_changes():
    intent = TaskIntentService().classify("Please investigate why the build is failing.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="The build fails because the generated config file is missing at startup.",
        execution_result=ExecutionResult(content="The build fails because the generated config file is missing at startup."),
    )

    assert result.status == "complete"
    assert result.reason == "debug diagnosis was provided without requiring code changes"
