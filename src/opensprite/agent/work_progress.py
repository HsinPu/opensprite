"""Structured work progress state for multi-step agent turns."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from ..config import TaskMessagesConfig
from ..runs.events import ACTIVE_TASK_SEEDED_EVENT
from ..storage import StorageProvider, StoredDelegatedTask, StoredWorkState
from ..storage.base import (
    coerce_stored_delegated_tasks,
    get_storage_message_count,
    legacy_delegated_tasks,
    selected_delegated_task,
)
from ..documents.active_task import (
    ACTIVE_ACTIVE_TASK_STATUS,
    BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION,
    BLOCKED_ACTIVE_TASK_STATUS,
    DONE_ACTIVE_TASK_STATUS,
    TASK_BOUNDARY_CONFIRMATION_EVENT,
    TASK_BOUNDARY_CONFIRMATION_RESOLVED_EVENT,
    WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION,
    WAITING_USER_ACTIVE_TASK_STATUS,
    _extract_task_field,
    build_task_block_from_intent_fields,
    build_task_block_from_text,
    clear_open_questions,
    clears_active_task_open_questions,
    create_active_task_store,
    is_current_active_task_status,
    is_current_or_done_active_task_status,
    is_inactive_active_task_status,
    is_terminal_active_task_status,
)
from ..utils.log import logger
from .completion_gate import CompletionGateResult
from .completion_status import (
    is_complete_completion_status,
    is_blocking_completion_status,
    is_incomplete_completion_status,
    needs_review_completion_status,
    needs_verification_completion_status,
    is_terminal_completion_status,
    requires_evidence_follow_up,
)
from .execution import ExecutionResult, MAX_TOOL_ITERATIONS_STOP_REASON, is_max_tool_iterations_stop_reason
from .harness_policy import (
    ANALYSIS_TASK_TYPE,
    CODE_CHANGE_TASK_TYPE,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    HarnessProfile,
    VERIFICATION_REQUIREMENT_KIND,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_CHANGE_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP,
    is_chat_profile_name,
    is_coding_profile_name,
    is_media_profile_name,
    is_ops_profile_name,
    is_research_profile_name,
    normalize_profile_name,
)
from .task_resolver import (
    BOUNDARY_CONTINUE_REPLY_COMMAND,
    BOUNDARY_SWITCH_REPLY_COMMAND,
    CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE,
    PRESERVE_STATE_RESET_CONTINUATION_TYPES,
    TaskContextDecision,
    TaskIntent,
    TaskObjectiveDecision,
    is_ambiguous_boundary_continuation_type,
    is_current_task_continuation_type,
    is_current_task_replacement_type,
    intent_supports_default_work_plan,
)
from .workflows import is_workflow_failed_status


NEXT_ACTION_FINALIZE = "finalize"
NEXT_ACTION_STOP_BUDGET_EXHAUSTED = "stop_budget_exhausted"
NEXT_ACTION_STOP_NO_PROGRESS = "stop_no_progress"
NEXT_ACTION_CONTINUE_VERIFICATION = "continue_verification"
NEXT_ACTION_COLLECT_REVIEW_EVIDENCE = "collect_review_evidence"
NEXT_ACTION_ADDRESS_REVIEW_FINDINGS = "address_review_findings"
NEXT_ACTION_CONTINUE_REVIEW = "continue_review"
NEXT_ACTION_CONTINUE_WORK = "continue_work"
DEFAULT_WORK_STEP_NOT_SET = "not set"
TASK_DONE_RESUME_HINT = "Task is complete; only continue if the user asks for follow-up work."
VERIFICATION_REQUIRED_RESUME_HINT = "Resume by running or fixing the required verification."
REVIEW_FOLLOW_UP_NEXT_ACTIONS = frozenset(
    {
        NEXT_ACTION_COLLECT_REVIEW_EVIDENCE,
        NEXT_ACTION_ADDRESS_REVIEW_FINDINGS,
    }
)
REVIEW_PHASE_NEXT_ACTIONS = frozenset(
    {
        NEXT_ACTION_CONTINUE_REVIEW,
        *REVIEW_FOLLOW_UP_NEXT_ACTIONS,
    }
)
_DEFAULT_VERIFICATION_TARGET = "relevant tests or checks pass, or the verification gap is stated"
_WORK_STATE_ACTIVE_STATUS = "active"
_WORK_STATE_DONE_STATUS = "done"
_WORK_PROGRESS_VERIFYING_STATUS = "verifying"
_WORK_PROGRESS_REVIEWING_STATUS = "reviewing"
_WORK_PROGRESS_WORKING_STATUS = "working"
_NEXT_ACTION_FINALIZE = NEXT_ACTION_FINALIZE
_NEXT_ACTION_STOP_BUDGET_EXHAUSTED = NEXT_ACTION_STOP_BUDGET_EXHAUSTED
_NEXT_ACTION_STOP_NO_PROGRESS = NEXT_ACTION_STOP_NO_PROGRESS
_NEXT_ACTION_CONTINUE_VERIFICATION = NEXT_ACTION_CONTINUE_VERIFICATION
_NEXT_ACTION_COLLECT_REVIEW_EVIDENCE = NEXT_ACTION_COLLECT_REVIEW_EVIDENCE
_NEXT_ACTION_ADDRESS_REVIEW_FINDINGS = NEXT_ACTION_ADDRESS_REVIEW_FINDINGS
_NEXT_ACTION_CONTINUE_REVIEW = NEXT_ACTION_CONTINUE_REVIEW
_NEXT_ACTION_CONTINUE_WORK = NEXT_ACTION_CONTINUE_WORK
_PROGRESS_SIGNAL_TOOL_CALLS = "tool_calls"
_PROGRESS_SIGNAL_FILE_CHANGES = "file_changes"
_PROGRESS_SIGNAL_VERIFICATION_ATTEMPTED = "verification_attempted"
_PROGRESS_SIGNAL_VERIFICATION_PASSED = "verification_passed"
_PROGRESS_SIGNAL_CONTEXT_COMPACTION = "context_compaction"
_PROGRESS_SIGNAL_TOOL_ERROR = "tool_error"
WORK_PROGRESS_METADATA_SOURCE_KEY = "source"
WORK_PROGRESS_METADATA_SOURCE = "work_progress"
WORK_PROGRESS_ACTIVE_TASK_EVENT_TYPE = "work_progress"
WORK_STEP_NOT_SET = "not set"


def normalize_next_action(value: str | None) -> str:
    return str(value or "").strip()


def is_verification_next_action(value: str | None) -> bool:
    return normalize_next_action(value) == NEXT_ACTION_CONTINUE_VERIFICATION


def is_continue_work_next_action(value: str | None) -> bool:
    return normalize_next_action(value) == NEXT_ACTION_CONTINUE_WORK


def is_review_follow_up_next_action(value: str | None) -> bool:
    return normalize_next_action(value) in REVIEW_FOLLOW_UP_NEXT_ACTIONS


def is_review_phase_next_action(value: str | None) -> bool:
    return normalize_next_action(value) in REVIEW_PHASE_NEXT_ACTIONS


def build_policy_resume_hint(
    *,
    status: str,
    current_step: str,
    next_step: str,
    blockers: tuple[str, ...],
    next_action: str,
    workflow: str = "",
    step_label: str = "",
    prompt_type: str = "",
    verification_action: str = "",
    verification_path: str = "",
    done_status: str = "done",
    unset_step: str = DEFAULT_WORK_STEP_NOT_SET,
) -> str:
    if status == done_status:
        return TASK_DONE_RESUME_HINT
    if blockers:
        return f"Resolve blocker first: {blockers[0]}"
    if is_verification_next_action(next_action):
        if workflow and step_label:
            return f"Resume by finishing verification around the {step_label} step in {workflow}."
        if verification_action and verification_path:
            return f"Resume by running verify {verification_action} for `{verification_path}`."
        return VERIFICATION_REQUIRED_RESUME_HINT
    if normalize_next_action(next_action) == NEXT_ACTION_COLLECT_REVIEW_EVIDENCE:
        if workflow and step_label and prompt_type:
            return f"Resume by running or rerunning the delegated {prompt_type} step ({step_label}) for {workflow}."
        if prompt_type:
            return f"Resume by running or rerunning the delegated {prompt_type} step for the changed code."
        return "Resume by running or rerunning a delegated review step for the changed code."
    if normalize_next_action(next_action) == NEXT_ACTION_ADDRESS_REVIEW_FINDINGS:
        if workflow:
            return f"Resume by addressing the review findings for {workflow} before rerunning review if needed."
        return "Resume by addressing the delegated review findings before treating the task as complete."
    if normalize_next_action(next_action) == NEXT_ACTION_CONTINUE_REVIEW:
        return "Resume by collecting review evidence or addressing delegated review findings."
    if workflow and step_label:
        return f"Resume with the {step_label} step in {workflow}."
    if current_step and current_step != unset_step:
        return f"Resume at current step: {current_step}"
    if next_step and next_step != unset_step:
        return f"Resume with next step: {next_step}"
    return "Continue the active task from the latest recorded state."


build_resume_hint = build_policy_resume_hint


def is_verification_work_progress(progress: Any) -> bool:
    """Return whether a structured progress update is in the verification phase."""
    return (
        is_verification_next_action(getattr(progress, "next_action", None))
        or str(getattr(progress, "status", "") or "").strip() == _WORK_PROGRESS_VERIFYING_STATUS
    )


def is_continue_work_progress(progress: Any) -> bool:
    """Return whether a structured progress update should resume regular work."""
    return is_continue_work_next_action(getattr(progress, "next_action", None))


def metadata_is_work_progress_source(metadata: dict[str, Any]) -> bool:
    """Return whether metadata was created by the structured work-progress service."""
    return str(metadata.get(WORK_PROGRESS_METADATA_SOURCE_KEY) or "").strip() == WORK_PROGRESS_METADATA_SOURCE


def _delegated_tasks_for_state(state: StoredWorkState | None) -> tuple[StoredDelegatedTask, ...]:
    if state is None:
        return ()
    return coerce_stored_delegated_tasks(state.delegated_tasks) or legacy_delegated_tasks(
        state.active_delegate_task_id,
        state.active_delegate_prompt_type,
    )


def _merge_delegated_tasks(
    existing_tasks: tuple[StoredDelegatedTask, ...],
    updates: tuple[StoredDelegatedTask, ...],
    *,
    clear_selection: bool,
) -> tuple[StoredDelegatedTask, ...]:
    by_id: dict[str, StoredDelegatedTask] = {task.task_id: task for task in existing_tasks if task.task_id}
    order = [task.task_id for task in existing_tasks if task.task_id]
    normalized_updates = coerce_stored_delegated_tasks(updates)
    now = time.time()

    for update in normalized_updates:
        previous = by_id.pop(update.task_id, None)
        if update.task_id in order:
            order.remove(update.task_id)
        order.append(update.task_id)
        by_id[update.task_id] = StoredDelegatedTask(
            task_id=update.task_id,
            prompt_type=update.prompt_type or (previous.prompt_type if previous is not None else None),
            status=update.status or (previous.status if previous is not None else "unknown"),
            selected=bool(update.selected),
            summary=update.summary or (previous.summary if previous is not None else ""),
            error=(
                update.error
                if update.error
                else ""
                if update.status and not is_workflow_failed_status(update.status)
                else previous.error if previous is not None else ""
            ),
            child_session_id=(
                update.child_session_id
                or previous.child_session_id if previous is not None else None
            ),
            last_child_run_id=(
                update.last_child_run_id
                or previous.last_child_run_id if previous is not None else None
            ),
            metadata={**(previous.metadata if previous is not None else {}), **dict(update.metadata or {})},
            created_at=(
                previous.created_at
                if previous is not None and previous.created_at
                else update.created_at
                or now
            ),
            updated_at=update.updated_at or now,
        )

    tasks = tuple(by_id[task_id] for task_id in order if task_id in by_id)
    if clear_selection:
        return tuple(replace(task, selected=False) for task in tasks)
    if normalized_updates:
        selected_task_id = next((task.task_id for task in reversed(normalized_updates) if task.selected), normalized_updates[-1].task_id)
        return tuple(replace(task, selected=task.task_id == selected_task_id) for task in tasks)
    return tasks


def _continues_existing_task(task_context_decision: TaskContextDecision | None) -> bool:
    if task_context_decision is None:
        return False
    return bool(
        task_context_decision.should_inherit_active_task
        or task_context_decision.continuation_type == CONTINUE_ACTIVE_TASK_CONTINUATION_TYPE
    )


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
    harness_profile: str = ""
    verification_policy: str = ""
    continuation_policy: str = ""

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
            "harness_profile": self.harness_profile,
            "verification_policy": self.verification_policy,
            "continuation_policy": self.continuation_policy,
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

    def create_plan(self, task_intent: TaskIntent, harness_profile: HarnessProfile | None = None) -> WorkPlan | None:
        """Return a plan only for actionable tasks, not casual conversation."""
        profile_name = normalize_profile_name(harness_profile.name if harness_profile is not None else "")
        if is_chat_profile_name(profile_name):
            return None
        if not _intent_supports_default_work_plan(task_intent) and profile_name == "":
            return None

        steps: list[str]
        if is_research_profile_name(profile_name):
            steps = ["search for relevant sources", "fetch or inspect source details", "answer with cited evidence"]
        elif is_coding_profile_name(profile_name):
            steps = [
                "inspect relevant workspace context",
                "make the smallest correct change or collect concrete workspace evidence",
                "run focused verification or state the verification gap",
                "summarize changes, evidence, and remaining risk",
            ]
        elif is_media_profile_name(profile_name):
            steps = ["inspect the referenced media", "produce the required media artifact", "answer using the artifact result"]
        elif is_ops_profile_name(profile_name):
            steps = ["inspect the requested operation", "obtain or honor required approval", "execute and validate", "report outcome and risk"]
        elif task_intent.kind == ANALYSIS_TASK_TYPE:
            steps = ["inspect the relevant context", "collect concrete evidence", "deliver the findings clearly"]
        elif task_intent.long_running:
            steps = ["make measurable progress", "verify or summarize remaining work"]
        else:
            steps = ["complete the requested task"]

        if harness_profile is not None:
            expects_code_change = _profile_requires_code_change(harness_profile)
            expects_verification = _profile_requires_verification(harness_profile)
        else:
            expects_code_change = False
            expects_verification = False
        done_criteria = list(task_intent.done_criteria)
        verification_done = _DEFAULT_VERIFICATION_TARGET
        if expects_verification and verification_done not in done_criteria:
            done_criteria.append(verification_done)

        return WorkPlan(
            objective=task_intent.objective,
            kind=task_intent.kind,
            steps=tuple(steps),
            constraints=tuple(task_intent.constraints),
            done_criteria=tuple(done_criteria),
            long_running=task_intent.long_running
            or is_coding_profile_name(profile_name)
            or is_research_profile_name(profile_name),
            coding_task=is_coding_profile_name(profile_name) or task_intent.expects_code_change or task_intent.expects_verification,
            expects_code_change=expects_code_change,
            expects_verification=expects_verification,
            harness_profile=profile_name,
            verification_policy=harness_profile.verification_policy if harness_profile is not None else "",
            continuation_policy=harness_profile.continuation_policy if harness_profile is not None else "",
        )

    def resolve_intent(
        self,
        task_intent: TaskIntent,
        state: StoredWorkState | None,
        *,
        task_context_decision: TaskContextDecision | None = None,
    ) -> TaskIntent:
        """Reuse persisted task semantics when structured context says this turn continues it."""
        if (
            state is None
            or not is_current_active_task_status(state.status)
            or not _continues_existing_task(task_context_decision)
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
        task_context_decision: TaskContextDecision | None = None,
    ) -> StoredWorkState | None:
        """Create a new persisted state when a concrete task begins."""
        if work_plan is None:
            if existing_state is not None and task_intent.needs_clarification and task_intent.long_running:
                return existing_state
            return None
        if self._should_resume_existing_state(task_intent, work_plan, existing_state):
            return self._resume_existing_state(existing_state, work_plan)
        if self._should_preserve_existing_state(existing_state, task_context_decision):
            return self._resume_existing_state(existing_state, work_plan)
        if existing_state is not None and task_intent.needs_clarification and task_intent.long_running:
            return existing_state

        numbered_steps = _numbered_steps(work_plan.steps)
        now = time.time()
        pending_steps = tuple(step for step in numbered_steps if step != WORK_STEP_NOT_SET)
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
            current_step=numbered_steps[0] if numbered_steps else WORK_STEP_NOT_SET,
            next_step=numbered_steps[1] if len(numbered_steps) > 1 else WORK_STEP_NOT_SET,
            completed_steps=(),
            pending_steps=pending_steps,
            blockers=(),
            verification_targets=_derive_verification_targets(
                work_plan.done_criteria,
                expects_verification=work_plan.expects_verification,
            ),
            resume_hint=_build_resume_hint(
                status="active",
                current_step=numbered_steps[0] if numbered_steps else WORK_STEP_NOT_SET,
                next_step=numbered_steps[1] if len(numbered_steps) > 1 else WORK_STEP_NOT_SET,
                blockers=(),
                next_action=_NEXT_ACTION_CONTINUE_WORK,
            ),
            last_progress_signals=(),
            file_change_count=0,
            touched_paths=(),
            verification_attempted=False,
            verification_passed=False,
            last_next_action=_NEXT_ACTION_CONTINUE_WORK,
            metadata={
                WORK_PROGRESS_METADATA_SOURCE_KEY: WORK_PROGRESS_METADATA_SOURCE,
                "schema_version": 1,
                "harness_profile": work_plan.harness_profile,
                "verification_policy": work_plan.verification_policy,
                "continuation_policy": work_plan.continuation_policy,
            },
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
            pending_steps = tuple(
                step for step in state.steps if step not in state.completed_steps and step != WORK_STEP_NOT_SET
            )
        if not blockers and is_blocking_completion_status(state.status) and state.last_next_action:
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
        delegated_task_updates: tuple[StoredDelegatedTask, ...] = (),
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

        if not delegated_task_updates and delegate_task_id:
            delegated_task_updates = (
                StoredDelegatedTask(
                    task_id=delegate_task_id,
                    prompt_type=delegate_prompt_type,
                    status="unknown",
                    selected=True,
                    updated_at=time.time(),
                ),
            )
        delegated_tasks = _merge_delegated_tasks(
            _delegated_tasks_for_state(current),
            delegated_task_updates,
            clear_selection=is_complete_completion_status(completion_result.status),
        )
        selected_task = selected_delegated_task(delegated_tasks)

        metadata = dict(current.metadata or {})
        metadata.pop("workboard", None)
        metadata = _apply_structured_follow_up_metadata(metadata, completion_result)
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
            delegated_tasks=delegated_tasks,
            active_delegate_task_id=selected_task.task_id if selected_task is not None else None,
            active_delegate_prompt_type=selected_task.prompt_type if selected_task is not None else None,
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
        delegated_tasks = _delegated_tasks_for_state(state)
        selected_task = selected_delegated_task(delegated_tasks)
        if selected_task is not None:
            lines.append(
                f"- Active delegate: {selected_task.prompt_type or 'subagent'} ({selected_task.task_id})"
            )
        elif delegated_tasks:
            lines.append(f"- Delegated tasks tracked: {len(delegated_tasks)}")
        return "\n".join(lines)

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        execution_result: ExecutionResult,
        auto_continue_attempts: int,
        pass_index: int,
        harness_profile: HarnessProfile | None = None,
    ) -> WorkProgressUpdate:
        """Summarize the current pass and choose the next high-level action."""
        signals = self._progress_signals(execution_result)
        continuation_budget = self.continuation_budget(task_intent, harness_profile=harness_profile)
        if _completion_gap_should_get_one_retry(completion_result):
            continuation_budget = max(continuation_budget, self.default_continuation_budget)
        if requires_evidence_follow_up(completion_result.status) or completion_result.verification_required or completion_result.review_required:
            continuation_budget = max(continuation_budget, self.long_running_continuation_budget)
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

    def continuation_budget(self, task_intent: TaskIntent, harness_profile: HarnessProfile | None = None) -> int:
        profile_name = normalize_profile_name(harness_profile.name if harness_profile is not None else "")
        if is_chat_profile_name(profile_name):
            return 0
        if is_coding_profile_name(profile_name) or is_research_profile_name(profile_name):
            return self.long_running_continuation_budget
        if is_media_profile_name(profile_name) or is_ops_profile_name(profile_name):
            return self.default_continuation_budget
        if task_intent.long_running or task_intent.expects_code_change or task_intent.expects_verification:
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
        if not is_current_active_task_status(existing_state.status):
            return False
        if not existing_state.objective.strip():
            return False
        if task_intent.needs_clarification:
            return True
        return (
            existing_state.objective.strip().lower() == work_plan.objective.strip().lower()
            and existing_state.kind == work_plan.kind
        )

    @staticmethod
    def _should_preserve_existing_state(
        existing_state: StoredWorkState | None,
        task_context_decision: TaskContextDecision | None,
    ) -> bool:
        if existing_state is None:
            return False
        if not is_current_active_task_status(existing_state.status):
            return False
        if task_context_decision is None:
            return True
        if (
            task_context_decision.should_replace_active_task
            or task_context_decision.continuation_type in PRESERVE_STATE_RESET_CONTINUATION_TYPES
        ):
            return False
        return True

    def _resume_existing_state(
        self,
        existing_state: StoredWorkState,
        work_plan: WorkPlan,
    ) -> StoredWorkState:
        steps = tuple(existing_state.steps) or _numbered_steps(work_plan.steps)
        delegated_tasks = _delegated_tasks_for_state(existing_state)
        selected_task = selected_delegated_task(delegated_tasks)
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
            last_next_action=existing_state.last_next_action or _NEXT_ACTION_CONTINUE_WORK,
            delegated_tasks=delegated_tasks,
            active_delegate_task_id=selected_task.task_id if selected_task is not None else None,
            active_delegate_prompt_type=selected_task.prompt_type if selected_task is not None else None,
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
        pending_steps = [step for step in steps if step not in completed_steps and step != WORK_STEP_NOT_SET]
        follow_up_step = _follow_up_pending_step(completion_result, progress.next_action)
        if follow_up_step and follow_up_step not in pending_steps:
            pending_steps.insert(0, follow_up_step)
        if current_step != WORK_STEP_NOT_SET and current_step not in completed_steps and current_step not in pending_steps:
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
                completion_result=completion_result,
            ),
            last_progress_signals=progress.progress_signals,
        )

    @staticmethod
    def _progress_signals(execution_result: ExecutionResult) -> tuple[str, ...]:
        signals: list[str] = []
        if execution_result.executed_tool_calls > 0:
            signals.append(_PROGRESS_SIGNAL_TOOL_CALLS)
        if execution_result.file_change_count > 0:
            signals.append(_PROGRESS_SIGNAL_FILE_CHANGES)
        if execution_result.verification_attempted:
            signals.append(_PROGRESS_SIGNAL_VERIFICATION_ATTEMPTED)
        if execution_result.verification_passed:
            signals.append(_PROGRESS_SIGNAL_VERIFICATION_PASSED)
        if execution_result.context_compactions > 0:
            signals.append(_PROGRESS_SIGNAL_CONTEXT_COMPACTION)
        if is_max_tool_iterations_stop_reason(execution_result.stop_reason):
            signals.append(MAX_TOOL_ITERATIONS_STOP_REASON)
        if execution_result.had_tool_error:
            signals.append(_PROGRESS_SIGNAL_TOOL_ERROR)
        return tuple(signals)

    @staticmethod
    def _status(completion_result: CompletionGateResult) -> str:
        if is_terminal_completion_status(completion_result.status):
            return completion_result.status
        if needs_verification_completion_status(completion_result.status):
            return _WORK_PROGRESS_VERIFYING_STATUS
        if needs_review_completion_status(completion_result.status):
            return _WORK_PROGRESS_REVIEWING_STATUS
        return _WORK_PROGRESS_WORKING_STATUS

    @staticmethod
    def _next_action(
        completion_result: CompletionGateResult,
        *,
        has_progress: bool,
        attempts: int,
        budget: int,
    ) -> str:
        if is_complete_completion_status(completion_result.status):
            return _NEXT_ACTION_FINALIZE
        if is_blocking_completion_status(completion_result.status):
            return completion_result.status
        if attempts >= budget:
            return _NEXT_ACTION_STOP_BUDGET_EXHAUSTED
        if attempts > 0 and not has_progress:
            return _NEXT_ACTION_STOP_NO_PROGRESS
        if needs_verification_completion_status(completion_result.status):
            return _NEXT_ACTION_CONTINUE_VERIFICATION
        if needs_review_completion_status(completion_result.status):
            return (
                _NEXT_ACTION_ADDRESS_REVIEW_FINDINGS
                if completion_result.review_attempted
                else _NEXT_ACTION_COLLECT_REVIEW_EVIDENCE
            )
        return _NEXT_ACTION_CONTINUE_WORK


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


def _apply_structured_follow_up_metadata(metadata: dict[str, Any], completion_result: CompletionGateResult) -> dict[str, Any]:
    next_metadata = dict(metadata or {})
    for key in (
        "follow_up_workflow",
        "follow_up_step_id",
        "follow_up_step_label",
        "follow_up_prompt_type",
        "verification_action",
        "verification_path",
        "verification_pytest_args",
        "active_task_detail",
    ):
        next_metadata.pop(key, None)
    if completion_result.follow_up_workflow:
        next_metadata["follow_up_workflow"] = completion_result.follow_up_workflow
    if completion_result.follow_up_step_id:
        next_metadata["follow_up_step_id"] = completion_result.follow_up_step_id
    if completion_result.follow_up_step_label:
        next_metadata["follow_up_step_label"] = completion_result.follow_up_step_label
    if completion_result.follow_up_prompt_type:
        next_metadata["follow_up_prompt_type"] = completion_result.follow_up_prompt_type
    if completion_result.verification_action:
        next_metadata["verification_action"] = completion_result.verification_action
    if completion_result.verification_path:
        next_metadata["verification_path"] = completion_result.verification_path
    if completion_result.verification_pytest_args:
        next_metadata["verification_pytest_args"] = list(completion_result.verification_pytest_args)
    if completion_result.active_task_detail:
        next_metadata["active_task_detail"] = completion_result.active_task_detail
    return next_metadata


def _derive_verification_targets(
    _done_criteria: tuple[str, ...],
    *,
    expects_verification: bool,
) -> tuple[str, ...]:
    if not expects_verification:
        return ()
    return (_DEFAULT_VERIFICATION_TARGET,)


def _profile_requires_code_change(harness_profile: HarnessProfile) -> bool:
    required_tool_groups = set(harness_profile.required_tool_groups)
    required_evidence = set(harness_profile.required_evidence)
    return (
        harness_profile.task_type in {WORKSPACE_CHANGE_TASK_TYPE, CODE_CHANGE_TASK_TYPE}
        or WORKSPACE_WRITE_TOOL_GROUP in required_tool_groups
        or FILE_CHANGE_REQUIREMENT_KIND in required_evidence
    )


def _profile_requires_verification(harness_profile: HarnessProfile) -> bool:
    required_tool_groups = set(harness_profile.required_tool_groups)
    required_evidence = set(harness_profile.required_evidence)
    return VERIFICATION_TOOL_GROUP in required_tool_groups or VERIFICATION_REQUIREMENT_KIND in required_evidence


def _intent_supports_default_work_plan(task_intent: TaskIntent) -> bool:
    return intent_supports_default_work_plan(task_intent)


def _derive_blockers(completion_result: CompletionGateResult) -> tuple[str, ...]:
    if is_blocking_completion_status(completion_result.status):
        detail = completion_result.active_task_detail or completion_result.reason
        if detail:
            return (detail,)
    return ()


def _follow_up_pending_step(completion_result: CompletionGateResult, next_action: str) -> str:
    detail = str(completion_result.active_task_detail or "").strip()
    if not detail:
        return ""
    if is_incomplete_completion_status(completion_result.status):
        return detail
    if is_review_follow_up_next_action(next_action):
        return detail
    return ""


def _build_resume_hint(
    *,
    status: str,
    current_step: str,
    next_step: str,
    blockers: tuple[str, ...],
    next_action: str,
    completion_result: CompletionGateResult | None = None,
) -> str:
    if status == _WORK_STATE_DONE_STATUS:
        return TASK_DONE_RESUME_HINT
    if blockers:
        return f"Resolve blocker first: {blockers[0]}"
    workflow = str(getattr(completion_result, "follow_up_workflow", "") or "").strip()
    step_label = str(getattr(completion_result, "follow_up_step_label", "") or getattr(completion_result, "follow_up_step_id", "") or "").strip()
    prompt_type = str(getattr(completion_result, "follow_up_prompt_type", "") or "").strip()
    verification_action = str(getattr(completion_result, "verification_action", "") or "").strip()
    verification_path = str(getattr(completion_result, "verification_path", "") or "").strip()
    return build_policy_resume_hint(
        status=status,
        current_step=current_step,
        next_step=next_step,
        blockers=blockers,
        next_action=next_action,
        workflow=workflow,
        step_label=step_label,
        prompt_type=prompt_type,
        verification_action=verification_action,
        verification_path=verification_path,
        done_status=_WORK_STATE_DONE_STATUS,
        unset_step=WORK_STEP_NOT_SET,
    )


def _map_state_status(completion_result: CompletionGateResult, progress: WorkProgressUpdate) -> str:
    if is_complete_completion_status(completion_result.status):
        return _WORK_STATE_DONE_STATUS
    if is_blocking_completion_status(completion_result.status):
        return completion_result.status
    return _WORK_STATE_ACTIVE_STATUS


def _completion_gap_should_get_one_retry(completion_result: CompletionGateResult) -> bool:
    if not is_incomplete_completion_status(completion_result.status):
        return False
    if completion_result.progress_only_response:
        return True
    return bool(completion_result.missing_evidence)


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
    if is_complete_completion_status(progress.completion_status):
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
    if is_complete_completion_status(progress.completion_status):
        return WORK_STEP_NOT_SET, WORK_STEP_NOT_SET
    if is_blocking_completion_status(progress.completion_status):
        current = steps[-1] if steps else WORK_STEP_NOT_SET
        return current, WORK_STEP_NOT_SET
    if is_verification_next_action(progress.next_action):
        return _verification_step(steps, expects_code_change=expects_code_change), WORK_STEP_NOT_SET
    if is_review_phase_next_action(progress.next_action):
        return (steps[-1] if steps else WORK_STEP_NOT_SET), WORK_STEP_NOT_SET
    if expects_code_change and progress.file_change_count <= 0 and len(steps) >= 2:
        next_step = steps[2] if len(steps) > 2 else WORK_STEP_NOT_SET
        return steps[1], next_step
    if expects_verification and steps:
        return _verification_step(steps, expects_code_change=expects_code_change), WORK_STEP_NOT_SET
    if is_continue_work_next_action(progress.next_action) and steps:
        current = steps[-1] if progress.file_change_count > 0 else (steps[1] if len(steps) > 1 else steps[0])
        next_step = WORK_STEP_NOT_SET
        if progress.file_change_count <= 0 and len(steps) > 2:
            next_step = steps[2]
        return current, next_step
    return WORK_STEP_NOT_SET, WORK_STEP_NOT_SET


def _verification_step(steps: tuple[str, ...], *, expects_code_change: bool) -> str:
    if not steps:
        return WORK_STEP_NOT_SET
    if expects_code_change and len(steps) >= 3:
        return steps[2]
    if len(steps) >= 2:
        return steps[1]
    return steps[-1] if steps else WORK_STEP_NOT_SET

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
        if status == WAITING_USER_ACTIVE_TASK_STATUS:
            store.update_fields(
                status=WAITING_USER_ACTIVE_TASK_STATUS,
                open_questions=[detail or WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION],
                force=True,
            )
        elif status == BLOCKED_ACTIVE_TASK_STATUS:
            store.update_fields(
                status=BLOCKED_ACTIVE_TASK_STATUS,
                open_questions=[detail or BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION],
                force=True,
            )
        elif status == DONE_ACTIVE_TASK_STATUS:
            store.update_fields(status=DONE_ACTIVE_TASK_STATUS, open_questions=clear_open_questions(), force=True)
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
            if is_verification_work_progress(progress):
                current_step = self.messages.progress_verify_current_step
                next_step = WORK_STEP_NOT_SET
            elif is_continue_work_progress(progress):
                current_step = self.messages.progress_continue_current_step
                next_step = (
                    self.messages.progress_verify_current_step if progress.verification_required else WORK_STEP_NOT_SET
                )
            else:
                return
        if current_step is None or next_step is None:
            return

        workboard = WorkProgressService.extract_workboard(state)
        open_questions: list[str] | None = None
        if workboard.blockers:
            open_questions = list(workboard.blockers)
        elif state is not None and clears_active_task_open_questions(state.status):
            open_questions = clear_open_questions()
        elif is_blocking_completion_status(progress.status):
            open_questions = [progress.completion_reason]

        store.update_fields(
            status=state.status
            if state is not None and is_current_or_done_active_task_status(state.status)
            else ACTIVE_ACTIVE_TASK_STATUS,
            current_step=current_step,
            next_step=next_step,
            open_questions=open_questions,
            force=True,
        )
        store.append_event(
            WORK_PROGRESS_ACTIVE_TASK_EVENT_TYPE,
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
                store.update_fields(status=WAITING_USER_ACTIVE_TASK_STATUS, open_questions=[question], force=True)
                await self._mark_processed(session_id, store)
                store.append_event(
                    TASK_BOUNDARY_CONFIRMATION_EVENT,
                    "immediate",
                    details={
                        "message": re.sub(r"\s+", " ", current_message).strip()[:120],
                        "pending_request": _compact_for_prompt(current_message),
                        "confidence": task_context_decision.confidence if task_context_decision else 0.0,
                    },
                )
                return
            if _decision_continues_current_task(task_context_decision):
                if current_status == WAITING_USER_ACTIVE_TASK_STATUS and store.read_pending_boundary_request():
                    store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, open_questions=clear_open_questions(), force=True)
                    await self._mark_processed(session_id, store)
                    store.append_event(
                        TASK_BOUNDARY_CONFIRMATION_RESOLVED_EVENT,
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
        logger.info("[{}] {} | replace={}", session_id, ACTIVE_TASK_SEEDED_EVENT, replacing)

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
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, open_questions=clear_open_questions(), force=True)
        await self._mark_processed(session_id, store)
        store.append_event("activate", "user")
        return f"# Active Task\n\n{rendered}"

    async def reopen(self, session_id: str) -> str | None:
        """Reopen a terminal ACTIVE_TASK and resume it as active."""
        store = self.get_store(session_id)
        if store is None:
            return None
        if not is_terminal_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("reopen", "user")
        return f"# Active Task\n\n{rendered}"

    async def block(self, session_id: str, reason: str) -> str | None:
        """Mark the current ACTIVE_TASK as blocked with one explicit reason."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=BLOCKED_ACTIVE_TASK_STATUS, open_questions=[reason], force=True)
        await self._mark_processed(session_id, store)
        store.append_event("block", "user", details={"reason": reason})
        return f"# Active Task\n\n{rendered}"

    async def wait_on(self, session_id: str, question: str) -> str | None:
        """Mark the current ACTIVE_TASK as waiting for user input."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=WAITING_USER_ACTIVE_TASK_STATUS, open_questions=[question], force=True)
        await self._mark_processed(session_id, store)
        store.append_event("wait", "user", details={"question": question})
        return f"# Active Task\n\n{rendered}"

    async def set_current_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the current step for the active task."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(status=ACTIVE_ACTIVE_TASK_STATUS, current_step=step_text, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("set_current_step", "user", details={"current_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def set_next_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the planned next step for the active task."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        rendered = store.update_fields(next_step=step_text, force=True)
        await self._mark_processed(session_id, store)
        store.append_event("set_next_step", "user", details={"next_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def advance(self, session_id: str) -> str | None:
        """Promote the next step into the current step and mark the previous step complete."""
        store = self.get_store(session_id)
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        next_step = _extract_task_field(current_block, "Next step")
        if next_step == WORK_STEP_NOT_SET:
            return None
        rendered = store.update_fields(
            status=ACTIVE_ACTIVE_TASK_STATUS,
            current_step=next_step,
            next_step=WORK_STEP_NOT_SET,
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
        if store is None or is_inactive_active_task_status(store.read_status()):
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
        if store is None or is_inactive_active_task_status(store.read_status()):
            return None
        open_questions = clear_open_questions() if clears_active_task_open_questions(status) else None
        store.update_fields(status=status, open_questions=open_questions, force=True)
        if is_terminal_active_task_status(status):
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
        or is_current_task_continuation_type(decision.continuation_type)
        or decision.is_follow_up
    )


def _decision_replaces_current_task(decision: TaskContextDecision | None) -> bool:
    return bool(
        decision
        and decision.should_replace_active_task
        and is_current_task_replacement_type(decision.continuation_type)
    )


def _decision_needs_boundary_confirmation(decision: TaskContextDecision | None) -> bool:
    return bool(decision and is_ambiguous_boundary_continuation_type(decision.continuation_type))


def _decision_controls_task_seed(decision: TaskContextDecision | None) -> bool:
    return bool(decision and decision.method == "llm")


def _boundary_confirmation_question(current_task: str, current_message: str) -> str:
    current_goal = _compact_for_prompt(_extract_task_field(current_task, "Goal"))
    new_request = _compact_for_prompt(current_message) or "the new request"
    if current_goal and current_goal.lower() != WORK_STEP_NOT_SET:
        return (
            f"Reply `{BOUNDARY_SWITCH_REPLY_COMMAND}` to replace the active task ({current_goal}) "
            f"with the new request ({new_request}), or `{BOUNDARY_CONTINUE_REPLY_COMMAND}` to keep the active task."
        )
    return (
        f"Reply `{BOUNDARY_SWITCH_REPLY_COMMAND}` to replace it with the new request ({new_request}), "
        f"or `{BOUNDARY_CONTINUE_REPLY_COMMAND}` to keep the active task."
    )


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
