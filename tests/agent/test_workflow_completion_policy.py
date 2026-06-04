from opensprite.agent.workflow_completion_policy import (
    WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON,
    is_research_then_outline_workflow,
    is_review_workflow,
    workflow_fix_follow_up_fields,
    workflow_review_follow_up_fields,
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
