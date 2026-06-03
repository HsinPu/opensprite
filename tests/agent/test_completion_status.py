from opensprite.agent.completion_status import (
    allows_nonfinal_response_replacement,
    allows_workflow_resume,
    is_blocking_completion_status,
    is_complete_completion_status,
    is_continuable_completion_status,
    is_incomplete_completion_status,
    is_terminal_completion_status,
    normalize_completion_status,
    needs_review_completion_status,
    needs_verification_completion_status,
    requires_evidence_follow_up,
)


def test_completion_status_helpers_normalize_values():
    assert normalize_completion_status(" COMPLETE ") == "complete"
    assert is_terminal_completion_status("WAITING_USER") is True
    assert is_complete_completion_status(" COMPLETE ") is True
    assert is_complete_completion_status("incomplete") is False
    assert is_incomplete_completion_status(" incomplete ") is True
    assert is_incomplete_completion_status("complete") is False
    assert needs_verification_completion_status(" NEEDS_VERIFICATION ") is True
    assert needs_verification_completion_status("needs_review") is False
    assert needs_review_completion_status(" NEEDS_REVIEW ") is True
    assert needs_review_completion_status("needs_verification") is False
    assert is_blocking_completion_status("blocked") is True
    assert is_blocking_completion_status("complete") is False
    assert is_continuable_completion_status("needs_review") is True
    assert is_continuable_completion_status("done") is False
    assert requires_evidence_follow_up("needs_verification") is True
    assert requires_evidence_follow_up("incomplete") is False
    assert allows_nonfinal_response_replacement("incomplete") is True
    assert allows_nonfinal_response_replacement("needs_review") is False
    assert allows_workflow_resume("needs_review") is True
    assert allows_workflow_resume("needs_verification") is False
