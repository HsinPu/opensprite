from opensprite.agent.completion_gate import (
    COMPLETION_GATE_STATUS_NOT_CONTINUABLE_REASON,
    COMPLETION_GATE_TERMINAL_STATUS_REASON,
    MAX_AUTO_CONTINUES_REACHED_REASON,
    MAX_DETERMINISTIC_ACTIONS_REACHED_REASON,
    NO_PROGRESS_DURING_CONTINUATION_REASON,
    NO_TOOL_PROGRESS_AFTER_INCOMPLETE_RESPONSE_REASON,
    REVIEW_EVIDENCE_STILL_MISSING_REASON,
    REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON,
    TOOL_ERROR_REQUIRES_BLOCKER_OR_USER_HANDOFF_REASON,
    completion_gate_continue_reason,
    review_follow_up_skip_reason,
)


def test_auto_continue_skip_reasons_are_stable():
    assert COMPLETION_GATE_TERMINAL_STATUS_REASON == "completion_gate_terminal_status"
    assert COMPLETION_GATE_STATUS_NOT_CONTINUABLE_REASON == "completion_gate_status_not_continuable"
    assert MAX_DETERMINISTIC_ACTIONS_REACHED_REASON == "max_deterministic_actions_reached"
    assert NO_PROGRESS_DURING_CONTINUATION_REASON == "no_progress_during_continuation"
    assert MAX_AUTO_CONTINUES_REACHED_REASON == "max_auto_continues_reached"
    assert TOOL_ERROR_REQUIRES_BLOCKER_OR_USER_HANDOFF_REASON == "tool_error_requires_blocker_or_user_handoff"
    assert NO_TOOL_PROGRESS_AFTER_INCOMPLETE_RESPONSE_REASON == "no_tool_progress_after_incomplete_response"


def test_review_follow_up_skip_reason_uses_review_state():
    assert review_follow_up_skip_reason(review_attempted=True) == REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON
    assert review_follow_up_skip_reason(review_attempted=False) == REVIEW_EVIDENCE_STILL_MISSING_REASON


def test_completion_gate_continue_reason_normalizes_empty_status():
    assert completion_gate_continue_reason("incomplete") == "completion_gate_incomplete"
    assert completion_gate_continue_reason("") == "completion_gate_unknown"
