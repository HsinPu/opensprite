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
        verification_action="pytest",
        verification_path=".",
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
    assert decision.direct_verify_action == "pytest"
    assert decision.direct_verify_path == "."
    assert "Verification is required" in decision.prompt
    assert "verify(action=\"pytest\", path=\".\")" in decision.prompt


def test_auto_continue_skips_direct_verify_when_verify_tool_is_unavailable():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    completion = CompletionGateResult(
        status="needs_verification",
        reason="required verification was not recorded",
        verification_required=True,
        verification_action="pytest",
        verification_path=".",
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Completed the refactor."),
        attempts_used=0,
        previous_response="Completed the refactor.",
        verification_available=False,
    )

    assert decision.should_continue is True
    assert decision.direct_verify_action is None


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
    assert decision.direct_workflow == "implement_then_review"
    assert decision.direct_start_step == "implement"
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


def test_auto_continue_guides_retry_after_missing_task_artifacts():
    intent = TaskIntentService().classify("Please inspect all attached images and summarize them.")
    completion = CompletionGateResult(
        status="incomplete",
        reason="required task artifacts were not produced",
        active_task_detail="- Missing artifact for image:images/a.jpg",
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="I checked the image.", executed_tool_calls=1),
        attempts_used=0,
        previous_response="I checked the image.",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "typed artifacts for every required resource" in (decision.prompt or "")
    assert "Use the relevant media/source tools" in (decision.prompt or "")
    assert "Missing artifact for image:images/a.jpg" in (decision.prompt or "")


def test_auto_continue_guides_retry_after_terse_final_answer():
    intent = TaskIntentService().classify("Please inspect all attached images and summarize them.")
    completion = CompletionGateResult(
        status="incomplete",
        reason="assistant final answer was too terse for the task",
        active_task_detail="Provide a substantive final answer that uses the inspected media results.",
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Done.", executed_tool_calls=1),
        attempts_used=0,
        previous_response="Done.",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "previous final answer was too terse" in (decision.prompt or "")
    assert "Do not reply with only 'done'" in (decision.prompt or "")
    assert "substantive final answer" in (decision.prompt or "")


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
    assert decision.direct_workflow == "implement_then_review"
    assert decision.direct_start_step == "review"
    assert "The missing work is already identified" in (decision.prompt or "")
    assert "Workflow follow-up target: implement_then_review -> Code review" in (decision.prompt or "")
    assert "run_workflow(workflow=\"implement_then_review\", task=<original objective>, start_step=\"review\")" in (decision.prompt or "")
    assert "Prefer a delegated `code-reviewer` step" in (decision.prompt or "")
    assert "Required follow-up: Resume with the Code review step in implement_then_review." in (decision.prompt or "")


def test_auto_continue_allows_second_direct_resume_when_target_changes():
    intent = TaskIntentService().classify("Please implement the cleanup.")
    completion = CompletionGateResult(
        status="needs_review",
        reason="workflow implement_then_review completed but review findings still require follow-up",
        follow_up_workflow="implement_then_review",
        follow_up_step_id="implement",
        follow_up_step_label="Implement",
        follow_up_prompt_type="implementer",
        review_required=True,
        review_attempted=True,
        review_finding_count=1,
        active_task_detail="src/foo.py: Null handling bug: Guard the null path before dereference.",
    )
    progress = WorkProgressService().evaluate(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Need one more pass.", executed_tool_calls=1),
        auto_continue_attempts=1,
        pass_index=2,
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Need one more pass.", executed_tool_calls=1),
        attempts_used=1,
        previous_response="Need one more pass.",
        work_progress=progress,
        last_direct_workflow="implement_then_review",
        last_direct_start_step="review",
    )

    assert decision.should_continue is True
    assert decision.direct_workflow == "implement_then_review"
    assert decision.direct_start_step == "implement"


def test_auto_continue_skips_second_direct_resume_when_target_is_unchanged():
    intent = TaskIntentService().classify("Please implement the cleanup.")
    completion = CompletionGateResult(
        status="needs_review",
        reason="workflow implement_then_review completed but review findings still require follow-up",
        follow_up_workflow="implement_then_review",
        follow_up_step_id="implement",
        follow_up_step_label="Implement",
        follow_up_prompt_type="implementer",
        review_required=True,
        review_attempted=True,
        review_finding_count=1,
        active_task_detail="src/foo.py: Null handling bug: Guard the null path before dereference.",
    )
    progress = WorkProgressService().evaluate(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Need one more pass.", executed_tool_calls=1),
        auto_continue_attempts=1,
        pass_index=2,
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Need one more pass.", executed_tool_calls=1),
        attempts_used=1,
        previous_response="Need one more pass.",
        work_progress=progress,
        last_direct_workflow="implement_then_review",
        last_direct_start_step="implement",
    )

    assert decision.should_continue is False
    assert decision.reason == "review_findings_require_follow_up"


def test_auto_continue_limits_same_target_verify_retries():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    completion = CompletionGateResult(
        status="needs_verification",
        reason="required verification did not pass",
        verification_required=True,
        verification_attempted=True,
        verification_passed=False,
        verification_action="pytest",
        verification_path=".",
    )
    progress = WorkProgressService().evaluate(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Error: Verification failed: pytest", executed_tool_calls=1, had_tool_error=True, verification_attempted=True),
        auto_continue_attempts=3,
        pass_index=4,
    )

    allowed = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Error: Verification failed: pytest", executed_tool_calls=1, had_tool_error=True, verification_attempted=True),
        attempts_used=3,
        previous_response="Verification failed.",
        work_progress=progress,
        direct_actions_used=3,
        last_direct_verify_action="pytest",
        last_direct_verify_path=".",
        same_target_verify_attempts=1,
    )
    blocked = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Verification did not pass.", executed_tool_calls=1, verification_attempted=True),
        attempts_used=1,
        previous_response="Verification failed again.",
        work_progress=progress,
        direct_actions_used=2,
        last_direct_verify_action="pytest",
        last_direct_verify_path=".",
        same_target_verify_attempts=2,
    )

    assert allowed.should_continue is True
    assert allowed.direct_verify_action == "pytest"
    assert blocked.should_continue is True
    assert blocked.direct_verify_action is None


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
