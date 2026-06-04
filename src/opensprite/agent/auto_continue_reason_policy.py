"""Shared reason labels for auto-continuation decisions."""

from __future__ import annotations


COMPLETION_GATE_TERMINAL_STATUS_REASON = "completion_gate_terminal_status"
COMPLETION_GATE_STATUS_NOT_CONTINUABLE_REASON = "completion_gate_status_not_continuable"
MAX_DETERMINISTIC_ACTIONS_REACHED_REASON = "max_deterministic_actions_reached"
NO_PROGRESS_DURING_CONTINUATION_REASON = "no_progress_during_continuation"
MAX_AUTO_CONTINUES_REACHED_REASON = "max_auto_continues_reached"
TOOL_ERROR_REQUIRES_BLOCKER_OR_USER_HANDOFF_REASON = "tool_error_requires_blocker_or_user_handoff"
NO_TOOL_PROGRESS_AFTER_INCOMPLETE_RESPONSE_REASON = "no_tool_progress_after_incomplete_response"
REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON = "review_findings_require_follow_up"
REVIEW_EVIDENCE_STILL_MISSING_REASON = "review_evidence_still_missing"
COMPLETION_GATE_CONTINUE_REASON_PREFIX = "completion_gate"


def review_follow_up_skip_reason(*, review_attempted: bool) -> str:
    """Return the stable skip reason for a review completion gate."""
    return REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON if review_attempted else REVIEW_EVIDENCE_STILL_MISSING_REASON


def completion_gate_continue_reason(status: str) -> str:
    """Return the stable continuation reason for a completion gate status."""
    normalized = str(status or "").strip() or "unknown"
    return f"{COMPLETION_GATE_CONTINUE_REASON_PREFIX}_{normalized}"
