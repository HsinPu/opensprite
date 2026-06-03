from opensprite.agent.completion_status import (
    allows_nonfinal_response_replacement,
    is_blocking_completion_status,
    is_continuable_completion_status,
    is_terminal_completion_status,
    normalize_completion_status,
    requires_evidence_follow_up,
)


def test_completion_status_helpers_normalize_values():
    assert normalize_completion_status(" COMPLETE ") == "complete"
    assert is_terminal_completion_status("WAITING_USER") is True
    assert is_blocking_completion_status("blocked") is True
    assert is_blocking_completion_status("complete") is False
    assert is_continuable_completion_status("needs_review") is True
    assert is_continuable_completion_status("done") is False
    assert requires_evidence_follow_up("needs_verification") is True
    assert requires_evidence_follow_up("incomplete") is False
    assert allows_nonfinal_response_replacement("incomplete") is True
    assert allows_nonfinal_response_replacement("needs_review") is False
