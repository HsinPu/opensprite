"""Shared next-action policy for structured work progress."""

from __future__ import annotations


NEXT_ACTION_FINALIZE = "finalize"
NEXT_ACTION_STOP_BUDGET_EXHAUSTED = "stop_budget_exhausted"
NEXT_ACTION_STOP_NO_PROGRESS = "stop_no_progress"
NEXT_ACTION_CONTINUE_VERIFICATION = "continue_verification"
NEXT_ACTION_COLLECT_REVIEW_EVIDENCE = "collect_review_evidence"
NEXT_ACTION_ADDRESS_REVIEW_FINDINGS = "address_review_findings"
NEXT_ACTION_CONTINUE_REVIEW = "continue_review"
NEXT_ACTION_CONTINUE_WORK = "continue_work"
DEFAULT_WORK_STEP_NOT_SET = "not set"
TASK_DONE_RESUME_HINT = "Task is complete; only continue if the user asks for follow-up work."

REVIEW_FOLLOW_UP_NEXT_ACTIONS = frozenset(
    {
        NEXT_ACTION_COLLECT_REVIEW_EVIDENCE,
        NEXT_ACTION_ADDRESS_REVIEW_FINDINGS,
    }
)
REVIEW_PHASE_NEXT_ACTIONS = frozenset(
    {
        NEXT_ACTION_CONTINUE_REVIEW,
        *REVIEW_FOLLOW_UP_NEXT_ACTIONS,
    }
)


def normalize_next_action(value: str | None) -> str:
    return str(value or "").strip()


def is_verification_next_action(value: str | None) -> bool:
    return normalize_next_action(value) == NEXT_ACTION_CONTINUE_VERIFICATION


def is_continue_work_next_action(value: str | None) -> bool:
    return normalize_next_action(value) == NEXT_ACTION_CONTINUE_WORK


def is_review_follow_up_next_action(value: str | None) -> bool:
    return normalize_next_action(value) in REVIEW_FOLLOW_UP_NEXT_ACTIONS


def is_review_phase_next_action(value: str | None) -> bool:
    return normalize_next_action(value) in REVIEW_PHASE_NEXT_ACTIONS


def build_resume_hint(
    *,
    status: str,
    current_step: str,
    next_step: str,
    blockers: tuple[str, ...],
    next_action: str,
    workflow: str = "",
    step_label: str = "",
    prompt_type: str = "",
    verification_action: str = "",
    verification_path: str = "",
    done_status: str = "done",
    unset_step: str = DEFAULT_WORK_STEP_NOT_SET,
) -> str:
    if status == done_status:
        return TASK_DONE_RESUME_HINT
    if blockers:
        return f"Resolve blocker first: {blockers[0]}"
    if is_verification_next_action(next_action):
        if workflow and step_label:
            return f"Resume by finishing verification around the {step_label} step in {workflow}."
        if verification_action and verification_path:
            return f"Resume by running verify {verification_action} for `{verification_path}`."
        return "Resume by running or fixing the required verification."
    if normalize_next_action(next_action) == NEXT_ACTION_COLLECT_REVIEW_EVIDENCE:
        if workflow and step_label and prompt_type:
            return f"Resume by running or rerunning the delegated {prompt_type} step ({step_label}) for {workflow}."
        if prompt_type:
            return f"Resume by running or rerunning the delegated {prompt_type} step for the changed code."
        return "Resume by running or rerunning a delegated review step for the changed code."
    if normalize_next_action(next_action) == NEXT_ACTION_ADDRESS_REVIEW_FINDINGS:
        if workflow:
            return f"Resume by addressing the review findings for {workflow} before rerunning review if needed."
        return "Resume by addressing the delegated review findings before treating the task as complete."
    if normalize_next_action(next_action) == NEXT_ACTION_CONTINUE_REVIEW:
        return "Resume by collecting review evidence or addressing delegated review findings."
    if workflow and step_label:
        return f"Resume with the {step_label} step in {workflow}."
    if current_step and current_step != unset_step:
        return f"Resume at current step: {current_step}"
    if next_step and next_step != unset_step:
        return f"Resume with next step: {next_step}"
    return "Continue the active task from the latest recorded state."
