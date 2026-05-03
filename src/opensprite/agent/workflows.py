"""Fixed orchestration workflows built on delegated subagents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..utils.log import logger
from .run_state import RunCancelledError
from .subagents import SubagentTaskOutcome


@dataclass(frozen=True)
class WorkflowStepSpec:
    """One fixed child-step inside a workflow."""

    step_id: str
    label: str
    prompt_type: str
    task_builder: Callable[[str, list[SubagentTaskOutcome]], str]
    resume_task_builder: Callable[[str], str] | None = None


@dataclass(frozen=True)
class WorkflowSpec:
    """One supported multi-step orchestration workflow."""

    workflow_id: str
    description: str
    steps: tuple[WorkflowStepSpec, ...]


def _result_summary(outcome: SubagentTaskOutcome) -> str:
    if outcome.summary:
        return outcome.summary
    if outcome.error:
        return outcome.error
    return outcome.content


def _format_review_finding(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    path = str(item.get("path") or "").strip()
    fix = str(item.get("fix") or "").strip()
    why = str(item.get("why") or "").strip()
    subject = f"{path}: {title}" if path and title else title or path
    if fix:
        return f"{subject}: {fix}" if subject else fix
    if why:
        return f"{subject}: {why}" if subject else why
    return subject


def _first_structured_review_finding(structured_output: dict[str, Any] | None) -> str:
    sections = structured_output.get("sections") if isinstance(structured_output, dict) else None
    if not isinstance(sections, list):
        return ""
    for section in sections:
        if not isinstance(section, dict):
            continue
        items = section.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                detail = _format_review_finding(item)
                if detail:
                    return detail
            elif isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _workflow_progress_fields(
    steps: tuple[WorkflowStepSpec, ...],
    outcomes: list[SubagentTaskOutcome],
    *,
    status: str,
    start_index: int = 0,
) -> dict[str, Any]:
    completed_prefix = start_index
    for outcome in outcomes[: len(steps)]:
        if outcome.status != "completed":
            break
        completed_prefix += 1

    payload: dict[str, Any] = {}
    if completed_prefix > 0:
        last_completed = steps[completed_prefix - 1]
        payload.update(
            {
                "last_completed_step_id": last_completed.step_id,
                "last_completed_step_label": last_completed.label,
                "last_completed_prompt_type": last_completed.prompt_type,
            }
        )
    if status != "completed" and completed_prefix < len(steps):
        next_step = steps[completed_prefix]
        payload.update(
            {
                "next_step_id": next_step.step_id,
                "next_step_label": next_step.label,
                "next_step_prompt_type": next_step.prompt_type,
            }
        )
    return payload


def _resolve_start_index(spec: WorkflowSpec, start_step: str | None) -> tuple[int, WorkflowStepSpec | None, str | None]:
    normalized = str(start_step or "").strip()
    if not normalized:
        return 0, None, None
    for index, step in enumerate(spec.steps):
        if step.step_id == normalized:
            return index, step, None
    available = ", ".join(step.step_id for step in spec.steps)
    return 0, None, f"Error: unknown start_step '{normalized}' for workflow '{spec.workflow_id}'. Available: {available}"


def _build_step_task(
    step: WorkflowStepSpec,
    *,
    task_text: str,
    outcomes: list[SubagentTaskOutcome],
    resumed: bool,
) -> str:
    if resumed and not outcomes:
        builder = step.resume_task_builder
        if builder is None:
            return step.task_builder(task_text, outcomes).strip()
        return builder(task_text).strip()
    return step.task_builder(task_text, outcomes).strip()


def _implement_review_steps() -> tuple[WorkflowStepSpec, ...]:
    return (
        WorkflowStepSpec(
            step_id="implement",
            label="Implement",
            prompt_type="implementer",
            task_builder=lambda task, _: task,
            resume_task_builder=lambda task: task,
        ),
        WorkflowStepSpec(
            step_id="review",
            label="Code review",
            prompt_type="code-reviewer",
            task_builder=lambda task, results: (
                "Review the current workspace changes for correctness, regressions, and missing tests. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Implementation result:\n{_result_summary(results[0])}"
            ),
            resume_task_builder=lambda task: (
                "Resume the code review step for the current workspace changes. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
    )


def _research_outline_steps() -> tuple[WorkflowStepSpec, ...]:
    return (
        WorkflowStepSpec(
            step_id="research",
            label="Research",
            prompt_type="researcher",
            task_builder=lambda task, _: task,
            resume_task_builder=lambda task: task,
        ),
        WorkflowStepSpec(
            step_id="outline",
            label="Outline",
            prompt_type="outliner",
            task_builder=lambda task, results: (
                "Create a clear outline based on the research summary below.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Research summary:\n{results[0].content}"
            ),
            resume_task_builder=lambda task: (
                "Resume the outline step for the original objective below. "
                "Use any already gathered research context available in the current session or workspace, "
                "and clearly state missing inputs if the research context is insufficient.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
    )


def _bugfix_test_review_steps() -> tuple[WorkflowStepSpec, ...]:
    return (
        WorkflowStepSpec(
            step_id="bugfix",
            label="Bug fix",
            prompt_type="bug-fixer",
            task_builder=lambda task, _: task,
            resume_task_builder=lambda task: task,
        ),
        WorkflowStepSpec(
            step_id="tests",
            label="Tests",
            prompt_type="test-writer",
            task_builder=lambda task, results: (
                "Add the minimal effective tests for the bug fix below. Inspect the current workspace changes first.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Bug-fix result:\n{_result_summary(results[0])}"
            ),
            resume_task_builder=lambda task: (
                "Resume the tests step for the current workspace changes related to the bug fix below. "
                "Inspect the actual files first and add the minimal effective tests.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
        WorkflowStepSpec(
            step_id="review",
            label="Code review",
            prompt_type="code-reviewer",
            task_builder=lambda task, results: (
                "Review the current workspace changes after the bug fix and test additions. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}\n\n"
                f"Bug-fix result:\n{_result_summary(results[0])}\n\n"
                f"Test result:\n{_result_summary(results[1])}"
            ),
            resume_task_builder=lambda task: (
                "Resume the code review step for the current workspace changes after the bug fix and test additions. "
                "Inspect the actual files and report findings first.\n\n"
                f"Original objective:\n{task}"
            ),
        ),
    )


WORKFLOW_SPECS: dict[str, WorkflowSpec] = {
    "implement_then_review": WorkflowSpec(
        workflow_id="implement_then_review",
        description="Run implementer first, then inspect the workspace with code-reviewer.",
        steps=_implement_review_steps(),
    ),
    "research_then_outline": WorkflowSpec(
        workflow_id="research_then_outline",
        description="Gather research context first, then turn it into an outline.",
        steps=_research_outline_steps(),
    ),
    "bugfix_then_test_then_review": WorkflowSpec(
        workflow_id="bugfix_then_test_then_review",
        description="Fix the bug, add focused tests, then run a code review pass.",
        steps=_bugfix_test_review_steps(),
    ),
}


class SubagentWorkflowService:
    """Runs fixed orchestration workflows on top of delegated subagents."""

    def __init__(
        self,
        *,
        current_session_id_getter: Callable[[], str | None],
        current_run_id_getter: Callable[[], str | None],
        current_channel_getter: Callable[[], str | None],
        current_external_chat_id_getter: Callable[[], str | None],
        run_subagent_task: Callable[[str, str], Awaitable[SubagentTaskOutcome]],
        emit_run_event: Callable[..., Awaitable[None]],
        format_log_preview: Callable[..., str],
        record_workflow_outcome: Callable[[str | None, dict[str, Any]], None],
    ):
        self._current_session_id_getter = current_session_id_getter
        self._current_run_id_getter = current_run_id_getter
        self._current_channel_getter = current_channel_getter
        self._current_external_chat_id_getter = current_external_chat_id_getter
        self._run_subagent_task = run_subagent_task
        self._emit_run_event = emit_run_event
        self._format_log_preview = format_log_preview
        self._record_workflow_outcome = record_workflow_outcome

    @staticmethod
    def catalog() -> dict[str, str]:
        """Return workflow ids and user-facing descriptions."""
        return {workflow_id: spec.description for workflow_id, spec in WORKFLOW_SPECS.items()}

    @staticmethod
    def _new_workflow_run_id() -> str:
        return f"workflow_{uuid4().hex[:12]}"

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        session_id = self._current_session_id_getter()
        run_id = self._current_run_id_getter()
        if session_id is None or run_id is None:
            return
        await self._emit_run_event(
            session_id,
            run_id,
            event_type,
            payload,
            channel=self._current_channel_getter(),
            external_chat_id=self._current_external_chat_id_getter(),
        )

    @staticmethod
    def _step_payload(
        *,
        workflow_run_id: str,
        workflow_id: str,
        spec: WorkflowStepSpec,
        step_index: int,
        total_steps: int,
        outcome: SubagentTaskOutcome | None = None,
        task_preview: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        payload = {
            "workflow_run_id": workflow_run_id,
            "workflow": workflow_id,
            "step_id": spec.step_id,
            "label": spec.label,
            "prompt_type": spec.prompt_type,
            "step_index": step_index,
            "total_steps": total_steps,
            "task_preview": task_preview,
        }
        if outcome is not None:
            payload.update(
                {
                    "status": outcome.status,
                    "task_id": outcome.task_id,
                    "child_session_id": outcome.child_session_id,
                    "child_run_id": outcome.child_run_id,
                    "summary": outcome.summary,
                    "error": outcome.error,
                }
            )
            if outcome.structured_output is not None:
                payload["structured_output"] = {
                    "status": outcome.structured_output.get("status"),
                    "summary": outcome.structured_output.get("summary"),
                    "finding_count": outcome.structured_output.get("finding_count", 0),
                    "question_count": outcome.structured_output.get("question_count", 0),
                    "residual_risk_count": outcome.structured_output.get("residual_risk_count", 0),
                }
        if error:
            payload["error"] = error
        return payload

    @staticmethod
    def _workflow_payload(
        *,
        workflow_run_id: str,
        workflow_id: str,
        task_preview: str,
        steps: tuple[WorkflowStepSpec, ...],
        outcomes: list[SubagentTaskOutcome],
        status: str,
        start_index: int = 0,
        error: str = "",
    ) -> dict[str, Any]:
        completed_steps = start_index + sum(1 for outcome in outcomes if outcome.status == "completed")
        failed_steps = sum(1 for outcome in outcomes if outcome.status in {"failed", "error"})
        summary = (
            f"Completed {completed_steps}/{len(steps)} workflow step(s)."
            if status == "completed"
            else f"Workflow stopped after {completed_steps}/{len(steps)} completed step(s)."
        )
        payload = {
            "workflow_run_id": workflow_run_id,
            "workflow": workflow_id,
            "status": status,
            "task_preview": task_preview,
            "total_steps": len(steps),
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "summary": summary,
            "steps": [
                {
                    "step_id": spec.step_id,
                    "label": spec.label,
                    "prompt_type": spec.prompt_type,
                    "status": outcome.status,
                    "task_id": outcome.task_id,
                    "child_session_id": outcome.child_session_id,
                    "child_run_id": outcome.child_run_id,
                    "summary": outcome.summary,
                    "error": outcome.error,
                }
                for spec, outcome in zip(steps[start_index:], outcomes)
            ],
            **_workflow_progress_fields(steps, outcomes, status=status, start_index=start_index),
        }
        if start_index > 0:
            start_step = steps[start_index]
            payload.update(
                {
                    "resumed": True,
                    "start_step_id": start_step.step_id,
                    "start_step_label": start_step.label,
                }
            )
        if error:
            payload["error"] = error
        return payload

    @staticmethod
    def _format_result(workflow_id: str, outcomes: list[SubagentTaskOutcome], *, status: str, start_index: int = 0) -> str:
        lines = [
            f"Workflow: {workflow_id}",
            f"Status: {status}",
        ]
        if start_index > 0:
            lines.append(f"Resumed from step: {start_index + 1}")
        for index, outcome in enumerate(outcomes, start=start_index + 1):
            lines.extend(
                [
                    "",
                    f"[{index}] {outcome.prompt_type} | {outcome.status}",
                    f"Task ID: {outcome.task_id}",
                    f"Run ID: {outcome.child_run_id}",
                ]
            )
            if outcome.summary:
                lines.append(f"Summary: {outcome.summary}")
            if outcome.error:
                lines.append(f"Error: {outcome.error}")
            if outcome.content:
                lines.extend(["Result:", outcome.content])
        return "\n".join(lines)

    @staticmethod
    def _review_outcome(outcomes: list[SubagentTaskOutcome]) -> dict[str, Any]:
        review_outcomes = [
            outcome
            for outcome in outcomes
            if outcome.prompt_type in {"code-reviewer", "security-reviewer", "async-concurrency-reviewer"}
        ]
        finding_count = sum(
            int((outcome.structured_output or {}).get("finding_count") or 0)
            for outcome in review_outcomes
        )
        attempted = any(outcome.status == "completed" for outcome in review_outcomes)
        passed = False
        summary = ""
        first_finding = ""
        for outcome in review_outcomes:
            if outcome.summary and not summary:
                summary = outcome.summary
            if not first_finding:
                first_finding = _first_structured_review_finding(outcome.structured_output)
            if outcome.status != "completed":
                continue
            structured = outcome.structured_output or {}
            if str(structured.get("status") or "") == "ok" and int(structured.get("finding_count") or 0) == 0:
                passed = True
                continue
            lowered = (outcome.summary or outcome.content or "").lower()
            if "no major findings" in lowered or "沒有重大發現" in lowered:
                passed = True
        return {
            "attempted": attempted,
            "passed": attempted and passed and finding_count == 0,
            "finding_count": finding_count,
            "summary": summary,
            "first_finding": first_finding,
        }

    @staticmethod
    def _verification_outcome(outcomes: list[SubagentTaskOutcome]) -> dict[str, Any]:
        attempted = any(outcome.verification_attempted for outcome in outcomes)
        passed = any(outcome.verification_passed for outcome in outcomes)
        return {
            "attempted": attempted,
            "passed": passed,
        }

    def _build_workflow_outcome(
        self,
        *,
        workflow_run_id: str,
        spec: WorkflowSpec,
        task_preview: str,
        outcomes: list[SubagentTaskOutcome],
        status: str,
        start_index: int = 0,
        error: str = "",
    ) -> dict[str, Any]:
        review = self._review_outcome(outcomes)
        verification = self._verification_outcome(outcomes)
        return {
            "workflow_run_id": workflow_run_id,
            "workflow": spec.workflow_id,
            "status": status,
            "task_preview": task_preview,
            "total_steps": len(spec.steps),
            "completed_steps": start_index + sum(1 for outcome in outcomes if outcome.status == "completed"),
            "failed_steps": sum(1 for outcome in outcomes if outcome.status in {"failed", "error", "cancelled"}),
            "summary": (
                f"Completed {start_index + sum(1 for outcome in outcomes if outcome.status == 'completed')}/{len(spec.steps)} workflow step(s)."
                if status == "completed"
                else f"Workflow stopped after {start_index + sum(1 for outcome in outcomes if outcome.status == 'completed')}/{len(spec.steps)} completed step(s)."
            ),
            "review_attempted": review["attempted"],
            "review_passed": review["passed"],
            "review_finding_count": review["finding_count"],
            "review_summary": review["summary"],
            "review_first_finding": review["first_finding"],
            "verification_attempted": verification["attempted"],
            "verification_passed": verification["passed"],
            **_workflow_progress_fields(spec.steps, outcomes, status=status, start_index=start_index),
            **(
                {
                    "resumed": True,
                    "start_step_id": spec.steps[start_index].step_id,
                    "start_step_label": spec.steps[start_index].label,
                }
                if start_index > 0
                else {}
            ),
            **({"error": error} if error else {}),
        }

    async def run(self, workflow_id: str, task: str) -> str:
        workflow_key = str(workflow_id or "").strip()
        spec = WORKFLOW_SPECS.get(workflow_key)
        if spec is None:
            available = ", ".join(sorted(WORKFLOW_SPECS))
            return f"Error: unknown workflow '{workflow_key}'. Available: {available}"

        task_text = str(task or "").strip()
        if not task_text:
            return "Error: workflow task must be a non-empty string."

        return await self.run_from_step(workflow_key, task_text)

    async def run_from_step(self, workflow_id: str, task: str, start_step: str | None = None) -> str:
        workflow_key = str(workflow_id or "").strip()
        spec = WORKFLOW_SPECS.get(workflow_key)
        if spec is None:
            available = ", ".join(sorted(WORKFLOW_SPECS))
            return f"Error: unknown workflow '{workflow_key}'. Available: {available}"

        task_text = str(task or "").strip()
        if not task_text:
            return "Error: workflow task must be a non-empty string."

        start_index, start_spec, start_error = _resolve_start_index(spec, start_step)
        if start_error:
            return start_error

        workflow_run_id = self._new_workflow_run_id()
        task_preview = self._format_log_preview(task_text, max_chars=240)
        start_step_summary = (
            f"Resumed workflow {spec.workflow_id} from step {start_spec.step_id} ({start_spec.label})."
            if start_spec is not None
            else f"Started workflow {spec.workflow_id} with {len(spec.steps)} step(s)."
        )
        await self._emit_event(
            "workflow.started",
            {
                "workflow_run_id": workflow_run_id,
                "workflow": spec.workflow_id,
                "status": "running",
                "task_preview": task_preview,
                "total_steps": len(spec.steps),
                "summary": start_step_summary,
                **(
                    {
                        "resumed": True,
                        "start_step_id": start_spec.step_id,
                        "start_step_label": start_spec.label,
                        "start_step_prompt_type": start_spec.prompt_type,
                    }
                    if start_spec is not None
                    else {}
                ),
            },
        )

        outcomes: list[SubagentTaskOutcome] = []
        for index, step in enumerate(spec.steps[start_index:], start=start_index + 1):
            step_task = _build_step_task(
                step,
                task_text=task_text,
                outcomes=outcomes,
                resumed=start_spec is not None and index == start_index + 1,
            )
            step_preview = self._format_log_preview(step_task, max_chars=240)
            await self._emit_event(
                "workflow.step.started",
                self._step_payload(
                    workflow_run_id=workflow_run_id,
                    workflow_id=spec.workflow_id,
                    spec=step,
                    step_index=index,
                    total_steps=len(spec.steps),
                    task_preview=step_preview,
                ),
            )
            try:
                outcome = await self._run_subagent_task(step_task, step.prompt_type)
            except RunCancelledError:
                self._record_workflow_outcome(
                    self._current_run_id_getter(),
                    self._build_workflow_outcome(
                        workflow_run_id=workflow_run_id,
                        spec=spec,
                        task_preview=task_preview,
                        outcomes=outcomes,
                        status="cancelled",
                        start_index=start_index,
                        error="cancelled",
                    ),
                )
                await self._emit_event(
                    "workflow.failed",
                    self._workflow_payload(
                        workflow_run_id=workflow_run_id,
                        workflow_id=spec.workflow_id,
                        task_preview=task_preview,
                        steps=spec.steps,
                        outcomes=outcomes,
                        status="cancelled",
                        start_index=start_index,
                        error="cancelled",
                    ),
                )
                raise
            except Exception as exc:  # pragma: no cover - defensive guard
                error_preview = self._format_log_preview(f"{type(exc).__name__}: {exc}", max_chars=240)
                logger.warning("workflow.run.failed | workflow={} step={} error={}", spec.workflow_id, step.step_id, error_preview)
                self._record_workflow_outcome(
                    self._current_run_id_getter(),
                    self._build_workflow_outcome(
                        workflow_run_id=workflow_run_id,
                        spec=spec,
                        task_preview=task_preview,
                        outcomes=outcomes,
                        status="failed",
                        start_index=start_index,
                        error=error_preview,
                    ),
                )
                await self._emit_event(
                    "workflow.step.failed",
                    self._step_payload(
                        workflow_run_id=workflow_run_id,
                        workflow_id=spec.workflow_id,
                        spec=step,
                        step_index=index,
                        total_steps=len(spec.steps),
                        task_preview=step_preview,
                        error=error_preview,
                    ),
                )
                await self._emit_event(
                    "workflow.failed",
                    self._workflow_payload(
                        workflow_run_id=workflow_run_id,
                        workflow_id=spec.workflow_id,
                        task_preview=task_preview,
                        steps=spec.steps,
                        outcomes=outcomes,
                        status="failed",
                        start_index=start_index,
                        error=error_preview,
                    ),
                )
                return f"Error: workflow step '{step.step_id}' failed: {error_preview}"

            outcomes.append(outcome)
            await self._emit_event(
                "workflow.step.completed",
                self._step_payload(
                    workflow_run_id=workflow_run_id,
                    workflow_id=spec.workflow_id,
                    spec=step,
                    step_index=index,
                    total_steps=len(spec.steps),
                    outcome=outcome,
                ),
            )

        await self._emit_event(
            "workflow.completed",
            self._workflow_payload(
                workflow_run_id=workflow_run_id,
                workflow_id=spec.workflow_id,
                task_preview=task_preview,
                steps=spec.steps,
                outcomes=outcomes,
                status="completed",
                start_index=start_index,
            ),
        )
        self._record_workflow_outcome(
            self._current_run_id_getter(),
            self._build_workflow_outcome(
                workflow_run_id=workflow_run_id,
                spec=spec,
                task_preview=task_preview,
                outcomes=outcomes,
                status="completed",
                start_index=start_index,
            ),
        )
        return self._format_result(spec.workflow_id, outcomes, status="completed", start_index=start_index)
