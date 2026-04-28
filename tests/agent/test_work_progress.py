from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task_intent import TaskIntentService
from opensprite.agent.work_progress import WorkProgressService
from opensprite.storage import StoredWorkState


def test_work_progress_creates_coding_plan_from_intent():
    intent = TaskIntentService().classify("Please implement the feature and run tests.")

    plan = WorkProgressService().create_plan(intent)

    assert plan is not None
    assert plan.coding_task is True
    assert plan.long_running is True
    assert plan.expects_code_change is True
    assert plan.expects_verification is True
    assert plan.steps == (
        "inspect relevant code",
        "make the smallest correct change",
        "verify the result",
    )
    assert "relevant tests or checks pass" in plan.done_criteria[2]


def test_work_progress_tracks_verification_and_next_action():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    completion = CompletionGateResult(
        status="needs_verification",
        reason="required verification was not recorded",
        verification_required=True,
    )

    update = WorkProgressService().evaluate(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(
            content="Implemented.",
            executed_tool_calls=1,
            file_change_count=1,
            touched_paths=("src/agent.py",),
        ),
        auto_continue_attempts=0,
        pass_index=1,
    )

    assert update.status == "verifying"
    assert update.has_progress is True
    assert update.progress_signals == ("tool_calls", "file_changes")
    assert update.file_change_count == 1
    assert update.touched_paths == ("src/agent.py",)
    assert update.next_action == "continue_verification"
    assert update.continuation_budget == 3


def test_work_progress_stops_repeated_continuation_without_progress():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    completion = CompletionGateResult(status="needs_verification", reason="required verification was not recorded")

    update = WorkProgressService().evaluate(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="Still done."),
        auto_continue_attempts=1,
        pass_index=2,
    )

    assert update.has_progress is False
    assert update.next_action == "stop_no_progress"


def test_work_progress_resolves_vague_continue_from_existing_state():
    service = WorkProgressService()
    existing = StoredWorkState(
        session_id="web:browser-1",
        objective="Finish the refactor",
        kind="refactor",
        status="active",
        steps=("1. inspect", "2. change", "3. verify"),
        constraints=("Keep the public API stable",),
        done_criteria=("tests pass",),
        long_running=True,
        coding_task=True,
        expects_code_change=True,
        expects_verification=True,
    )
    vague_intent = TaskIntentService().classify("continue")

    resolved = service.resolve_intent(vague_intent, existing)

    assert resolved.objective == "Finish the refactor"
    assert resolved.kind == "refactor"
    assert resolved.expects_code_change is True
    assert resolved.expects_verification is True
    assert resolved.needs_clarification is False


def test_work_progress_resume_existing_state_preserves_progress_for_continue():
    service = WorkProgressService()
    intent = TaskIntentService().classify("continue")
    resolved = service.resolve_intent(
        intent,
        StoredWorkState(
            session_id="web:browser-1",
            objective="Finish the refactor",
            kind="refactor",
            status="active",
            steps=("1. inspect", "2. change", "3. verify"),
            constraints=("Keep the public API stable",),
            done_criteria=("tests pass",),
            long_running=True,
            coding_task=True,
            expects_code_change=True,
            expects_verification=True,
            current_step="2. change",
            next_step="3. verify",
            completed_steps=("1. inspect",),
            file_change_count=2,
            touched_paths=("src/agent.py",),
            pending_steps=("2. change", "3. verify"),
            verification_targets=("tests pass",),
            resume_hint="Resume at current step: 2. change",
            last_progress_signals=("file_changes",),
        ),
    )
    plan = service.create_plan(resolved)
    existing = StoredWorkState(
        session_id="web:browser-1",
        objective="Finish the refactor",
        kind="refactor",
        status="active",
        steps=("1. inspect", "2. change", "3. verify"),
        constraints=("Keep the public API stable",),
        done_criteria=("tests pass",),
        long_running=True,
        coding_task=True,
        expects_code_change=True,
        expects_verification=True,
        current_step="2. change",
        next_step="3. verify",
        completed_steps=("1. inspect",),
        file_change_count=2,
        touched_paths=("src/agent.py",),
        pending_steps=("2. change", "3. verify"),
        verification_targets=("tests pass",),
        resume_hint="Resume at current step: 2. change",
        last_progress_signals=("file_changes",),
    )

    resumed = service.build_initial_state(
        session_id="web:browser-1",
        task_intent=resolved,
        work_plan=plan,
        existing_state=existing,
    )

    assert resumed is not None
    assert resumed.current_step == "2. change"
    assert resumed.next_step == "3. verify"
    assert resumed.completed_steps == ("1. inspect",)
    assert resumed.file_change_count == 2
    assert resumed.resume_hint == "Resume at current step: 2. change"


def test_work_progress_extract_workboard_falls_back_to_legacy_metadata():
    state = StoredWorkState(
        session_id="web:browser-1",
        objective="Finish the refactor",
        kind="refactor",
        status="active",
        steps=("1. inspect", "2. change", "3. verify"),
        completed_steps=("1. inspect",),
        metadata={
            "workboard": {
                "pending_steps": ["2. change", "3. verify"],
                "blockers": ["Need user decision"],
                "verification_targets": ["tests pass"],
                "resume_hint": "Resume at current step: 2. change",
                "last_progress_signals": ["file_changes"],
            }
        },
    )

    workboard = WorkProgressService.extract_workboard(state)

    assert workboard.pending_steps == ("2. change", "3. verify")
    assert workboard.blockers == ("Need user decision",)
    assert workboard.verification_targets == ("tests pass",)
    assert workboard.resume_hint == "Resume at current step: 2. change"
    assert workboard.last_progress_signals == ("file_changes",)


def test_work_progress_updates_state_and_renders_summary():
    service = WorkProgressService()
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    plan = service.create_plan(intent)
    initial = service.build_initial_state(session_id="web:browser-1", task_intent=intent, work_plan=plan)
    assert initial is not None
    progress = service.evaluate(
        task_intent=intent,
        completion_result=CompletionGateResult(
            status="needs_verification",
            reason="required verification was not recorded",
            verification_required=True,
        ),
        execution_result=ExecutionResult(
            content="Refactor complete.",
            executed_tool_calls=1,
            file_change_count=2,
            touched_paths=("src/agent.py", "tests/test_agent.py"),
        ),
        auto_continue_attempts=0,
        pass_index=1,
    )

    updated = service.update_state(
        session_id="web:browser-1",
        state=initial,
        task_intent=intent,
        work_plan=plan,
        progress=progress,
        completion_result=CompletionGateResult(
            status="needs_verification",
            reason="required verification was not recorded",
            verification_required=True,
        ),
        delegate_task_id="task_abc12345",
        delegate_prompt_type="implementer",
    )

    assert updated is not None
    assert updated.file_change_count == 2
    assert updated.current_step == "3. verify the result"
    assert updated.active_delegate_task_id == "task_abc12345"
    workboard = WorkProgressService.extract_workboard(updated)
    assert workboard.pending_steps == ("3. verify the result",)
    assert workboard.verification_targets == (
        "relevant tests or checks pass, or the verification gap is stated",
    )
    assert workboard.resume_hint == "Resume by running or fixing the required verification."
    assert updated.pending_steps == ("3. verify the result",)
    assert updated.resume_hint == "Resume by running or fixing the required verification."
    summary = service.render_state_summary(updated)
    assert "Structured Work State" in summary
    assert "Active delegate: implementer (task_abc12345)" in summary
    assert "Pending steps:" in summary
    assert "Resume hint: Resume by running or fixing the required verification." in summary
    assert "src/agent.py" in summary
