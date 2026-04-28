"""Structured work progress state for multi-step agent turns."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..storage import StoredWorkState
from .completion_gate import CompletionGateResult
from .execution import ExecutionResult
from .task_intent import TaskIntent


_CODING_KINDS = {"debug", "implementation", "refactor", "review"}
_TERMINAL_STATUSES = {"blocked", "complete", "waiting_user"}
_FOLLOW_UP_OBJECTIVES = {
    "continue",
    "keep going",
    "do it",
    "fix it",
    "handle it",
    "make it better",
    "處理一下",
    "幫我處理",
    "繼續",
    "修一下",
    "搞定",
}


@dataclass(frozen=True)
class WorkPlan:
    """Small durable plan derived from the user intent."""

    objective: str
    kind: str
    steps: tuple[str, ...]
    constraints: tuple[str, ...]
    done_criteria: tuple[str, ...]
    long_running: bool
    coding_task: bool
    expects_code_change: bool
    expects_verification: bool

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "objective": self.objective,
            "kind": self.kind,
            "steps": list(self.steps),
            "constraints": list(self.constraints),
            "done_criteria": list(self.done_criteria),
            "long_running": self.long_running,
            "coding_task": self.coding_task,
            "expects_code_change": self.expects_code_change,
            "expects_verification": self.expects_verification,
        }


@dataclass(frozen=True)
class WorkProgressUpdate:
    """One pass worth of structured progress signals."""

    status: str
    pass_index: int
    auto_continue_attempts: int
    progress_signals: tuple[str, ...]
    has_progress: bool
    file_change_count: int
    touched_paths: tuple[str, ...]
    verification_required: bool
    verification_attempted: bool
    verification_passed: bool
    completion_status: str
    completion_reason: str
    next_action: str
    continuation_budget: int

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "pass_index": self.pass_index,
            "auto_continue_attempts": self.auto_continue_attempts,
            "progress_signals": list(self.progress_signals),
            "has_progress": self.has_progress,
            "file_change_count": self.file_change_count,
            "touched_paths": list(self.touched_paths),
            "verification_required": self.verification_required,
            "verification_attempted": self.verification_attempted,
            "verification_passed": self.verification_passed,
            "completion_status": self.completion_status,
            "completion_reason": self.completion_reason,
            "next_action": self.next_action,
            "continuation_budget": self.continuation_budget,
        }


@dataclass(frozen=True)
class WorkboardState:
    """Typed durable view of remaining work for one task."""

    pending_steps: tuple[str, ...] = ()
    completed_steps: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    verification_targets: tuple[str, ...] = ()
    resume_hint: str = ""
    last_progress_signals: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "pending_steps": list(self.pending_steps),
            "completed_steps": list(self.completed_steps),
            "blockers": list(self.blockers),
            "verification_targets": list(self.verification_targets),
            "resume_hint": self.resume_hint,
            "last_progress_signals": list(self.last_progress_signals),
        }


class WorkProgressService:
    """Create a coherent work state from intent, execution, and completion signals."""

    def __init__(self, *, default_continuation_budget: int = 1, long_running_continuation_budget: int = 3):
        self.default_continuation_budget = max(0, default_continuation_budget)
        self.long_running_continuation_budget = max(self.default_continuation_budget, long_running_continuation_budget)

    def create_plan(self, task_intent: TaskIntent) -> WorkPlan | None:
        """Return a plan only for actionable tasks, not casual conversation."""
        if not task_intent.should_seed_active_task:
            return None

        steps: list[str]
        if task_intent.expects_code_change:
            steps = [
                "inspect relevant code",
                "make the smallest correct change",
                "verify the result" if task_intent.expects_verification else "review the result and finalize",
            ]
        elif task_intent.kind == "debug":
            steps = ["inspect the relevant context", "identify the root cause", "state the diagnosis or blocker"]
        elif task_intent.kind in {"analysis", "review"}:
            steps = ["inspect the relevant context", "collect concrete evidence", "deliver the findings clearly"]
        elif task_intent.long_running:
            steps = ["make measurable progress", "verify or summarize remaining work"]
        else:
            steps = ["complete the requested task"]

        return WorkPlan(
            objective=task_intent.objective,
            kind=task_intent.kind,
            steps=tuple(steps),
            constraints=tuple(task_intent.constraints),
            done_criteria=tuple(task_intent.done_criteria),
            long_running=task_intent.long_running,
            coding_task=task_intent.kind in _CODING_KINDS,
            expects_code_change=task_intent.expects_code_change,
            expects_verification=task_intent.expects_verification,
        )

    def resolve_intent(self, task_intent: TaskIntent, state: StoredWorkState | None) -> TaskIntent:
        """Reuse persisted task semantics when the new user turn is only a vague continuation."""
        if (
            state is None
            or state.status not in {"active", "blocked", "waiting_user"}
            or (
                not task_intent.needs_clarification
                and task_intent.objective.strip().lower() not in _FOLLOW_UP_OBJECTIVES
            )
            or not (task_intent.long_running or task_intent.objective.strip().lower() in _FOLLOW_UP_OBJECTIVES)
        ):
            return task_intent
        return TaskIntent(
            kind=state.kind,
            objective=state.objective,
            constraints=tuple(state.constraints),
            done_criteria=tuple(state.done_criteria),
            needs_clarification=False,
            verification_hint=(
                task_intent.verification_hint
                or ("Run the requested verification and report pass or fail." if state.expects_verification else None)
            ),
            long_running=bool(state.long_running),
            expects_code_change=bool(state.expects_code_change),
            expects_verification=bool(state.expects_verification),
        )

    def build_initial_state(
        self,
        *,
        session_id: str,
        task_intent: TaskIntent,
        work_plan: WorkPlan | None,
        existing_state: StoredWorkState | None = None,
    ) -> StoredWorkState | None:
        """Create a new persisted state when a concrete task begins."""
        if work_plan is None:
            if existing_state is not None and task_intent.needs_clarification and task_intent.long_running:
                return existing_state
            return None
        if self._should_resume_existing_state(task_intent, work_plan, existing_state):
            return self._resume_existing_state(existing_state, work_plan)
        if existing_state is not None and task_intent.needs_clarification and task_intent.long_running:
            return existing_state

        numbered_steps = _numbered_steps(work_plan.steps)
        now = time.time()
        pending_steps = tuple(step for step in numbered_steps if step != "not set")
        return StoredWorkState(
            session_id=session_id,
            objective=work_plan.objective,
            kind=work_plan.kind,
            status="active",
            steps=numbered_steps,
            constraints=tuple(work_plan.constraints),
            done_criteria=tuple(work_plan.done_criteria),
            long_running=work_plan.long_running,
            coding_task=work_plan.coding_task,
            expects_code_change=work_plan.expects_code_change,
            expects_verification=work_plan.expects_verification,
            current_step=numbered_steps[0] if numbered_steps else "not set",
            next_step=numbered_steps[1] if len(numbered_steps) > 1 else "not set",
            completed_steps=(),
            pending_steps=pending_steps,
            blockers=(),
            verification_targets=_derive_verification_targets(
                work_plan.done_criteria,
                expects_verification=work_plan.expects_verification,
            ),
            resume_hint=_build_resume_hint(
                status="active",
                current_step=numbered_steps[0] if numbered_steps else "not set",
                next_step=numbered_steps[1] if len(numbered_steps) > 1 else "not set",
                blockers=(),
                next_action="continue_work",
            ),
            last_progress_signals=(),
            file_change_count=0,
            touched_paths=(),
            verification_attempted=False,
            verification_passed=False,
            last_next_action="continue_work",
            metadata={"source": "work_progress", "schema_version": 1},
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def extract_workboard(state: StoredWorkState | None) -> WorkboardState:
        """Return the normalized structured workboard metadata for one state."""
        if state is None:
            return WorkboardState()
        legacy = _legacy_workboard(state)
        pending_steps = tuple(state.pending_steps) or tuple(_string_list(legacy.get("pending_steps")))
        blockers = tuple(state.blockers) or tuple(_string_list(legacy.get("blockers")))
        verification_targets = tuple(state.verification_targets) or tuple(_string_list(legacy.get("verification_targets")))
        resume_hint = state.resume_hint or str(legacy.get("resume_hint") or "")
        last_progress_signals = tuple(state.last_progress_signals) or tuple(_string_list(legacy.get("last_progress_signals")))
        if not pending_steps:
            pending_steps = tuple(step for step in state.steps if step not in state.completed_steps and step != "not set")
        if not blockers and state.status in {"blocked", "waiting_user"} and state.last_next_action:
            blockers = (state.last_next_action,)
        if not verification_targets:
            verification_targets = _derive_verification_targets(
                state.done_criteria,
                expects_verification=state.expects_verification,
            )
        if not resume_hint:
            resume_hint = _build_resume_hint(
                status=state.status,
                current_step=state.current_step,
                next_step=state.next_step,
                blockers=blockers,
                next_action=state.last_next_action,
            )
        return WorkboardState(
            pending_steps=pending_steps,
            completed_steps=tuple(state.completed_steps),
            blockers=blockers,
            verification_targets=verification_targets,
            resume_hint=resume_hint,
            last_progress_signals=last_progress_signals,
        )

    def update_state(
        self,
        *,
        session_id: str,
        state: StoredWorkState | None,
        task_intent: TaskIntent,
        work_plan: WorkPlan | None,
        progress: WorkProgressUpdate,
        completion_result: CompletionGateResult,
        delegate_task_id: str | None = None,
        delegate_prompt_type: str | None = None,
    ) -> StoredWorkState | None:
        """Apply one turn's progress and completion result to persisted work state."""
        current = state or self.build_initial_state(
            session_id=session_id,
            task_intent=task_intent,
            work_plan=work_plan,
        )
        if current is None:
            return None

        steps = tuple(current.steps)
        status = _map_state_status(completion_result, progress)
        completed_steps = _completed_steps(
            steps,
            current.completed_steps,
            progress,
            expects_code_change=current.expects_code_change,
        )
        current_step, next_step = _state_steps(
            steps,
            progress,
            expects_code_change=current.expects_code_change,
            expects_verification=current.expects_verification,
        )
        touched_paths = tuple(dict.fromkeys((*current.touched_paths, *progress.touched_paths)))
        file_change_count = max(0, current.file_change_count + progress.file_change_count)
        verification_attempted = current.verification_attempted or progress.verification_attempted
        verification_passed = current.verification_passed or progress.verification_passed
        if progress.file_change_count > 0 and current.expects_verification and not progress.verification_passed:
            verification_passed = False

        active_delegate_task_id = delegate_task_id or current.active_delegate_task_id
        active_delegate_prompt_type = delegate_prompt_type or current.active_delegate_prompt_type
        if completion_result.status == "complete":
            active_delegate_task_id = None
            active_delegate_prompt_type = None

        metadata = dict(current.metadata or {})
        metadata.pop("workboard", None)
        workboard = self._build_workboard(
            steps=steps,
            completed_steps=completed_steps,
            status=status,
            current_step=current_step,
            next_step=next_step,
            done_criteria=current.done_criteria,
            expects_verification=current.expects_verification,
            progress=progress,
            completion_result=completion_result,
        )

        return StoredWorkState(
            session_id=current.session_id,
            objective=current.objective,
            kind=current.kind,
            status=status,
            steps=steps,
            constraints=tuple(current.constraints),
            done_criteria=tuple(current.done_criteria),
            long_running=current.long_running,
            coding_task=current.coding_task,
            expects_code_change=current.expects_code_change,
            expects_verification=current.expects_verification,
            current_step=current_step,
            next_step=next_step,
            completed_steps=completed_steps,
            pending_steps=workboard.pending_steps,
            blockers=workboard.blockers,
            verification_targets=workboard.verification_targets,
            resume_hint=workboard.resume_hint,
            last_progress_signals=workboard.last_progress_signals,
            file_change_count=file_change_count,
            touched_paths=touched_paths,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
            last_next_action=progress.next_action,
            active_delegate_task_id=active_delegate_task_id,
            active_delegate_prompt_type=active_delegate_prompt_type,
            metadata=metadata,
            created_at=current.created_at or time.time(),
            updated_at=time.time(),
        )

    @staticmethod
    def render_state_summary(state: StoredWorkState | None) -> str:
        """Render a compact state block that can survive compaction and retries."""
        if state is None:
            return ""
        lines = [
            "## Structured Work State",
            f"- Objective: {state.objective}",
            f"- Kind: {state.kind}",
            f"- Status: {state.status}",
            f"- Current step: {state.current_step}",
            f"- Next step: {state.next_step}",
            f"- Verification: attempted={state.verification_attempted} passed={state.verification_passed}",
            f"- Last next action: {state.last_next_action or 'none'}",
        ]
        if state.constraints:
            lines.extend(["- Constraints:", *[f"  - {item}" for item in state.constraints]])
        if state.done_criteria:
            lines.extend(["- Definition of done:", *[f"  - {item}" for item in state.done_criteria]])
        if state.completed_steps:
            lines.extend(["- Completed steps:", *[f"  - {step}" for step in state.completed_steps]])
        workboard = WorkProgressService.extract_workboard(state)
        if workboard.pending_steps:
            lines.extend(["- Pending steps:", *[f"  - {step}" for step in workboard.pending_steps]])
        if workboard.verification_targets:
            lines.extend(["- Verification targets:", *[f"  - {item}" for item in workboard.verification_targets]])
        if workboard.blockers:
            lines.extend(["- Blockers:", *[f"  - {item}" for item in workboard.blockers]])
        if workboard.resume_hint:
            lines.append(f"- Resume hint: {workboard.resume_hint}")
        if state.touched_paths:
            lines.extend(["- Touched paths:", *[f"  - {path}" for path in state.touched_paths[:12]]])
        if state.active_delegate_task_id:
            lines.append(
                f"- Active delegate: {state.active_delegate_prompt_type or 'subagent'} ({state.active_delegate_task_id})"
            )
        return "\n".join(lines)

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        execution_result: ExecutionResult,
        auto_continue_attempts: int,
        pass_index: int,
    ) -> WorkProgressUpdate:
        """Summarize the current pass and choose the next high-level action."""
        signals = self._progress_signals(execution_result)
        continuation_budget = self.continuation_budget(task_intent)
        status = self._status(completion_result)
        return WorkProgressUpdate(
            status=status,
            pass_index=max(1, pass_index),
            auto_continue_attempts=max(0, auto_continue_attempts),
            progress_signals=signals,
            has_progress=bool(signals),
            file_change_count=max(0, execution_result.file_change_count),
            touched_paths=tuple(execution_result.touched_paths),
            verification_required=completion_result.verification_required,
            verification_attempted=completion_result.verification_attempted,
            verification_passed=completion_result.verification_passed,
            completion_status=completion_result.status,
            completion_reason=completion_result.reason,
            next_action=self._next_action(completion_result, has_progress=bool(signals), attempts=auto_continue_attempts, budget=continuation_budget),
            continuation_budget=continuation_budget,
        )

    def continuation_budget(self, task_intent: TaskIntent) -> int:
        if task_intent.long_running or task_intent.kind in _CODING_KINDS:
            return self.long_running_continuation_budget
        return self.default_continuation_budget

    @staticmethod
    def _should_resume_existing_state(
        task_intent: TaskIntent,
        work_plan: WorkPlan,
        existing_state: StoredWorkState | None,
    ) -> bool:
        if existing_state is None:
            return False
        if existing_state.status not in {"active", "blocked", "waiting_user"}:
            return False
        if not existing_state.objective.strip():
            return False
        if task_intent.objective.strip().lower() in _FOLLOW_UP_OBJECTIVES:
            return True
        return (
            existing_state.objective.strip().lower() == work_plan.objective.strip().lower()
            and existing_state.kind == work_plan.kind
        )

    def _resume_existing_state(
        self,
        existing_state: StoredWorkState,
        work_plan: WorkPlan,
    ) -> StoredWorkState:
        steps = tuple(existing_state.steps) or _numbered_steps(work_plan.steps)
        metadata = dict(existing_state.metadata or {})
        metadata.pop("workboard", None)
        existing_workboard = self.extract_workboard(existing_state)
        return StoredWorkState(
            session_id=existing_state.session_id,
            objective=existing_state.objective or work_plan.objective,
            kind=existing_state.kind or work_plan.kind,
            status=existing_state.status,
            steps=steps,
            constraints=tuple(existing_state.constraints or work_plan.constraints),
            done_criteria=tuple(existing_state.done_criteria or work_plan.done_criteria),
            long_running=bool(existing_state.long_running or work_plan.long_running),
            coding_task=bool(existing_state.coding_task or work_plan.coding_task),
            expects_code_change=bool(existing_state.expects_code_change or work_plan.expects_code_change),
            expects_verification=bool(existing_state.expects_verification or work_plan.expects_verification),
            current_step=existing_state.current_step,
            next_step=existing_state.next_step,
            completed_steps=tuple(existing_state.completed_steps),
            pending_steps=tuple(existing_state.pending_steps) or existing_workboard.pending_steps,
            blockers=tuple(existing_state.blockers) or existing_workboard.blockers,
            verification_targets=tuple(existing_state.verification_targets) or existing_workboard.verification_targets,
            resume_hint=existing_state.resume_hint or existing_workboard.resume_hint,
            last_progress_signals=tuple(existing_state.last_progress_signals) or existing_workboard.last_progress_signals,
            file_change_count=int(existing_state.file_change_count),
            touched_paths=tuple(existing_state.touched_paths),
            verification_attempted=bool(existing_state.verification_attempted),
            verification_passed=bool(existing_state.verification_passed),
            last_next_action=existing_state.last_next_action or "continue_work",
            active_delegate_task_id=existing_state.active_delegate_task_id,
            active_delegate_prompt_type=existing_state.active_delegate_prompt_type,
            metadata=metadata,
            created_at=existing_state.created_at or time.time(),
            updated_at=time.time(),
        )

    @staticmethod
    def _build_workboard(
        *,
        steps: tuple[str, ...],
        completed_steps: tuple[str, ...],
        status: str,
        current_step: str,
        next_step: str,
        done_criteria: tuple[str, ...],
        expects_verification: bool,
        progress: WorkProgressUpdate,
        completion_result: CompletionGateResult,
    ) -> WorkboardState:
        pending_steps = [step for step in steps if step not in completed_steps and step != "not set"]
        if current_step != "not set" and current_step not in completed_steps and current_step not in pending_steps:
            pending_steps.insert(0, current_step)
        blockers = _derive_blockers(completion_result)
        return WorkboardState(
            pending_steps=tuple(pending_steps),
            completed_steps=completed_steps,
            blockers=blockers,
            verification_targets=_derive_verification_targets(
                done_criteria,
                expects_verification=expects_verification,
            ),
            resume_hint=_build_resume_hint(
                status=status,
                current_step=current_step,
                next_step=next_step,
                blockers=blockers,
                next_action=progress.next_action,
            ),
            last_progress_signals=progress.progress_signals,
        )

    @staticmethod
    def _progress_signals(execution_result: ExecutionResult) -> tuple[str, ...]:
        signals: list[str] = []
        if execution_result.executed_tool_calls > 0:
            signals.append("tool_calls")
        if execution_result.file_change_count > 0:
            signals.append("file_changes")
        if execution_result.verification_attempted:
            signals.append("verification_attempted")
        if execution_result.verification_passed:
            signals.append("verification_passed")
        if execution_result.context_compactions > 0:
            signals.append("context_compaction")
        if execution_result.had_tool_error:
            signals.append("tool_error")
        return tuple(signals)

    @staticmethod
    def _status(completion_result: CompletionGateResult) -> str:
        if completion_result.status in _TERMINAL_STATUSES:
            return completion_result.status
        if completion_result.status == "needs_verification":
            return "verifying"
        return "working"

    @staticmethod
    def _next_action(
        completion_result: CompletionGateResult,
        *,
        has_progress: bool,
        attempts: int,
        budget: int,
    ) -> str:
        if completion_result.status == "complete":
            return "finalize"
        if completion_result.status in {"blocked", "waiting_user"}:
            return completion_result.status
        if attempts >= budget:
            return "stop_budget_exhausted"
        if attempts > 0 and not has_progress:
            return "stop_no_progress"
        if completion_result.status == "needs_verification":
            return "continue_verification"
        return "continue_work"


