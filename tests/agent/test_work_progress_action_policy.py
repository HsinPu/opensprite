from opensprite.agent.work_progress_action_policy import (
    NEXT_ACTION_ADDRESS_REVIEW_FINDINGS,
    NEXT_ACTION_COLLECT_REVIEW_EVIDENCE,
    NEXT_ACTION_CONTINUE_REVIEW,
    NEXT_ACTION_CONTINUE_VERIFICATION,
    NEXT_ACTION_CONTINUE_WORK,
    build_resume_hint,
    is_continue_work_next_action,
    is_review_follow_up_next_action,
    is_review_phase_next_action,
    is_verification_next_action,
)


def test_work_progress_action_policy_classifies_core_next_actions():
    assert is_verification_next_action(NEXT_ACTION_CONTINUE_VERIFICATION) is True
    assert is_verification_next_action(NEXT_ACTION_CONTINUE_WORK) is False
    assert is_continue_work_next_action(NEXT_ACTION_CONTINUE_WORK) is True
    assert is_continue_work_next_action(NEXT_ACTION_CONTINUE_VERIFICATION) is False


def test_work_progress_action_policy_distinguishes_review_phase_and_follow_up_actions():
    assert is_review_follow_up_next_action(NEXT_ACTION_COLLECT_REVIEW_EVIDENCE) is True
    assert is_review_follow_up_next_action(NEXT_ACTION_ADDRESS_REVIEW_FINDINGS) is True
    assert is_review_follow_up_next_action(NEXT_ACTION_CONTINUE_REVIEW) is False

    assert is_review_phase_next_action(NEXT_ACTION_COLLECT_REVIEW_EVIDENCE) is True
    assert is_review_phase_next_action(NEXT_ACTION_ADDRESS_REVIEW_FINDINGS) is True
    assert is_review_phase_next_action(NEXT_ACTION_CONTINUE_REVIEW) is True
    assert is_review_phase_next_action(NEXT_ACTION_CONTINUE_WORK) is False


def test_work_progress_action_policy_builds_verification_resume_hints():
    assert build_resume_hint(
        status="active",
        current_step="",
        next_step="",
        blockers=(),
        next_action=NEXT_ACTION_CONTINUE_VERIFICATION,
        verification_action="pytest",
        verification_path=".",
    ) == "Resume by running verify pytest for `.`."
    assert build_resume_hint(
        status="active",
        current_step="",
        next_step="",
        blockers=(),
        next_action=NEXT_ACTION_CONTINUE_VERIFICATION,
    ) == "Resume by running or fixing the required verification."


def test_work_progress_action_policy_builds_review_resume_hints():
    assert build_resume_hint(
        status="active",
        current_step="",
        next_step="",
        blockers=(),
        next_action=NEXT_ACTION_COLLECT_REVIEW_EVIDENCE,
        workflow="implement_then_review",
        step_label="Code review",
        prompt_type="code-reviewer",
    ) == "Resume by running or rerunning the delegated code-reviewer step (Code review) for implement_then_review."
    assert build_resume_hint(
        status="active",
        current_step="",
        next_step="",
        blockers=(),
        next_action=NEXT_ACTION_ADDRESS_REVIEW_FINDINGS,
        workflow="implement_then_review",
    ) == "Resume by addressing the review findings for implement_then_review before rerunning review if needed."


def test_work_progress_action_policy_builds_default_resume_hints():
    assert build_resume_hint(
        status="done",
        current_step="",
        next_step="",
        blockers=(),
        next_action=NEXT_ACTION_CONTINUE_WORK,
    ) == "Task is complete; only continue if the user asks for follow-up work."
    assert build_resume_hint(
        status="active",
        current_step="2. change",
        next_step="",
        blockers=(),
        next_action=NEXT_ACTION_CONTINUE_WORK,
    ) == "Resume at current step: 2. change"
