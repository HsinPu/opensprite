"""Shared helpers for completion-gate status values."""

from __future__ import annotations


INCOMPLETE_COMPLETION_STATUS = "incomplete"
NEEDS_VERIFICATION_COMPLETION_STATUS = "needs_verification"
NEEDS_REVIEW_COMPLETION_STATUS = "needs_review"
COMPLETE_COMPLETION_STATUS = "complete"
BLOCKED_COMPLETION_STATUS = "blocked"
WAITING_USER_COMPLETION_STATUS = "waiting_user"
CONTINUABLE_COMPLETION_STATUSES = frozenset(
    {INCOMPLETE_COMPLETION_STATUS, NEEDS_VERIFICATION_COMPLETION_STATUS, NEEDS_REVIEW_COMPLETION_STATUS}
)
TERMINAL_COMPLETION_STATUSES = frozenset(
    {BLOCKED_COMPLETION_STATUS, COMPLETE_COMPLETION_STATUS, WAITING_USER_COMPLETION_STATUS}
)
BLOCKING_COMPLETION_STATUSES = frozenset({BLOCKED_COMPLETION_STATUS, WAITING_USER_COMPLETION_STATUS})
EVIDENCE_FOLLOW_UP_COMPLETION_STATUSES = frozenset(
    {NEEDS_VERIFICATION_COMPLETION_STATUS, NEEDS_REVIEW_COMPLETION_STATUS}
)
REPLACEABLE_NONFINAL_COMPLETION_STATUSES = frozenset(
    {INCOMPLETE_COMPLETION_STATUS, NEEDS_VERIFICATION_COMPLETION_STATUS}
)
WORKFLOW_RESUME_COMPLETION_STATUSES = frozenset({INCOMPLETE_COMPLETION_STATUS, NEEDS_REVIEW_COMPLETION_STATUS})


def normalize_completion_status(status: str | None) -> str:
    return str(status or "").strip().lower()


def is_continuable_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) in CONTINUABLE_COMPLETION_STATUSES


def is_terminal_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) in TERMINAL_COMPLETION_STATUSES


def is_complete_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) == COMPLETE_COMPLETION_STATUS


def is_incomplete_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) == INCOMPLETE_COMPLETION_STATUS


def needs_verification_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) == NEEDS_VERIFICATION_COMPLETION_STATUS


def needs_review_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) == NEEDS_REVIEW_COMPLETION_STATUS


def is_blocking_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) in BLOCKING_COMPLETION_STATUSES


def requires_evidence_follow_up(status: str | None) -> bool:
    return normalize_completion_status(status) in EVIDENCE_FOLLOW_UP_COMPLETION_STATUSES


def allows_nonfinal_response_replacement(status: str | None) -> bool:
    return normalize_completion_status(status) in REPLACEABLE_NONFINAL_COMPLETION_STATUSES


def allows_workflow_resume(status: str | None) -> bool:
    return normalize_completion_status(status) in WORKFLOW_RESUME_COMPLETION_STATUSES
