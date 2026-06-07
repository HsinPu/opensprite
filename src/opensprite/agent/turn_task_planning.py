"""Pre-work task planning for one inbound user turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..bus.message import UserMessage
from ..storage import StoredWorkState
from .task_contract import TaskContextDecision, TaskContextResolver, TaskIntent, TaskIntentService


@dataclass(frozen=True)
class TurnTaskPlanningResult:
    """Task intent and initial work state prepared before execution branches."""

    task_intent: TaskIntent
    task_context_decision: TaskContextDecision
    existing_work_state: StoredWorkState | None
    work_plan: Any | None
    current_work_state: StoredWorkState | None


class TurnTaskPlanningService:
    """Resolve deterministic task shape before the normal execution path starts."""

    def __init__(
        self,
        *,
        task_intents: TaskIntentService,
        work_progress: Any,
        read_active_task_snapshot: Callable[[str], str],
        build_runtime_message: Callable[[str, dict[str, Any] | None], str],
    ) -> None:
        self.task_intents = task_intents
        self.work_progress = work_progress
        self._read_active_task_snapshot = read_active_task_snapshot
        self._build_runtime_message = build_runtime_message

    def plan(
        self,
        *,
        user_message: UserMessage,
        session_id: str,
        user_metadata: dict[str, Any] | None,
        existing_work_state: StoredWorkState | None,
    ) -> TurnTaskPlanningResult:
        """Return task intent, pre-work context, and initial work state for one turn."""
        task_intent = self.task_intents.classify(
            user_message.text,
            images=user_message.images,
            audios=user_message.audios,
            videos=user_message.videos,
            metadata=user_message.metadata,
        )
        task_context_decision = TaskContextResolver.resolve_deterministic(
            current_message=self._build_runtime_message(user_message.text, user_metadata),
            task_intent=task_intent,
            active_task=self._read_active_task_snapshot(session_id),
            work_state_summary=self.work_progress.render_state_summary(existing_work_state),
        )
        task_intent = self.work_progress.resolve_intent(
            task_intent,
            existing_work_state,
            task_context_decision=task_context_decision,
        )
        work_plan = self.work_progress.create_plan(task_intent)
        current_work_state = self.work_progress.build_initial_state(
            session_id=session_id,
            task_intent=task_intent,
            work_plan=work_plan,
            existing_state=existing_work_state,
            task_context_decision=task_context_decision,
        )
        return TurnTaskPlanningResult(
            task_intent=task_intent,
            task_context_decision=task_context_decision,
            existing_work_state=existing_work_state,
            work_plan=work_plan,
            current_work_state=current_work_state,
        )
