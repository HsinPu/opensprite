"""Shared helpers for completion-gate status values."""

from __future__ import annotations


CONTINUABLE_COMPLETION_STATUSES = frozenset({"incomplete", "needs_verification", "needs_review"})
TERMINAL_COMPLETION_STATUSES = frozenset({"blocked", "complete", "waiting_user"})
BLOCKING_COMPLETION_STATUSES = frozenset({"blocked", "waiting_user"})
EVIDENCE_FOLLOW_UP_COMPLETION_STATUSES = frozenset({"needs_verification", "needs_review"})


def normalize_completion_status(status: str | None) -> str:
    return str(status or "").strip().lower()


def is_continuable_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) in CONTINUABLE_COMPLETION_STATUSES


def is_terminal_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) in TERMINAL_COMPLETION_STATUSES


def is_blocking_completion_status(status: str | None) -> bool:
    return normalize_completion_status(status) in BLOCKING_COMPLETION_STATUSES


def requires_evidence_follow_up(status: str | None) -> bool:
    return normalize_completion_status(status) in EVIDENCE_FOLLOW_UP_COMPLETION_STATUSES
