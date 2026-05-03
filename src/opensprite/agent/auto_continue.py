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
        verification_instruction = ""
        if completion_result.status == "needs_verification":
            verification_instruction = (
                "\n- Verification is required. Use available verification tools or clearly state the blocker "
                "if verification cannot be run."
            )
        review_instruction = ""
        if completion_result.status == "needs_review":
            review_instruction = (
                "\n- Review evidence is required for the recorded code changes. Use delegated review workflows or review-focused subagents, "
                "then summarize whether the review found issues that still need follow-up."
            )

        return (
            "Continue the current task without asking the user unless you are blocked.\n"
            f"- Original objective: {task_intent.objective}\n"
            f"- Completion gate status: {completion_result.status}\n"
            f"- Completion gate reason: {completion_result.reason}"
            f"{verification_instruction}\n"
            f"{review_instruction}\n"
            "- If the task is complete, provide the final answer with the evidence or verification result.\n"
            "- If the task cannot proceed, state the blocker clearly.\n\n"
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


def _truncate(text: str, *, max_chars: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
