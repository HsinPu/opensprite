"""Per-run delegated task and workflow update buffers."""

from __future__ import annotations

import time
from typing import Any

from ..storage import StoredDelegatedTask
from .workflow import is_workflow_failed_status


class RunUpdateBuffer:
    """Collect transient delegated-task and workflow updates until a run consumes them."""

    def __init__(self) -> None:
        self._delegated_task_updates: dict[str, dict[str, StoredDelegatedTask]] = {}
        self._workflow_outcomes: dict[str, dict[str, dict[str, Any]]] = {}

    def record_delegated_task_update(self, run_id: str | None, task: StoredDelegatedTask) -> None:
        """Track delegated child-task updates for the active parent run."""
        if run_id is None:
            return
        task_id = str(task.task_id or "").strip()
        if not task_id:
            return
        bucket = self._delegated_task_updates.setdefault(run_id, {})
        previous = bucket.pop(task_id, None)
        now = time.time()
        bucket[task_id] = StoredDelegatedTask(
            task_id=task_id,
            prompt_type=task.prompt_type or (previous.prompt_type if previous is not None else None),
            status=str(task.status or (previous.status if previous is not None else "unknown")).strip() or "unknown",
            selected=bool(task.selected),
            summary=str(task.summary or (previous.summary if previous is not None else "")).strip(),
            error=(
                str(task.error or "").strip()
                if str(task.error or "").strip()
                else ""
                if str(task.status or "").strip() and not is_workflow_failed_status(task.status)
                else previous.error if previous is not None else ""
            ),
            child_session_id=task.child_session_id or (previous.child_session_id if previous is not None else None),
            last_child_run_id=task.last_child_run_id or (previous.last_child_run_id if previous is not None else None),
            metadata={**(previous.metadata if previous is not None else {}), **dict(task.metadata or {})},
            created_at=(
                previous.created_at
                if previous is not None and previous.created_at
                else float(task.created_at or now)
            ),
            updated_at=float(task.updated_at or now),
        )

    def consume_delegated_task_updates(self, run_id: str) -> tuple[StoredDelegatedTask, ...]:
        """Return and clear delegated child-task updates captured for one run."""
        bucket = self._delegated_task_updates.pop(run_id, None)
        if not bucket:
            return ()
        return tuple(bucket.values())

    def clear_delegated_task_updates(self, run_id: str) -> None:
        """Drop delegated child-task updates for one run without returning them."""
        self._delegated_task_updates.pop(run_id, None)

    def record_workflow_outcome(self, run_id: str | None, outcome: dict[str, Any]) -> None:
        """Track one completed or failed workflow outcome for the active run."""
        if run_id is None or not isinstance(outcome, dict):
            return
        workflow_run_id = str(outcome.get("workflow_run_id") or "").strip()
        if not workflow_run_id:
            return
        bucket = self._workflow_outcomes.setdefault(run_id, {})
        bucket.pop(workflow_run_id, None)
        bucket[workflow_run_id] = dict(outcome)

    def consume_workflow_outcomes(self, run_id: str) -> tuple[dict[str, Any], ...]:
        """Return and clear workflow outcomes captured for one run."""
        bucket = self._workflow_outcomes.pop(run_id, None)
        if not bucket:
            return ()
        return tuple(bucket.values())

    def clear_workflow_outcomes(self, run_id: str) -> None:
        """Drop workflow outcomes for one run without returning them."""
        self._workflow_outcomes.pop(run_id, None)