def _numbered_steps(steps: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{index}. {step}" for index, step in enumerate(steps, start=1))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _legacy_workboard(state: StoredWorkState) -> dict[str, Any]:
    metadata = state.metadata if isinstance(state.metadata, dict) else {}
    payload = metadata.get("workboard")
    return payload if isinstance(payload, dict) else {}


def _derive_verification_targets(
    done_criteria: tuple[str, ...],
    *,
    expects_verification: bool,
) -> tuple[str, ...]:
    if not expects_verification:
        return ()
    targets = [
        str(item).strip()
        for item in done_criteria
        if str(item).strip()
        and any(token in str(item).lower() for token in ("verify", "verification", "test", "build", "check", "pass"))
    ]
    if not targets:
        targets = ["Run the requested verification before treating the task as complete."]
    return tuple(dict.fromkeys(targets))


def _derive_blockers(completion_result: CompletionGateResult) -> tuple[str, ...]:
    if completion_result.status in {"blocked", "waiting_user"}:
        detail = completion_result.active_task_detail or completion_result.reason
        if detail:
            return (detail,)
    return ()


def _build_resume_hint(
    *,
    status: str,
    current_step: str,
    next_step: str,
    blockers: tuple[str, ...],
    next_action: str,
) -> str:
    if status == "done":
        return "Task is complete; only continue if the user asks for follow-up work."
    if blockers:
        return f"Resolve blocker first: {blockers[0]}"
    if next_action == "continue_verification":
        return "Resume by running or fixing the required verification."
    if current_step and current_step != "not set":
        return f"Resume at current step: {current_step}"
    if next_step and next_step != "not set":
        return f"Resume with next step: {next_step}"
    return "Continue the active task from the latest recorded state."


def _map_state_status(completion_result: CompletionGateResult, progress: WorkProgressUpdate) -> str:
    if completion_result.status == "complete":
        return "done"
    if completion_result.status in {"blocked", "waiting_user"}:
        return completion_result.status
    if progress.status == "verifying":
        return "active"
    return "active"


def _completed_steps(
    steps: tuple[str, ...],
    existing: tuple[str, ...],
    progress: WorkProgressUpdate,
    *,
    expects_code_change: bool,
) -> tuple[str, ...]:
    completed = list(existing)
    if not completed and steps:
        completed.append(steps[0])
    if expects_code_change and progress.file_change_count > 0 and len(steps) > 1 and steps[1] not in completed:
        completed.append(steps[1])
    if progress.completion_status == "complete":
        for step in steps:
            if step not in completed:
                completed.append(step)
    return tuple(completed)


def _state_steps(
    steps: tuple[str, ...],
    progress: WorkProgressUpdate,
    *,
    expects_code_change: bool,
    expects_verification: bool,
) -> tuple[str, str]:
    if progress.completion_status == "complete":
        return "not set", "not set"
    if progress.completion_status in {"blocked", "waiting_user"}:
        current = steps[-1] if steps else "not set"
        return current, "not set"
    if progress.next_action == "continue_verification":
        return (steps[-1] if steps else "not set"), "not set"
    if expects_code_change and progress.file_change_count <= 0 and len(steps) >= 2:
        next_step = steps[2] if len(steps) > 2 else "not set"
        return steps[1], next_step
    if expects_verification and steps:
        return steps[-1], "not set"
    if progress.next_action == "continue_work" and steps:
        current = steps[-1] if progress.file_change_count > 0 else (steps[1] if len(steps) > 1 else steps[0])
        next_step = "not set"
        if progress.file_change_count <= 0 and len(steps) > 2:
            next_step = steps[2]
        return current, next_step
    return "not set", "not set"
