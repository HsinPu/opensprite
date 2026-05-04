"""Bounded autonomous continuation decisions for user turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .completion_gate import CompletionGateResult
from .execution import ExecutionResult
from .task_intent import TaskIntent
from .work_progress import WorkProgressUpdate


_CONTINUABLE_STATUSES = {"incomplete", "needs_verification", "needs_review"}
_TERMINAL_STATUSES = {"blocked", "complete", "waiting_user"}


@dataclass(frozen=True)
class AutoContinueDecision:
    """Decision for whether the current run may perform one more LLM/tool pass."""

    should_continue: bool
    reason: str
    attempt: int
    max_attempts: int
    prompt: str | None = None
    direct_workflow: str | None = None
    direct_start_step: str | None = None
    emit_skipped_event: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe run event payload."""
        payload: dict[str, Any] = {
            "schema_version": 1,
            "reason": self.reason,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "will_continue": self.should_continue,
        }
        if self.prompt:
            payload["prompt_len"] = len(self.prompt)
        if self.direct_workflow:
            payload["direct_workflow"] = self.direct_workflow
        if self.direct_start_step:
            payload["direct_start_step"] = self.direct_start_step
        return payload


class AutoContinueService:
    """Allow at most a small number of safe self-continuations."""

    def __init__(self, *, max_auto_continues: int = 1):
        self.max_auto_continues = max(0, max_auto_continues)

    def decide(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        execution_result: ExecutionResult,
        attempts_used: int,
        previous_response: str,
        work_progress: WorkProgressUpdate | None = None,
    ) -> AutoContinueDecision:
        """Return whether another bounded pass should run."""
        next_attempt = attempts_used + 1
        max_attempts = work_progress.continuation_budget if work_progress is not None else self.max_auto_continues
        if completion_result.status in _TERMINAL_STATUSES:
            return self._skip(
                "completion_gate_terminal_status",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=False,
            )
        if completion_result.status not in _CONTINUABLE_STATUSES:
            return self._skip(
                "completion_gate_status_not_continuable",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=False,
            )
        if completion_result.status == "needs_review" and attempts_used > 0:
            reason = "review_findings_require_follow_up" if completion_result.review_attempted else "review_evidence_still_missing"
            return self._skip(
                reason,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if attempts_used >= max_attempts:
            return self._skip(
                "max_auto_continues_reached",
                attempt=attempts_used,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if attempts_used > 0 and work_progress is not None and not work_progress.has_progress:
            return self._skip(
                "no_progress_during_continuation",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if execution_result.had_tool_error:
            return self._skip(
                "tool_error_requires_blocker_or_user_handoff",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if (
            completion_result.status == "incomplete"
            and execution_result.executed_tool_calls == 0
            and not task_intent.expects_code_change
        ):
            return self._skip(
                "no_tool_progress_after_incomplete_response",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )

        direct_workflow, direct_start_step = self._deterministic_workflow_resume_target(
            completion_result,
            attempts_used=attempts_used,
        )
        return AutoContinueDecision(
            should_continue=True,
            reason=f"completion_gate_{completion_result.status}",
            attempt=next_attempt,
            max_attempts=max_attempts,
            prompt=self.build_prompt(
                task_intent=task_intent,
                completion_result=completion_result,
                previous_response=previous_response,
            ),
            direct_workflow=direct_workflow,
            direct_start_step=direct_start_step,
        )

    def build_prompt(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        previous_response: str,
    ) -> str:
        """Build the synthetic continuation instruction for the next pass."""
        previous = _truncate(previous_response, max_chars=1200) or "(no previous visible response)"
        follow_up_detail = str(completion_result.active_task_detail or "").strip()
        workflow_target = _workflow_follow_up_target(completion_result)
        follow_up_instruction = ""
        if follow_up_detail:
            follow_up_instruction = (
                f"\n- Required follow-up: {follow_up_detail}"
                "\n- Treat the required follow-up as the next concrete step instead of restarting the task broadly."
            )
        workflow_instruction = ""
        if workflow_target:
            workflow_instruction = f"\n- Workflow follow-up target: {workflow_target}"
            if completion_result.follow_up_workflow and completion_result.follow_up_step_id:
                workflow_instruction += (
                    "\n- If the task still fits the workflow, prefer calling "
                    f"`run_workflow(workflow=\"{completion_result.follow_up_workflow}\", task=<original objective>, start_step=\"{completion_result.follow_up_step_id}\")`."
                )
            if completion_result.follow_up_prompt_type:
                workflow_instruction += (
                    f"\n- Prefer a delegated `{completion_result.follow_up_prompt_type}` step or an equivalent focused step "
                    "before rerunning broader workflow work."
                )
            elif completion_result.follow_up_step_label:
                workflow_instruction += (
                    "\n- Prefer resuming this concrete workflow step instead of rerunning already completed workflow steps."
                )
        verification_instruction = ""
        if completion_result.status == "needs_verification":
            verification_instruction = (
                "\n- Verification is required. Use available verification tools or clearly state the blocker "
                "if verification cannot be run."
            )
        review_instruction = ""
        if completion_result.status == "needs_review":
            if completion_result.review_attempted:
                review_instruction = (
                    "\n- Review findings already exist. Address the recorded findings first, "
                    "then rerun delegated review only if needed to confirm the fix."
                )
            else:
                review_instruction = (
                    "\n- Review evidence is required for the recorded code changes. Use delegated review workflows or review-focused subagents, "
                    "then summarize whether the review found issues that still need follow-up."
                )
        incomplete_instruction = ""
        if completion_result.status == "incomplete" and follow_up_detail:
            incomplete_instruction = (
                "\n- The missing work is already identified. Resume from the required follow-up detail below before doing broader new work."
            )

        return (
            "Continue the current task without asking the user unless you are blocked.\n"
            f"- Original objective: {task_intent.objective}\n"
            f"- Completion gate status: {completion_result.status}\n"
            f"- Completion gate reason: {completion_result.reason}"
            f"{verification_instruction}\n"
            f"{review_instruction}\n"
            f"{incomplete_instruction}\n"
            f"{workflow_instruction}\n"
            f"{follow_up_instruction}\n"
            "- If the task is complete, provide the final answer with the evidence or verification result.\n"
            "- If the task cannot proceed, state the blocker clearly.\n\n"
            "Previous assistant response:\n"
            f"{previous}"
        )

    def build_post_workflow_resume_prompt(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        previous_response: str,
        workflow_result: str,
    ) -> str:
        previous = _truncate(previous_response, max_chars=800) or "(no previous visible response)"
        workflow_output = _truncate(workflow_result, max_chars=2000) or "(workflow returned no visible result)"
        workflow_target = _workflow_follow_up_target(completion_result)
        return (
            "The runtime already resumed the workflow follow-up step for you. Continue from that result instead of rerunning the same step unless you find a concrete reason.\n"
            f"- Original objective: {task_intent.objective}\n"
            f"- Prior completion gate status: {completion_result.status}\n"
            f"- Prior completion gate reason: {completion_result.reason}\n"
            f"- Workflow follow-up target: {workflow_target or 'workflow'}\n"
            "- Use the resumed workflow result below to finish the task, summarize the result, or state any remaining blocker clearly.\n\n"
            "Resumed workflow result:\n"
            f"{workflow_output}\n\n"
            "Previous assistant response:\n"
            f"{previous}"
        )

    def _skip(
        self,
        reason: str,
        *,
        attempt: int,
        emit_event: bool,
        max_attempts: int | None = None,
    ) -> AutoContinueDecision:
        return AutoContinueDecision(
            should_continue=False,
            reason=reason,
            attempt=attempt,
            max_attempts=self.max_auto_continues if max_attempts is None else max_attempts,
            emit_skipped_event=emit_event,
        )

    @staticmethod
    def _deterministic_workflow_resume_target(
        completion_result: CompletionGateResult,
        *,
        attempts_used: int,
    ) -> tuple[str | None, str | None]:
        if attempts_used > 0:
            return None, None
        if completion_result.status not in {"incomplete", "needs_review"}:
            return None, None
        workflow = str(completion_result.follow_up_workflow or "").strip()
        start_step = str(completion_result.follow_up_step_id or "").strip()
        if not workflow or not start_step:
            return None, None
        return workflow, start_step


def _truncate(text: str, *, max_chars: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _workflow_follow_up_target(completion_result: CompletionGateResult) -> str:
    workflow = str(completion_result.follow_up_workflow or "").strip()
    step_label = str(completion_result.follow_up_step_label or completion_result.follow_up_step_id or "").strip()
    if workflow and step_label:
        return f"{workflow} -> {step_label}"
    return workflow or step_label
