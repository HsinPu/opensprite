"""Tool for explicit ACTIVE_TASK.md updates during agent execution."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..documents.active_task import (
    ActiveTaskStore,
    _extract_task_field,
    build_task_block_from_text,
)
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


ActiveTaskStoreFactory = Callable[[str], ActiveTaskStore | None]
MessageCountGetter = Callable[[str], Awaitable[int]]

_ALLOWED_STATUSES = ("inactive", "active", "blocked", "waiting_user", "done", "cancelled")
_ACTION_VALUES = ("set", "update", "advance", "complete_step", "reset", "show")


class TaskUpdateTool(Tool):
    """Update the current session's ACTIVE_TASK.md managed block."""

    name = "task_update"

    description = (
        "Update the current session's ACTIVE_TASK.md so long-running work stays explicit. "
        "Use this after materially changing task status, completing a step, blocking on missing input, "
        "or setting/replacing the active task. Do not use it for trivial one-turn chat."
    )

    def __init__(
        self,
        *,
        get_session_id: Callable[[], str | None],
        active_task_store_factory: ActiveTaskStoreFactory | None = None,
        get_message_count: MessageCountGetter | None = None,
    ):
        self._get_session_id = get_session_id
        self._active_task_store_factory = active_task_store_factory
        self._get_message_count = get_message_count

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTION_VALUES),
                    "description": "Required. Operation to perform on the active task.",
                },
                "task": {
                    "type": "string",
                    "description": "Required for action='set'. Free-form task request used to create/replace ACTIVE_TASK.md.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "status": {
                    "type": "string",
                    "enum": list(_ALLOWED_STATUSES),
                    "description": "Optional for action='update'. New task status.",
                },
                "current_step": {
                    "type": "string",
                    "description": "Optional for action='update'. Replacement current step.",
                },
                "next_step": {
                    "type": "string",
                    "description": "Optional for action='update' or action='complete_step'. Replacement/planned next step.",
                },
                "completed_step": {
                    "type": "string",
                    "description": "Optional for action='update'. Step to append under Completed steps.",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional for action='update'. Open questions/blockers; pass [] or ['none'] to clear.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional short reason/evidence to record in task history.",
                },
            },
            "required": ["action"],
        }

    def _resolve_store(self) -> tuple[str, ActiveTaskStore] | str:
        session_id = self._get_session_id()
        if not session_id:
            return "Error: current session_id is unavailable. task_update requires an active session context."
        if self._active_task_store_factory is None:
            return "Error: ACTIVE_TASK.md store is unavailable in this runtime."
        store = self._active_task_store_factory(session_id)
        if store is None:
            return "Error: ACTIVE_TASK.md store is unavailable in this runtime."
        return session_id, store

    async def _mark_processed(self, session_id: str, store: ActiveTaskStore) -> None:
        if self._get_message_count is None:
            return
        count = await self._get_message_count(session_id)
        store.set_processed_index(session_id, max(0, int(count)))

    @staticmethod
    def _render_result(prefix: str, store: ActiveTaskStore) -> str:
        return f"{prefix}\n\n# Active Task\n\n{store.read_managed_block()}"

    async def _execute(self, **kwargs: Any) -> str:
        resolved = self._resolve_store()
        if isinstance(resolved, str):
            return resolved
        session_id, store = resolved

        action = str(kwargs["action"])
        note = str(kwargs.get("note") or "").strip()

        if action == "show":
            rendered = store.render_full_for_user()
            return rendered or "No active task."

        if action == "reset":
            store.clear(session_id)
            await self._mark_processed(session_id, store)
            store.append_event("reset", "tool", details={"note": note} if note else None)
            return self._render_result("Task reset.", store)

        if action == "set":
            task = str(kwargs.get("task") or "").strip()
            if not task:
                return "Error: action='set' requires a non-empty task."
            task_block = build_task_block_from_text(task, force=True)
            if not task_block:
                return "Error: action='set' could not create an active task from the provided task text."
            store.write_managed_block(task_block)
            await self._mark_processed(session_id, store)
            details = {"task": task}
            if note:
                details["note"] = note
            store.append_event("set", "tool", details=details)
            return self._render_result("Task set.", store)

        if store.read_status() == "inactive":
            return "Error: no active task to update. Use action='set' first."

        if action == "advance":
            current_block = store.read_managed_block()
            current_step = _extract_task_field(current_block, "Current step")
            next_step = _extract_task_field(current_block, "Next step")
            if next_step == "not set":
                return "Error: cannot advance because Next step is not set."
            store.update_fields(
                status="active",
                current_step=next_step,
                next_step="not set",
                append_completed_step=current_step,
                force=True,
            )
            await self._mark_processed(session_id, store)
            details = {"completed_step": current_step, "new_current_step": next_step}
            if note:
                details["note"] = note
            store.append_event("advance", "tool", details=details)
            return self._render_result("Task advanced.", store)

        if action == "complete_step":
            current_block = store.read_managed_block()
            current_step = _extract_task_field(current_block, "Current step")
            next_step_override = kwargs.get("next_step")
            rendered = store.complete_current_step(
                next_step_override=str(next_step_override).strip() if next_step_override is not None else None
            )
            if rendered is None:
                return "Error: cannot complete step because Current step is not set."
            await self._mark_processed(session_id, store)
            details = {"completed_step": current_step}
            if next_step_override:
                details["next_step_override"] = str(next_step_override)
            if note:
                details["note"] = note
            store.append_event("complete_step", "tool", details=details)
            return f"Task step completed.\n\n# Active Task\n\n{rendered}"

        if action == "update":
            status = kwargs.get("status")
            current_step = kwargs.get("current_step")
            next_step = kwargs.get("next_step")
            completed_step = kwargs.get("completed_step")
            open_questions = kwargs.get("open_questions")
            if not any(
                value is not None
                for value in (status, current_step, next_step, completed_step, open_questions)
            ):
                return "Error: action='update' requires at least one field to update."

            cleaned_questions = None
            if open_questions is not None:
                cleaned_questions = [str(item).strip() for item in open_questions if str(item).strip()]
                if any(item.lower() == "none" for item in cleaned_questions):
                    cleaned_questions = ["none"]

            rendered = store.update_fields(
                status=str(status).strip() if status is not None else None,
                current_step=str(current_step).strip() if current_step is not None else None,
                next_step=str(next_step).strip() if next_step is not None else None,
                open_questions=cleaned_questions,
                append_completed_step=str(completed_step).strip() if completed_step is not None else None,
                force=True,
            )
            await self._mark_processed(session_id, store)
            details: dict[str, Any] = {}
            for key in ("status", "current_step", "next_step", "completed_step"):
                if kwargs.get(key) is not None:
                    details[key] = str(kwargs[key])
            if cleaned_questions is not None:
                details["open_questions"] = cleaned_questions
            if note:
                details["note"] = note
            store.append_event("update", "tool", details=details)
            return f"Task updated.\n\n# Active Task\n\n{rendered}"

        return f"Error: unsupported task_update action: {action}"
