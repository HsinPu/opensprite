"""Active task state helpers for AgentLoop."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..documents.active_task import (
    _extract_task_field,
    build_initial_active_task_block,
    build_task_block_from_intent_fields,
    build_task_block_from_text,
    create_active_task_store,
    infer_immediate_task_transition,
    should_replace_active_task,
)
from ..storage import StorageProvider
from ..storage.base import get_storage_message_count
from ..utils.log import logger
from .task_intent import TaskIntent


class ActiveTaskCommandService:
    """Handles direct commands and immediate updates for ACTIVE_TASK state."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        app_home_getter: Callable[[], Path | None],
        workspace_root_getter: Callable[[], Path | None],
    ):
        self.storage = storage
        self._app_home_getter = app_home_getter
        self._workspace_root_getter = workspace_root_getter

    def get_store(self, chat_id: str):
        app_home = self._app_home_getter()
        if app_home is None:
            return None
        return create_active_task_store(
            app_home,
            chat_id,
            workspace_root=self._workspace_root_getter(),
        )

    def clear(self, chat_id: str) -> None:
        """Reset ACTIVE_TASK.md for one chat session."""
        store = self.get_store(chat_id)
        if store is not None:
            store.clear(chat_id)

    async def _mark_processed(self, chat_id: str, store: Any) -> None:
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)

    async def apply_immediate_transition(
        self,
        chat_id: str,
        response_text: str,
        *,
        had_tool_error: bool,
    ) -> None:
        """Apply conservative immediate task-state transitions after a response."""
        store = self.get_store(chat_id)
        if store is None:
            return
        if store.read_status() not in {"active", "blocked", "waiting_user"}:
            return

        transition = infer_immediate_task_transition(
            response_text,
            had_tool_error=had_tool_error,
        )
        if transition is None:
            return

        status, detail = transition
        if status == "waiting_user":
            store.update_fields(status="waiting_user", open_questions=[detail or "need user input"], force=True)
        elif status == "blocked":
            store.update_fields(status="blocked", open_questions=[detail or "blocked"], force=True)
        else:
            return

        await self._mark_processed(chat_id, store)
        store.append_event("auto_direct_transition", "immediate", details={"status": status, "reason": detail or ""})

    async def maybe_seed(
        self,
        chat_id: str,
        current_message: str,
        *,
        enabled: bool,
        task_intent: TaskIntent | None = None,
    ) -> None:
        """Create a minimal ACTIVE_TASK.md before the first heavy turn when appropriate."""
        if not enabled:
            return
        store = self.get_store(chat_id)
        if store is None:
            return

        current_status = store.read_status()
        replacing = False
        if current_status in {"active", "blocked", "waiting_user"}:
            if not should_replace_active_task(store.read_managed_block(), current_message):
                return
            replacing = True

        initial_task = None
        if task_intent is not None:
            if task_intent.should_seed_active_task:
                initial_task = build_task_block_from_intent_fields(
                    goal=task_intent.objective,
                    definition_of_done=task_intent.done_criteria,
                    constraints=task_intent.constraints,
                )
        else:
            initial_task = build_initial_active_task_block(current_message)
        if not initial_task:
            return

        store.write_managed_block(initial_task)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, max(0, message_count - 1))
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
        store.append_event("seed", "immediate", details=event_details)
        logger.info("[{}] active_task.seeded | replace={}", chat_id, replacing)

    async def show(self, chat_id: str) -> str | None:
        """Return the current ACTIVE_TASK block for user display, if any."""
        store = self.get_store(chat_id)
        if store is None:
            return None
        return store.render_for_user()

    async def show_full(self, chat_id: str) -> str | None:
        """Return the full ACTIVE_TASK block for user display, if any."""
        store = self.get_store(chat_id)
        if store is None:
            return None
        return store.render_full_for_user()

    async def show_history(self, chat_id: str, *, limit: int = 10) -> str | None:
        """Return recent ACTIVE_TASK events for user display, if any."""
        store = self.get_store(chat_id)
        if store is None:
            return None
        return store.render_history(limit=limit)

    async def set_from_text(self, chat_id: str, task_text: str) -> str | None:
        """Create or replace the current ACTIVE_TASK from explicit user text."""
        store = self.get_store(chat_id)
        if store is None:
            return None
        task_block = build_task_block_from_text(task_text, force=True)
        if not task_block:
            return None
        store.write_managed_block(task_block)
        await self._mark_processed(chat_id, store)
        store.append_event("set", "user", details={"task": task_text})
        return store.render_full_for_user()

    async def activate(self, chat_id: str) -> str | None:
        """Mark the current ACTIVE_TASK as active again."""
        store = self.get_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="active", open_questions=["none"], force=True)
        await self._mark_processed(chat_id, store)
        store.append_event("activate", "user")
        return f"# Active Task\n\n{rendered}"

    async def reopen(self, chat_id: str) -> str | None:
        """Reopen a terminal ACTIVE_TASK and resume it as active."""
        store = self.get_store(chat_id)
        if store is None:
            return None
        if store.read_status() not in {"done", "cancelled"}:
            return None
        rendered = store.update_fields(status="active", force=True)
        await self._mark_processed(chat_id, store)
        store.append_event("reopen", "user")
        return f"# Active Task\n\n{rendered}"

    async def block(self, chat_id: str, reason: str) -> str | None:
        """Mark the current ACTIVE_TASK as blocked with one explicit reason."""
        store = self.get_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="blocked", open_questions=[reason], force=True)
        await self._mark_processed(chat_id, store)
        store.append_event("block", "user", details={"reason": reason})
        return f"# Active Task\n\n{rendered}"

    async def wait_on(self, chat_id: str, question: str) -> str | None:
        """Mark the current ACTIVE_TASK as waiting for user input."""
        store = self.get_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="waiting_user", open_questions=[question], force=True)
        await self._mark_processed(chat_id, store)
        store.append_event("wait", "user", details={"question": question})
        return f"# Active Task\n\n{rendered}"

    async def set_current_step(self, chat_id: str, step_text: str) -> str | None:
        """Replace the current step for the active task."""
        store = self.get_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="active", current_step=step_text, force=True)
        await self._mark_processed(chat_id, store)
        store.append_event("set_current_step", "user", details={"current_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def set_next_step(self, chat_id: str, step_text: str) -> str | None:
        """Replace the planned next step for the active task."""
        store = self.get_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(next_step=step_text, force=True)
        await self._mark_processed(chat_id, store)
        store.append_event("set_next_step", "user", details={"next_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def advance(self, chat_id: str) -> str | None:
        """Promote the next step into the current step and mark the previous step complete."""
        store = self.get_store(chat_id)
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
        await self._mark_processed(chat_id, store)
        store.append_event(
            "advance",
            "user",
            details={"completed_step": current_step, "new_current_step": next_step},
        )
        return f"# Active Task\n\n{rendered}"

    async def complete_step(self, chat_id: str, next_step_override: str | None = None) -> str | None:
        """Complete the current step and either advance or finish the task."""
        store = self.get_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        rendered = store.complete_current_step(next_step_override=next_step_override)
        if rendered is None:
            return None
        await self._mark_processed(chat_id, store)
        store.append_event(
            "complete_step",
            "user",
            details={
                "completed_step": current_step,
                "next_step_override": next_step_override or "",
            },
        )
        return f"# Active Task\n\n{rendered}"

    async def mark_status(self, chat_id: str, status: str) -> str | None:
        """Set the current ACTIVE_TASK status when one exists."""
        store = self.get_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        open_questions = ["none"] if status in {"active", "done", "cancelled"} else None
        store.update_fields(status=status, open_questions=open_questions, force=True)
        if status in {"done", "cancelled"}:
            await self._mark_processed(chat_id, store)
        store.append_event(status, "user")
        return store.render_full_for_user()

    async def reset(self, chat_id: str) -> None:
        """Clear the current ACTIVE_TASK state for one session."""
        store = self.get_store(chat_id)
        if store is None:
            return
        self.clear(chat_id)
        store.append_event("reset", "user")
