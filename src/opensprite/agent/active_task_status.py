"""Shared parsing for rendered ACTIVE_TASK status blocks."""

from __future__ import annotations

import re


_ACTIVE_STATUS_RE = re.compile(r"^- Status:\s*(?P<status>.+)$", re.MULTILINE)
INACTIVE_ACTIVE_TASK_STATUS = "inactive"
ACTIVE_ACTIVE_TASK_STATUS = "active"
BLOCKED_ACTIVE_TASK_STATUS = "blocked"
WAITING_USER_ACTIVE_TASK_STATUS = "waiting_user"
DONE_ACTIVE_TASK_STATUS = "done"
CANCELLED_ACTIVE_TASK_STATUS = "cancelled"
WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION = "need user input"
BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION = "blocked"
CURRENT_ACTIVE_TASK_STATUSES = frozenset(
    {ACTIVE_ACTIVE_TASK_STATUS, BLOCKED_ACTIVE_TASK_STATUS, WAITING_USER_ACTIVE_TASK_STATUS}
)
CURRENT_OR_DONE_ACTIVE_TASK_STATUSES = CURRENT_ACTIVE_TASK_STATUSES | frozenset({DONE_ACTIVE_TASK_STATUS})
TERMINAL_ACTIVE_TASK_STATUSES = frozenset({DONE_ACTIVE_TASK_STATUS, CANCELLED_ACTIVE_TASK_STATUS})
OPEN_QUESTION_CLEAR_ACTIVE_TASK_STATUSES = frozenset(
    {ACTIVE_ACTIVE_TASK_STATUS, DONE_ACTIVE_TASK_STATUS, CANCELLED_ACTIVE_TASK_STATUS}
)


def active_task_status(active_task_snapshot: str | None) -> str:
    """Return the normalized status from a rendered ACTIVE_TASK block."""
    match = _ACTIVE_STATUS_RE.search(str(active_task_snapshot or ""))
    if not match:
        return INACTIVE_ACTIVE_TASK_STATUS
    return match.group("status").strip().lower() or INACTIVE_ACTIVE_TASK_STATUS


def has_current_active_task(active_task_snapshot: str | None) -> bool:
    """Return whether the rendered ACTIVE_TASK block represents current work."""
    return is_current_active_task_status(active_task_status(active_task_snapshot))


def is_current_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status represents current work."""
    return str(status or "").strip().lower() in CURRENT_ACTIVE_TASK_STATUSES


def is_current_or_done_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status can be mirrored from work progress."""
    return str(status or "").strip().lower() in CURRENT_OR_DONE_ACTIVE_TASK_STATUSES


def is_inactive_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status is inactive."""
    return str(status or "").strip().lower() == INACTIVE_ACTIVE_TASK_STATUS


def is_terminal_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status is terminal and resumable."""
    return str(status or "").strip().lower() in TERMINAL_ACTIVE_TASK_STATUSES


def clears_active_task_open_questions(status: str | None) -> bool:
    """Return whether open questions should be cleared for this status."""
    return str(status or "").strip().lower() in OPEN_QUESTION_CLEAR_ACTIVE_TASK_STATUSES
