from opensprite.agent.workflow_completion_policy import (
    WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON,
    is_research_then_outline_workflow,
    is_review_workflow,
    workflow_clean_review_reason,
    workflow_completed_all_steps_reason,
    workflow_fix_follow_up_fields,
    workflow_review_evidence_missing_reason,
    workflow_review_evidence_missing_detail,
    workflow_review_findings_follow_up_reason,
    workflow_review_follow_up_fields,
    workflow_unsuccessful_reason,
    task_review_evidence_missing_detail,
    task_review_findings_follow_up_detail,
)


def test_workflow_completion_policy_classifies_workflow_families():
    assert is_review_workflow("implement_then_review") is True
    assert is_review_workflow("bugfix_then_test_then_review") is True
    assert is_review_workflow("research_then_outline") is False
    assert is_research_then_outline_workflow("research_then_outline") is True
    assert is_research_then_outline_workflow("implement_then_review") is False


def test_workflow_completion_reasons_are_stable():
    assert (
        WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON
        == "workflow completed but required verification evidence is still missing"
    )
    assert workflow_unsuccessful_reason("implement_then_review") == (
        "workflow implement_then_review did not complete successfully"
    )
    assert workflow_review_evidence_missing_reason("implement_then_review") == (
        "workflow implement_then_review completed but review evidence is missing"
    )
    assert workflow_review_findings_follow_up_reason("implement_then_review") == (
        "workflow implement_then_review completed but review findings still require follow-up"
    )
    assert workflow_clean_review_reason("implement_then_review") == (
        "workflow implement_then_review completed with clean review evidence"
    )
    assert workflow_completed_all_steps_reason("research_then_outline") == (
        "workflow research_then_outline completed all required steps"
    )


def test_workflow_review_follow_up_details_are_stable():
    assert workflow_review_evidence_missing_detail() == (
        "Run or rerun a delegated review step for the changed code before treating the workflow as complete."
    )
    assert task_review_evidence_missing_detail() == (
        "Run or rerun a delegated review step for the changed code before treating the task as complete."
    )
    assert task_review_findings_follow_up_detail() == (
        "Address the delegated review findings before treating the task as complete."
    )


def test_workflow_completion_policy_returns_review_follow_up_step():
    assert workflow_review_follow_up_fields("implement_then_review") == {
        "next_step_id": "review",
        "next_step_label": "Code review",
        "next_step_prompt_type": "code-reviewer",
    }
    assert workflow_review_follow_up_fields("research_then_outline") == {}


def test_workflow_completion_policy_returns_fix_follow_up_steps():
    assert workflow_fix_follow_up_fields("implement_then_review") == {
        "next_step_id": "implement",
        "next_step_label": "Implement",
        "next_step_prompt_type": "implementer",
    }
    assert workflow_fix_follow_up_fields("bugfix_then_test_then_review") == {
        "next_step_id": "bugfix",
        "next_step_label": "Bug fix",
        "next_step_prompt_type": "bug-fixer",
    }
    assert workflow_fix_follow_up_fields("research_then_outline") == {}
