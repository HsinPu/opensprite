"""Deterministic completion checks for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..documents.active_task import infer_immediate_task_transition
from .execution import ExecutionResult
from .task_intent import TaskIntent


_VERIFICATION_REQUIRED_MARKERS = (
    "test",
    "tests",
    "pytest",
    "verify",
    "verification",
    "build",
    "compile",
    "測試",
    "驗證",
    "建置",
    "編譯",
)
_COMPLETE_MARKERS = (
    "all set",
    "complete",
    "completed",
    "done",
    "finished",
    "fixed",
    "implemented",
    "resolved",
    "successfully",
    "passed",
    "passes",
    "verified",
    "已完成",
    "完成",
    "已處理",
    "已修正",
    "已修復",
    "通過",
)
_INCOMPLETE_MARKERS = (
    "not complete",
    "not completed",
    "not done",
    "cannot complete",
    "can't complete",
    "could not complete",
    "unable to complete",
    "still need",
    "needs more",
    "未完成",
    "尚未完成",
    "無法完成",
    "還需要",
)


@dataclass(frozen=True)
class CompletionGateResult:
    """Structured verdict about whether one turn completed the active objective."""

    status: str
    reason: str
    active_task_status: str | None = None
    active_task_detail: str | None = None
    should_update_active_task: bool = False
    verification_required: bool = False
    verification_attempted: bool = False
    verification_passed: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe run event payload."""
        payload: dict[str, Any] = {
            "schema_version": 1,
            "status": self.status,
            "reason": self.reason,
            "should_update_active_task": self.should_update_active_task,
            "verification_required": self.verification_required,
            "verification_attempted": self.verification_attempted,
            "verification_passed": self.verification_passed,
        }
        if self.active_task_status:
            payload["active_task_status"] = self.active_task_status
        if self.active_task_detail:
            payload["active_task_detail"] = self.active_task_detail
        return payload


class CompletionGateService:
    """Evaluate completion without calling the LLM or continuing autonomously."""

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
    ) -> CompletionGateResult:
        """Return the safest completion verdict for the current turn."""
        verification_required = _requires_verification(task_intent)
        verification_attempted = execution_result.verification_attempted
        verification_passed = execution_result.verification_passed

        immediate_transition = infer_immediate_task_transition(
            response_text,
            had_tool_error=execution_result.had_tool_error,
        )
        if immediate_transition is not None:
            active_task_status, detail = immediate_transition
            reason = "assistant requested user input" if active_task_status == "waiting_user" else "assistant reported a blocker"
            return CompletionGateResult(
                status=active_task_status,
                reason=reason,
                active_task_status=active_task_status,
                active_task_detail=detail,
                should_update_active_task=True,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
            )

        if execution_result.had_tool_error:
            return CompletionGateResult(
                status="incomplete",
                reason="tool execution reported an error without a clear blocker handoff",
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
            )

        if verification_required and not verification_passed:
            reason = (
                "required verification did not pass"
                if verification_attempted
                else "required verification was not recorded"
            )
            return CompletionGateResult(
                status="needs_verification",
                reason=reason,
                verification_required=True,
                verification_attempted=verification_attempted,
                verification_passed=False,
            )

        if task_intent.kind in {"conversation", "question", "command", "media_upload"}:
            return CompletionGateResult(
                status="complete" if response_text.strip() else "incomplete",
                reason="one-turn intent received a response" if response_text.strip() else "assistant response was empty",
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
            )

        if _looks_complete(response_text):
            return CompletionGateResult(
                status="complete",
                reason="assistant response explicitly indicates completion",
                active_task_status="done",
                should_update_active_task=task_intent.should_seed_active_task,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
            )

        return CompletionGateResult(
            status="incomplete",
            reason="assistant response did not explicitly complete the task",
            verification_required=verification_required,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
        )


def _requires_verification(task_intent: TaskIntent) -> bool:
    haystack = task_intent.objective.lower()
    return any(marker in haystack for marker in _VERIFICATION_REQUIRED_MARKERS)


def _looks_complete(response_text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (response_text or "").strip().lower())
    if not lowered:
        return False
    if any(marker in lowered for marker in _INCOMPLETE_MARKERS):
        return False
    return any(marker in lowered for marker in _COMPLETE_MARKERS)
