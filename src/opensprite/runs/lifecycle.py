"""Shared run lifecycle event and status markers."""

from __future__ import annotations

RUN_RUNNING_STATUS = "running"
RUN_COMPLETED_STATUS = "completed"
RUN_CANCELLED_STATUS = "cancelled"

RUN_STARTED_EVENT = "run_started"
RUN_FINISHED_EVENT = "run_finished"
RUN_FAILED_EVENT = "run_failed"
RUN_CANCELLED_EVENT = "run_cancelled"
RUN_CANCEL_REQUESTED_EVENT = "run_cancel_requested"

ACTIVE_RUN_EVENTS = frozenset(
    {
        RUN_STARTED_EVENT,
        RUN_FINISHED_EVENT,
        RUN_FAILED_EVENT,
        RUN_CANCELLED_EVENT,
    }
)
TERMINAL_RUN_EVENTS = frozenset(
    {
        RUN_FINISHED_EVENT,
        RUN_FAILED_EVENT,
        RUN_CANCELLED_EVENT,
    }
)
