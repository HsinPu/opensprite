"""Active task state helpers for AgentLoop."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..config import TaskMessagesConfig
from ..documents.active_task import (
    _extract_task_field,
    build_task_block_from_intent_fields,
    build_task_block_from_text,
    create_active_task_store,
)
from ..storage import StorageProvider
from ..storage.base import StoredWorkState
from ..storage.base import get_storage_message_count
from ..utils.log import logger
from .active_task_status import is_current_active_task_status, is_current_or_done_active_task_status
from .completion_gate import CompletionGateResult
from .completion_status import is_blocking_completion_status
from .task_context_resolver import TaskContextDecision
from .task_intent import TaskIntent
from .task_objective_resolver import TaskObjectiveDecision
from .work_progress import WorkProgressService, WorkProgressUpdate


_CURRENT_TASK_CONTINUATION_TYPES = frozenset(
    {
        "follow_up",
        "continue_active_task",
        "continue_last_answer",
        "continue_tool_work",
        "advance_current_step",
    }
)
_CURRENT_TASK_REPLACEMENT_TYPES = frozenset({"task_switch", "new_task"})
_AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE = "ambiguous_boundary"


class ActiveTaskCommandService:
    """Handles direct commands and immediate updates for ACTIVE_TASK state."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        app_home_getter: Callable[[], Path | None],
        workspace_root_getter: Callable[[], Path | None],
        messages: TaskMessagesConfig | None = None,
    ):
        self.storage = storage
        self._app_home_getter = app_home_getter
        self._workspace_root_getter = workspace_root_getter
        self.messages = messages or TaskMessagesConfig()

    def get_store(self, session_id: str):
        app_home = self._app_home_getter()
        if app_home is None:
            return None
        return create_active_task_store(
            app_home,
            session_id,
            workspace_root=self._workspace_root_getter(),
        )

    def clear(self, session_id: str) -> None:
        """Reset ACTIVE_TASK.md for one session."""
        store = self.get_store(session_id)
        if store is not None:
            store.clear(session_id)

    async def _mark_processed(self, session_id: str, store: Any) -> None:
        message_count = await get_storage_message_count(self.storage, session_id)
        store.set_processed_index(session_id, message_count)

    async def apply_completion_gate_result(
        self,
        session_id: str,
        result: CompletionGateResult,
    ) -> None:
        """Apply conservative task-state updates from completion-gate verdicts."""
        if not result.should_update_active_task or result.active_task_status is None:
            return
        store = self.get_store(session_id)
        if store is None:
            return
        if not is_current_active_task_status(store.read_status()):
            return

        status = result.active_task_status
        detail = result.active_task_detail or result.reason
        if status == "waiting_user":
            store.update_fields(status="waiting_user", open_questions=[detail or "need user input"], force=True)
        elif status == "blocked":
            store.update_fields(status="blocked", open_questions=[detail or "blocked"], force=True)
        elif status == "done":
            store.update_fields(status="done", open_questions=["none"], force=True)
        else:
            return

        await self._mark_processed(session_id, store)
        store.append_event(
            "completion_gate",
            "immediate",
            details={"status": result.status, "reason": result.reason},
        )

    async def apply_work_progress(
        self,
        session_id: str,
        progress: WorkProgressUpdate,
        state: StoredWorkState | None = None,
    ) -> None:
        """Keep ACTIVE_TASK aligned with the final structured work progress state."""
        store = self.get_store(session_id)
        if store is None:
            return
        if not is_current_active_task_status(store.read_status()):
            return

        current_step = state.current_step if state is not None else None
        next_step = state.next_step if state is not None else None
        if not current_step and not next_step:
            if progress.next_action == "continue_verification" or progress.status == "verifying":
                current_step = self.messages.progress_verify_current_step
                next_step = "not set"
            elif progress.next_action == "continue_work":
                current_step = self.messages.progress_continue_current_step
                next_step = self.messages.progress_verify_current_step if progress.verification_required else "not set"
            else:
                return
        if current_step is None or next_step is None:
            return

        workboard = WorkProgressService.extract_workboard(state)
        open_questions: list[str] | None = None
        if workboard.blockers:
            open_questions = list(workboard.blockers)
        elif state is not None and state.status in {"active", "done"}:
            open_questions = ["none"]
        elif is_blocking_completion_status(progress.status):
            open_questions = [progress.completion_reason]

        store.update_fields(
            status=state.status if state is not None and is_current_or_done_active_task_status(state.status) else "active",
            current_step=current_step,
            next_step=next_step,
            open_questions=open_questions,
            force=True,
        )
        store.append_event(
            "work_progress",
            "immediate",
            details={
                "status": progress.status,
                "next_action": progress.next_action,
                "file_change_count": progress.file_change_count,
                "verification_required": progress.verification_required,
                "verification_passed": progress.verification_passed,
            },
        )

    async def maybe_seed(
        self,
        session_id: str,
        current_message: str,
        *,
        enabled: bool,
        task_intent: TaskIntent | None = None,
        task_context_decision: TaskContextDecision | None = None,
        task_objective_decision: TaskObjectiveDecision | None = None,
    ) -> None:
        """Create a minimal ACTIVE_TASK.md before the first heavy turn when appropriate."""
        if not enabled:
            return
        store = self.get_store(session_id)
        if store is None:
            return

        current_status = store.read_status()
        replacing = False
        has_current_task = is_current_active_task_status(current_status)
        if has_current_task:
            current_task = store.read_managed_block()
            if _decision_needs_boundary_confirmation(task_context_decision):
                question = _boundary_confirmation_question(current_task, current_message)
                store.update_fields(status="waiting_user", open_questions=[question], force=True)
                await self._mark_processed(session_id, store)
                store.append_event(
                    "task_boundary_confirmation",
                    "immediate",
                    details={
                        "message": re.sub(r"\s+", " ", current_message).strip()[:120],
                        "pending_request": _compact_for_prompt(current_message),
                        "confidence": task_context_decision.confidence if task_context_decision else 0.0,
                    },
                )
                return
            if _decision_continues_current_task(task_context_decision):
                if current_status == "waiting_user" and store.read_pending_boundary_request():
                    store.update_fields(status="active", open_questions=["none"], force=True)
                    await self._mark_processed(session_id, store)
                    store.append_event(
                        "task_boundary_confirmation_resolved",
                        "immediate",
                        details={"action": "continue", "message": _compact_for_prompt(current_message)},
                    )
                return
            llm_replace = _decision_replaces_current_task(task_context_decision)
            if task_context_decision and task_context_decision.should_inherit_active_task and not llm_replace:
                return
            if not llm_replace:
                return
            replacing = True

        initial_task = None
        if task_intent is not None:
            if _decision_controls_task_seed(task_context_decision):
                should_seed = (
                    bool(task_context_decision and task_context_decision.should_seed_active_task)
                    or bool(task_context_decision and task_context_decision.should_replace_active_task)
                    or bool(task_objective_decision and task_objective_decision.should_use_resolved_objective)
                )
            else:
                should_seed = bool(task_objective_decision and task_objective_decision.should_use_resolved_objective)
            inheriting_current_task = bool(has_current_task and task_context_decision and task_context_decision.should_inherit_active_task)
            if should_seed and not inheriting_current_task:
                goal = task_intent.objective
                assumptions: list[str] | None = None
                if task_objective_decision and task_objective_decision.should_use_resolved_objective:
                    goal = task_objective_decision.resolved_objective
                    assumptions = _objective_assumptions(task_objective_decision)
                initial_task = build_task_block_from_intent_fields(
                    goal=goal,
                    definition_of_done=task_intent.done_criteria,
                    constraints=task_intent.constraints,
                    assumptions=assumptions,
                )
        else:
            return
        if not initial_task:
            return

        store.write_managed_block(initial_task)
        message_count = await get_storage_message_count(self.storage, session_id)
        store.set_processed_index(session_id, max(0, message_count - 1))
        compact_message = re.sub(r"\s+", " ", current_message).strip()
        if len(compact_message) > 120:
            compact_message = compact_message[:117].rstrip() + "..."
        event_details = {"replace": replacing, "message": compact_message}
        if task_intent is not None:
            event_details.update(
                {
                    "intent_kind": task_intent.kind,
                    "intent_long_running": task_intent.long_running,
                }
            )
        if task_objective_decision and task_objective_decision.should_use_resolved_objective:
            event_details.update(
                {
                    "original_message": task_objective_decision.original_message,
                    "resolved_objective": task_objective_decision.resolved_objective,
                    "objective_method": task_objective_decision.method,
                    "objective_confidence": task_objective_decision.confidence,
                }
            )
        store.append_event("seed", "immediate", details=event_details)
        logger.info("[{}] active_task.seeded | replace={}", session_id, replacing)

    async def show(self, session_id: str) -> str | None:
        """Return the current ACTIVE_TASK block for user display, if any."""
        store = self.get_store(session_id)
        if store is None:
            return None
        return store.render_for_user()

    async def show_full(self, session_id: str) -> str | None:
        """Return the full ACTIVE_TASK block for user display, if any."""
        store = self.get_store(session_id)
        if store is None:
            return None
        return store.render_full_for_user()

    async def show_history(self, session_id: str, *, limit: int = 10) -> str | None:
        """Return recent ACTIVE_TASK events for user display, if any."""
        store = self.get_store(session_id)
        if store is None:
            return None
        return store.render_history(limit=limit)

    async def set_from_text(self, session_id: str, task_text: str) -> str | None:
        """Create or replace the current ACTIVE_TASK from explicit user text."""
        store = self.get_store(session_id)
        if store is None:
            return None
        task_block = build_task_block_from_text(task_text, force=True)
        if not task_block:
            return None
        store.write_managed_block(task_block)
        await self._mark_processed(session_id, store)
        store.append_event("set", "user", details={"task": task_text})
        return store.render_full_for_user()

    async def activate(self, session_id: str) -> str | None:
        """Mark the current ACTIVE_TASK as active again."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="active", open_questions=["none"], force=True)
        await self._mark_processed(session_id, store)
        store.append_event("activate", "user")
        return f"# Active Task\n\n{rendered}"

    async def reopen(self, session_id: str) -> str | None:
        """Reopen a terminal ACTIVE_TASK and resume it as active."""
        store = self.get_store(session_id)
        if store is None:
            return None
        if store.read_status() not in {"done", "cancelled"}:
            return None
        rendered = store.update_fields(status="active", force=True)
        await self._mark_processed(session_id, store)
        store.append_event("reopen", "user")
        return f"# Active Task\n\n{rendered}"

    async def block(self, session_id: str, reason: str) -> str | None:
        """Mark the current ACTIVE_TASK as blocked with one explicit reason."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="blocked", open_questions=[reason], force=True)
        await self._mark_processed(session_id, store)
        store.append_event("block", "user", details={"reason": reason})
        return f"# Active Task\n\n{rendered}"

    async def wait_on(self, session_id: str, question: str) -> str | None:
        """Mark the current ACTIVE_TASK as waiting for user input."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="waiting_user", open_questions=[question], force=True)
        await self._mark_processed(session_id, store)
        store.append_event("wait", "user", details={"question": question})
        return f"# Active Task\n\n{rendered}"

    async def set_current_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the current step for the active task."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="active", current_step=step_text, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("set_current_step", "user", details={"current_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def set_next_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the planned next step for the active task."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(next_step=step_text, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("set_next_step", "user", details={"next_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def advance(self, session_id: str) -> str | None:
        """Promote the next step into the current step and mark the previous step complete."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        next_step = _extract_task_field(current_block, "Next step")
        if next_step == "not set":
            return None
        rendered = store.update_fields(
            status="active",
            current_step=next_step,
            next_step="not set",
            append_completed_step=current_step,
            force=True,
        )
        await self._mark_processed(session_id, store)
        store.append_event(
            "advance",
            "user",
            details={"completed_step": current_step, "new_current_step": next_step},
        )
        return f"# Active Task\n\n{rendered}"

    async def complete_step(self, session_id: str, next_step_override: str | None = None) -> str | None:
        """Complete the current step and either advance or finish the task."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        rendered = store.complete_current_step(next_step_override=next_step_override)
        if rendered is None:
            return None
        await self._mark_processed(session_id, store)
        store.append_event(
            "complete_step",
            "user",
            details={
                "completed_step": current_step,
                "next_step_override": next_step_override or "",
            },
        )
        return f"# Active Task\n\n{rendered}"

    async def mark_status(self, session_id: str, status: str) -> str | None:
        """Set the current ACTIVE_TASK status when one exists."""
        store = self.get_store(session_id)
        if store is None or store.read_status() == "inactive":
            return None
        open_questions = ["none"] if status in {"active", "done", "cancelled"} else None
        store.update_fields(status=status, open_questions=open_questions, force=True)
        if status in {"done", "cancelled"}:
            await self._mark_processed(session_id, store)
        store.append_event(status, "user")
        return store.render_full_for_user()

    async def reset(self, session_id: str) -> None:
        """Clear the current ACTIVE_TASK state for one session."""
        store = self.get_store(session_id)
        if store is None:
            return
        self.clear(session_id)
        store.append_event("reset", "user")


def _decision_continues_current_task(decision: TaskContextDecision | None) -> bool:
    if decision is None or decision.should_replace_active_task:
        return False
    return bool(
        decision.should_inherit_active_task
        or decision.continuation_type in _CURRENT_TASK_CONTINUATION_TYPES
        or decision.is_follow_up
    )


def _decision_replaces_current_task(decision: TaskContextDecision | None) -> bool:
    return bool(
        decision
        and decision.should_replace_active_task
        and decision.continuation_type in _CURRENT_TASK_REPLACEMENT_TYPES
    )


def _decision_needs_boundary_confirmation(decision: TaskContextDecision | None) -> bool:
    return bool(decision and decision.continuation_type == _AMBIGUOUS_BOUNDARY_CONTINUATION_TYPE)


def _decision_controls_task_seed(decision: TaskContextDecision | None) -> bool:
    return bool(decision and decision.method == "llm")


def _boundary_confirmation_question(current_task: str, current_message: str) -> str:
    current_goal = _compact_for_prompt(_extract_task_field(current_task, "Goal"))
    new_request = _compact_for_prompt(current_message) or "the new request"
    if current_goal and current_goal.lower() != "not set":
        return (
            f"Reply `switch` to replace the active task ({current_goal}) "
            f"with the new request ({new_request}), or `continue` to keep the active task."
        )
    return f"Reply `switch` to replace it with the new request ({new_request}), or `continue` to keep the active task."


def _compact_for_prompt(value: str, max_chars: int = 120) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _objective_assumptions(decision: TaskObjectiveDecision) -> list[str]:
    return [
        f"Original user message: {decision.original_message}",
        f"Objective inferred from conversation context ({decision.method}, confidence={decision.confidence:.2f}).",
    ]
