from opensprite.agent.auto_continue import AutoContinueService
from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task_intent import TaskIntentService
from opensprite.agent.work_progress import WorkProgressService


def test_auto_continue_allows_missing_verification_once():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    completion = CompletionGateResult(
        status="needs_verification",
        reason="required verification was not recorded",
        verification_required=True,
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Completed the refactor."),
        attempts_used=0,
        previous_response="Completed the refactor.",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_needs_verification"
    assert "Verification is required" in decision.prompt


def test_auto_continue_allows_missing_review_once():
    intent = TaskIntentService().classify("Please implement the cleanup.")
    completion = CompletionGateResult(
        status="needs_review",
        reason="delegated review was not recorded for code changes",
        review_required=True,
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Implemented the cleanup."),
        attempts_used=0,
        previous_response="Implemented the cleanup.",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_needs_review"
    assert "Review evidence is required" in decision.prompt


def test_auto_continue_uses_review_finding_detail_when_follow_up_is_needed():
    intent = TaskIntentService().classify("Please implement the cleanup.")
    completion = CompletionGateResult(
        status="needs_review",
        reason="delegated review reported findings that require follow-up",
        follow_up_workflow="implement_then_review",
        follow_up_step_id="implement",
        follow_up_step_label="Implement",
        follow_up_prompt_type="implementer",
        review_required=True,
        review_attempted=True,
        review_finding_count=1,
        active_task_detail="src/foo.py: Null handling bug: Guard the null path before dereference.",
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Implemented the cleanup."),
        attempts_used=0,
        previous_response="Implemented the cleanup.",
    )

    assert decision.should_continue is True
    assert "Workflow follow-up target: implement_then_review -> Implement" in (decision.prompt or "")
    assert "run_workflow(workflow=\"implement_then_review\", task=<original objective>, start_step=\"implement\")" in (decision.prompt or "")
    assert "Prefer a delegated `implementer` step" in (decision.prompt or "")
    assert "Review findings already exist" in (decision.prompt or "")
    assert "Required follow-up: src/foo.py: Null handling bug: Guard the null path before dereference." in (decision.prompt or "")


def test_auto_continue_skips_waiting_and_blocked_statuses():
    intent = TaskIntentService().classify("Continue the task")
    service = AutoContinueService(max_auto_continues=1)

    waiting = service.decide(
        task_intent=intent,
        completion_result=CompletionGateResult(status="waiting_user", reason="needs input"),
        execution_result=ExecutionResult(content="Which branch should I use?"),
        attempts_used=0,
        previous_response="Which branch should I use?",
    )
    blocked = service.decide(
        task_intent=intent,
        completion_result=CompletionGateResult(status="blocked", reason="blocked"),
        execution_result=ExecutionResult(content="Cannot continue."),
        attempts_used=0,
        previous_response="Cannot continue.",
    )

    assert waiting.should_continue is False
    assert waiting.emit_skipped_event is False
    assert blocked.should_continue is False
    assert blocked.emit_skipped_event is False


def test_auto_continue_stops_at_max_attempts():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    completion = CompletionGateResult(status="needs_verification", reason="required verification was not recorded")

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Still done."),
        attempts_used=1,
        previous_response="Still done.",
    )

    assert decision.should_continue is False
    assert decision.reason == "max_auto_continues_reached"
    assert decision.emit_skipped_event is True


def test_auto_continue_skips_incomplete_without_tool_progress():
    intent = TaskIntentService().classify("Please analyze the incident timeline.")
    completion = CompletionGateResult(status="incomplete", reason="not explicit")

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="I will do that."),
        attempts_used=0,
        previous_response="I will do that.",
    )

    assert decision.should_continue is False
    assert decision.reason == "no_tool_progress_after_incomplete_response"
    assert decision.emit_skipped_event is True


def test_auto_continue_allows_one_coding_retry_when_code_changes_are_missing():
    intent = TaskIntentService().classify("Please implement the cleanup.")
    completion = CompletionGateResult(status="incomplete", reason="expected code changes were not recorded")

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Implemented the cleanup."),
        attempts_used=0,
        previous_response="Implemented the cleanup.",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"


def test_auto_continue_uses_step_level_follow_up_for_incomplete_workflow():
    intent = TaskIntentService().classify("Please implement the cleanup.")
    completion = CompletionGateResult(
        status="incomplete",
        reason="workflow implement_then_review did not complete successfully",
        follow_up_workflow="implement_then_review",
        follow_up_step_id="review",
        follow_up_step_label="Code review",
        follow_up_prompt_type="code-reviewer",
        active_task_detail="Resume with the Code review step in implement_then_review. Workflow stopped after 1/2 completed step(s).",
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Workflow cancelled.", executed_tool_calls=1),
        attempts_used=0,
        previous_response="Workflow cancelled.",
    )

    assert decision.should_continue is True
    assert "The missing work is already identified" in (decision.prompt or "")
    assert "Workflow follow-up target: implement_then_review -> Code review" in (decision.prompt or "")
    assert "run_workflow(workflow=\"implement_then_review\", task=<original objective>, start_step=\"review\")" in (decision.prompt or "")
    assert "Prefer a delegated `code-reviewer` step" in (decision.prompt or "")
    assert "Required follow-up: Resume with the Code review step in implement_then_review." in (decision.prompt or "")


def test_auto_continue_uses_work_progress_budget_and_stops_without_progress():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    completion = CompletionGateResult(status="needs_verification", reason="required verification was not recorded")
    progress = WorkProgressService().evaluate(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Still done."),
        auto_continue_attempts=1,
        pass_index=2,
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Still done."),
        attempts_used=1,
        previous_response="Still done.",
        work_progress=progress,
    )

    assert decision.should_continue is False
    assert decision.reason == "no_progress_during_continuation"
    assert decision.max_attempts == 3
    assert decision.emit_skipped_event is True
