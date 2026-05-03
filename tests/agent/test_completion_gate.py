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
    assert "delegated review step" in (result.active_task_detail or "")


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
    assert "correctness risk" in (result.active_task_detail or "")


def test_completion_gate_prefers_structured_review_fix_for_follow_up_detail():
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
                    summary="One high-risk bug found.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "One high-risk bug found.",
                            "finding_count": 1,
                            "sections": [
                                {
                                    "key": "findings",
                                    "title": "Review Findings",
                                    "type": "finding_list",
                                    "items": [
                                        {
                                            "title": "Null handling bug",
                                            "path": "src/foo.py",
                                            "why": "Empty input can raise an exception.",
                                            "fix": "Guard the null path before dereference.",
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.active_task_detail == "src/foo.py: Null handling bug: Guard the null path before dereference."


def test_completion_gate_allows_workflow_completion_with_clean_review():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="No major findings.",
                    metadata={"structured_output": {"status": "ok", "summary": "No major findings.", "finding_count": 0}},
                ),
            ),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": True,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "complete"
    assert result.reason == "workflow implement_then_review completed with clean review evidence"


def test_completion_gate_uses_workflow_review_finding_detail_without_delegated_tasks():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": False,
                    "review_finding_count": 1,
                    "review_summary": "One high-risk bug found.",
                    "review_first_finding": "src/foo.py: Null handling bug: Guard the null path before dereference.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "workflow implement_then_review completed but review findings still require follow-up"
    assert result.active_task_detail == "src/foo.py: Null handling bug: Guard the null path before dereference."
    assert result.follow_up_workflow == "implement_then_review"
    assert result.follow_up_step_id == "address_review_findings"
    assert result.follow_up_step_label == "Address review findings"
    assert result.review_attempted is True
    assert result.review_finding_count == 1


def test_completion_gate_sets_workflow_review_step_target_when_review_is_missing():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": False,
                    "review_passed": False,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.follow_up_workflow == "implement_then_review"
    assert result.follow_up_step_id == "review"
    assert result.follow_up_step_label == "Code review"
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_marks_blocked_when_workflow_fails():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow failed.",
        execution_result=ExecutionResult(
            content="Workflow failed.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "failed",
                    "next_step_id": "review",
                    "next_step_label": "Code review",
                    "next_step_prompt_type": "code-reviewer",
                    "error": "review step failed",
                },
            ),
        ),
    )

    assert result.status == "blocked"
    assert result.reason == "workflow implement_then_review did not complete successfully"
    assert result.active_task_detail == "Resolve the Code review step failure in implement_then_review: review step failed"
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_marks_incomplete_when_workflow_is_cancelled():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow cancelled.",
        execution_result=ExecutionResult(
            content="Workflow cancelled.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "cancelled",
                    "next_step_id": "review",
                    "next_step_label": "Code review",
                    "next_step_prompt_type": "code-reviewer",
                    "error": "cancelled",
                    "summary": "Workflow stopped after 1/2 completed step(s).",
                },
            ),
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "workflow implement_then_review did not complete successfully"
    assert result.active_task_detail == (
        "Resume with the Code review step in implement_then_review. "
        "Workflow stopped after 1/2 completed step(s)."
    )
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_allows_research_then_outline_without_completion_phrase():
    intent = TaskIntentService().classify("Help me research and outline this topic.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow result attached below.",
        execution_result=ExecutionResult(
            content="Workflow result attached below.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_outline",
                    "workflow": "research_then_outline",
                    "status": "completed",
                    "review_attempted": False,
                    "review_passed": False,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "complete"
    assert result.reason == "workflow research_then_outline completed all required steps"


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
