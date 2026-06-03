"""Shared workflow and delegated-outcome status helpers."""

from __future__ import annotations


WORKFLOW_COMPLETED_STATUS = "completed"
WORKFLOW_FAILED_STATUS = "failed"
WORKFLOW_ERROR_STATUS = "error"
WORKFLOW_CANCELLED_STATUS = "cancelled"
WORKFLOW_RUNNING_STATUS = "running"
WORKFLOW_FAILURE_STATUSES = frozenset({WORKFLOW_FAILED_STATUS, WORKFLOW_ERROR_STATUS})
WORKFLOW_UNSUCCESSFUL_STATUSES = WORKFLOW_FAILURE_STATUSES | frozenset({WORKFLOW_CANCELLED_STATUS})


def is_workflow_completed_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status is completed."""
    return str(status or "").strip().lower() == WORKFLOW_COMPLETED_STATUS


def is_workflow_failed_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status represents failure."""
    return str(status or "").strip().lower() in WORKFLOW_FAILURE_STATUSES


def is_workflow_cancelled_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status represents cancellation."""
    return str(status or "").strip().lower() == WORKFLOW_CANCELLED_STATUS


def is_workflow_unsuccessful_status(status: str | None) -> bool:
    """Return whether a workflow/subtask status is failed, errored, or cancelled."""
    return str(status or "").strip().lower() in WORKFLOW_UNSUCCESSFUL_STATUSES
