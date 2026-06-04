"""Shared run trace event type markers."""

from __future__ import annotations

RUN_PART_DELTA_EVENT = "run_part_delta"
MESSAGE_PART_DELTA_EVENT = "message_part_delta"
TOOL_STARTED_EVENT = "tool_started"
TOOL_RESULT_EVENT = "tool_result"
PERMISSION_REQUESTED_EVENT = "permission_requested"
PERMISSION_GRANTED_EVENT = "permission_granted"
PERMISSION_DENIED_EVENT = "permission_denied"
VERIFICATION_STARTED_EVENT = "verification_started"
VERIFICATION_RESULT_EVENT = "verification_result"
COMPLETION_GATE_EVALUATED_EVENT = "completion_gate.evaluated"
WORK_PROGRESS_UPDATED_EVENT = "work_progress.updated"
TASK_ARTIFACTS_RECORDED_EVENT = "task_artifacts.recorded"

TEXT_DELTA_EVENTS = frozenset({RUN_PART_DELTA_EVENT, MESSAGE_PART_DELTA_EVENT})
TOOL_LIFECYCLE_EVENTS = frozenset({TOOL_STARTED_EVENT, TOOL_RESULT_EVENT})
PERMISSION_EVENTS = frozenset(
    {
        PERMISSION_REQUESTED_EVENT,
        PERMISSION_GRANTED_EVENT,
        PERMISSION_DENIED_EVENT,
    }
)
VERIFICATION_EVENTS = frozenset({VERIFICATION_STARTED_EVENT, VERIFICATION_RESULT_EVENT})
