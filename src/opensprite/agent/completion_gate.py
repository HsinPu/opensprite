"""Deterministic completion checks for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..documents.active_task import infer_immediate_task_transition
from ..storage.base import StoredDelegatedTask
from .execution import ExecutionResult
from .task_intent import TaskIntent


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
_REVIEW_PROMPT_TYPES = frozenset({"code-reviewer", "security-reviewer", "async-concurrency-reviewer"})


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
    review_required: bool = False
    review_attempted: bool = False
    review_passed: bool = False
    review_summary: str = ""
    review_prompt_types: tuple[str, ...] = ()
    review_finding_count: int = 0

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
            "review_required": self.review_required,
            "review_attempted": self.review_attempted,
            "review_passed": self.review_passed,
            "review_summary": self.review_summary,
            "review_prompt_types": list(self.review_prompt_types),
            "review_finding_count": self.review_finding_count,
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
        expects_code_change = task_intent.expects_code_change
        verification_attempted = execution_result.verification_attempted
        verification_passed = execution_result.verification_passed
        review = _review_evidence(execution_result.delegated_tasks)
        review_required = expects_code_change and execution_result.file_change_count > 0

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
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
            )

        if execution_result.had_tool_error:
            return CompletionGateResult(
                status="incomplete",
                reason="tool execution reported an error without a clear blocker handoff",
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
            )

        if expects_code_change and execution_result.file_change_count <= 0:
            return CompletionGateResult(
                status="incomplete",
                reason="expected code changes were not recorded",
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=False,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
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
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
            )

        if review_required and not review["passed"]:
            reason = (
                "delegated review reported findings that require follow-up"
                if review["attempted"]
                else "delegated review was not recorded for code changes"
            )
            return CompletionGateResult(
                status="needs_review",
                reason=reason,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=True,
                review_attempted=review["attempted"],
                review_passed=False,
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
            )

        if task_intent.kind in {"conversation", "question", "command", "media_upload"}:
            return CompletionGateResult(
                status="complete" if response_text.strip() else "incomplete",
                reason="one-turn intent received a response" if response_text.strip() else "assistant response was empty",
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
            )

        if task_intent.kind in {"analysis", "review"} and response_text.strip() and not _looks_incomplete(response_text):
            return CompletionGateResult(
                status="complete",
                reason="analysis-style task returned a substantive response",
                active_task_status="done",
                should_update_active_task=task_intent.should_seed_active_task,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
            )

        if task_intent.kind == "debug" and not expects_code_change and response_text.strip() and not _looks_incomplete(response_text):
            return CompletionGateResult(
                status="complete",
                reason="debug diagnosis was provided without requiring code changes",
                active_task_status="done",
                should_update_active_task=task_intent.should_seed_active_task,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
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
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
            )

        return CompletionGateResult(
            status="incomplete",
            reason="assistant response did not explicitly complete the task",
            verification_required=verification_required,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
            review_required=review_required,
            review_attempted=review["attempted"],
            review_passed=review["passed"],
            review_summary=review["summary"],
            review_prompt_types=review["prompt_types"],
            review_finding_count=review["finding_count"],
        )


def _requires_verification(task_intent: TaskIntent) -> bool:
    return task_intent.expects_verification


def _looks_complete(response_text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (response_text or "").strip().lower())
    if not lowered:
        return False
    if _looks_incomplete(response_text):
        return False
    return any(marker in lowered for marker in _COMPLETE_MARKERS)


def _looks_incomplete(response_text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (response_text or "").strip().lower())
    return any(marker in lowered for marker in _INCOMPLETE_MARKERS)


def _review_evidence(delegated_tasks: tuple[StoredDelegatedTask, ...]) -> dict[str, Any]:
    prompt_types: list[str] = []
    finding_count = 0
    attempted = False
    clean_review_recorded = False
    problematic_review_recorded = False
    summary = ""
    for task in delegated_tasks:
        prompt_type = str(task.prompt_type or "").strip()
        if prompt_type not in _REVIEW_PROMPT_TYPES:
            continue
        prompt_types.append(prompt_type)
        if str(task.status or "") != "completed":
            continue
        attempted = True
        structured = task.metadata.get("structured_output") if isinstance(task.metadata, dict) else None
        structured_status = str((structured or {}).get("status") or "").strip()
        task_findings = int((structured or {}).get("finding_count") or 0)
        finding_count += max(0, task_findings)
        task_summary = str((structured or {}).get("summary") or task.summary or "").strip()
        if task_summary and not summary:
            summary = task_summary
        if structured_status == "ok" and task_findings == 0:
            clean_review_recorded = True
            continue
        lowered_summary = re.sub(r"\s+", " ", task_summary.lower())
        if task_findings == 0 and ("no major findings" in lowered_summary or "沒有重大發現" in lowered_summary):
            clean_review_recorded = True
            continue
        problematic_review_recorded = True
    return {
        "attempted": attempted,
        "passed": attempted and clean_review_recorded and not problematic_review_recorded and finding_count == 0,
        "summary": summary,
        "prompt_types": tuple(dict.fromkeys(prompt_types)),
        "finding_count": finding_count,
    }
