"""Deterministic completion checks for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..documents.active_task import infer_immediate_task_transition
from ..storage.base import StoredDelegatedTask
from .evidence_gate import EvidenceGateService
from .execution import ExecutionResult
from .quality_gate import QualityGateService
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
_DIRECT_REPLY_INSTRUCTION_MARKERS = (
    "only reply",
    "reply only",
    "only respond",
    "respond only",
    "answer only",
    "say exactly",
    "return exactly",
    "output exactly",
    "only output",
    "output only",
    "只回覆",
    "只回答",
    "只要回覆",
    "只需回覆",
    "只輸出",
    "只要輸出",
    "只需輸出",
    "不要加入其他文字",
)


@dataclass(frozen=True)
class CompletionGateResult:
    """Structured verdict about whether one turn completed the active objective."""

    status: str
    reason: str
    active_task_status: str | None = None
    active_task_detail: str | None = None
    follow_up_workflow: str | None = None
    follow_up_step_id: str | None = None
    follow_up_step_label: str | None = None
    follow_up_prompt_type: str | None = None
    verification_action: str | None = None
    verification_path: str | None = None
    verification_pytest_args: tuple[str, ...] = ()
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
    missing_evidence: tuple[str, ...] = ()

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
            "missing_evidence": list(self.missing_evidence),
        }
        if self.active_task_status:
            payload["active_task_status"] = self.active_task_status
        if self.active_task_detail:
            payload["active_task_detail"] = self.active_task_detail
        if self.follow_up_workflow:
            payload["follow_up_workflow"] = self.follow_up_workflow
        if self.follow_up_step_id:
            payload["follow_up_step_id"] = self.follow_up_step_id
        if self.follow_up_step_label:
            payload["follow_up_step_label"] = self.follow_up_step_label
        if self.follow_up_prompt_type:
            payload["follow_up_prompt_type"] = self.follow_up_prompt_type
        if self.verification_action:
            payload["verification_action"] = self.verification_action
        if self.verification_path:
            payload["verification_path"] = self.verification_path
        if self.verification_pytest_args:
            payload["verification_pytest_args"] = list(self.verification_pytest_args)
        return payload


class CompletionGateService:
    """Evaluate completion without calling the LLM or continuing autonomously."""

    def __init__(
        self,
        *,
        evidence_gate: EvidenceGateService | None = None,
        quality_gate: QualityGateService | None = None,
    ):
        self.evidence_gate = evidence_gate or EvidenceGateService()
        self.quality_gate = quality_gate or QualityGateService()

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
    ) -> CompletionGateResult:
        """Return the safest completion verdict for the current turn."""
        contract_allows_plain_answer = _contract_allows_plain_answer(execution_result.task_contract)
        verification_required = False if contract_allows_plain_answer else _requires_verification(task_intent)
        expects_code_change = False if contract_allows_plain_answer else task_intent.expects_code_change
        verification_attempted = execution_result.verification_attempted
        verification_passed = execution_result.verification_passed
        verification_follow_up = _verification_follow_up(task_intent, execution_result)
        review = _review_evidence(execution_result.delegated_tasks)
        review_required = expects_code_change and execution_result.file_change_count > 0
        workflow_gate = _workflow_gate_outcome(
            task_intent=task_intent,
            workflow_outcomes=execution_result.workflow_outcomes,
            verification_required=verification_required,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
        )

        if execution_result.assistant_internal_only_response:
            return CompletionGateResult(
                status="incomplete",
                reason="assistant only emitted internal control text",
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

        planner_status = _task_contract_planner_status(execution_result.task_contract)
        if planner_status in {"blocked", "invalid"}:
            reason = "task contract planner did not produce a validated contract"
            detail = _task_contract_planner_reason(execution_result.task_contract) or reason
            return CompletionGateResult(
                status="blocked",
                reason=reason,
                active_task_status="blocked",
                active_task_detail=detail,
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

        if execution_result.stop_reason == "max_tool_iterations":
            return CompletionGateResult(
                status="incomplete",
                reason="max tool iterations exhausted before completion",
                active_task_detail="The execution loop hit the configured max_tool_iterations limit and needs another bounded continuation pass.",
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
            if verification_required and verification_attempted and not verification_passed:
                return CompletionGateResult(
                    status="needs_verification",
                    reason="required verification did not pass",
                    verification_required=True,
                    verification_attempted=True,
                    verification_passed=False,
                    verification_action=verification_follow_up["action"],
                    verification_path=verification_follow_up["path"],
                    verification_pytest_args=verification_follow_up["pytest_args"],
                    review_required=review_required,
                    review_attempted=review["attempted"],
                    review_passed=review["passed"],
                    review_summary=review["summary"],
                    review_prompt_types=review["prompt_types"],
                    review_finding_count=review["finding_count"],
                )
            immediate_transition = infer_immediate_task_transition(
                response_text,
                had_tool_error=True,
            )
            if immediate_transition is not None and immediate_transition[0] == "blocked":
                _, detail = immediate_transition
                return CompletionGateResult(
                    status="blocked",
                    reason="assistant reported a blocker",
                    active_task_status="blocked",
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
            if not self._tool_errors_are_non_blocking(
                task_intent=task_intent,
                response_text=response_text,
                execution_result=execution_result,
                verification_passed=verification_passed,
            ):
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

        if workflow_gate is not None:
            workflow_verification_attempted = bool(workflow_gate.get("verification_attempted", verification_attempted))
            workflow_verification_passed = bool(workflow_gate.get("verification_passed", verification_passed))
            workflow_review_attempted = bool(workflow_gate.get("review_attempted", review["attempted"]))
            workflow_review_passed = bool(workflow_gate.get("review_passed", review["passed"]))
            workflow_review_summary = str(workflow_gate.get("review_summary") or review["summary"] or "").strip()
            workflow_review_finding_count = int(workflow_gate.get("review_finding_count", review["finding_count"]))
            return CompletionGateResult(
                status=workflow_gate["status"],
                reason=workflow_gate["reason"],
                active_task_status="done" if workflow_gate["status"] == "complete" else None,
                active_task_detail=workflow_gate.get("detail") or None,
                follow_up_workflow=_string_or_none(workflow_gate.get("workflow")),
                follow_up_step_id=_string_or_none(workflow_gate.get("next_step_id")),
                follow_up_step_label=_string_or_none(workflow_gate.get("next_step_label")),
                follow_up_prompt_type=_string_or_none(workflow_gate.get("next_step_prompt_type")),
                should_update_active_task=workflow_gate["status"] == "complete" and task_intent.should_seed_active_task,
                verification_required=verification_required,
                verification_attempted=workflow_verification_attempted,
                verification_passed=workflow_verification_passed,
                verification_action=verification_follow_up["action"] if workflow_gate["status"] == "needs_verification" else None,
                verification_path=verification_follow_up["path"] if workflow_gate["status"] == "needs_verification" else None,
                verification_pytest_args=verification_follow_up["pytest_args"] if workflow_gate["status"] == "needs_verification" else (),
                review_required=review_required,
                review_attempted=workflow_review_attempted,
                review_passed=workflow_review_passed,
                review_summary=workflow_review_summary,
                review_prompt_types=review["prompt_types"],
                review_finding_count=workflow_review_finding_count,
            )

        immediate_transition = infer_immediate_task_transition(
            response_text,
            had_tool_error=execution_result.had_tool_error,
        )
        if immediate_transition is not None and immediate_transition[0] == "blocked":
            _, detail = immediate_transition
            return CompletionGateResult(
                status="blocked",
                reason="assistant reported a blocker",
                active_task_status="blocked",
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
                verification_action=verification_follow_up["action"],
                verification_path=verification_follow_up["path"],
                verification_pytest_args=verification_follow_up["pytest_args"],
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
                active_task_detail=_review_follow_up_detail(review),
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

        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=verification_passed,
        )
        if not evidence_result.passed:
            return CompletionGateResult(
                status="incomplete",
                reason=evidence_result.reason,
                active_task_detail=evidence_result.active_task_detail,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review["attempted"],
                review_passed=review["passed"],
                review_summary=review["summary"],
                review_prompt_types=review["prompt_types"],
                review_finding_count=review["finding_count"],
                missing_evidence=evidence_result.missing_evidence,
            )

        quality_result = self.quality_gate.evaluate(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            task_contract=evidence_result.task_contract,
        )
        if not quality_result.passed:
            return CompletionGateResult(
                status=quality_result.status,
                reason=quality_result.reason,
                active_task_detail=quality_result.active_task_detail,
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

        is_direct_reply = _looks_like_direct_reply_instruction(task_intent.objective)
        if is_direct_reply and response_text.strip() and not _looks_incomplete(response_text):
            return CompletionGateResult(
                status="complete",
                reason="direct reply instruction received a response",
                active_task_status="done" if task_intent.should_seed_active_task else None,
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

        if task_intent.kind not in {"analysis", "debug", "review", "writing"}:
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

        if task_intent.kind in {"analysis", "review", "writing"} and response_text.strip() and not _looks_incomplete(response_text):
            completion_reason = (
                "writing-style task returned a substantive response"
                if task_intent.kind == "writing"
                else "analysis-style task returned a substantive response"
            )
            return CompletionGateResult(
                status="complete",
                reason=completion_reason,
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

        if task_intent.kind == "task" and not expects_code_change and response_text.strip() and not _looks_incomplete(response_text):
            return CompletionGateResult(
                status="complete",
                reason="generic task returned a response",
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

        if _contract_has_completion_criteria(evidence_result.task_contract) and response_text.strip() and not _looks_incomplete(response_text):
            return CompletionGateResult(
                status="complete",
                reason="task contract was satisfied",
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

    def _tool_errors_are_non_blocking(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
        verification_passed: bool,
    ) -> bool:
        """Allow optional web discovery failures after required web sources are satisfied."""
        if not _has_only_optional_web_discovery_failures(execution_result):
            return False

        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=verification_passed,
        )
        if not evidence_result.passed:
            return False

        if not _has_successful_fetched_web_source_artifact(execution_result):
            return False

        quality_result = self.quality_gate.evaluate(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            task_contract=evidence_result.task_contract,
        )
        return quality_result.passed


def _requires_verification(task_intent: TaskIntent) -> bool:
    return task_intent.expects_verification


def _contract_allows_plain_answer(task_contract: Any) -> bool:
    return bool(
        task_contract is not None
        and getattr(task_contract, "task_type", None) == "pure_answer"
        and getattr(task_contract, "allow_no_tool_final", False)
        and not tuple(getattr(task_contract, "requirements", ()) or ())
    )


def _task_contract_planner_status(task_contract: Any) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get("planner_status") or "").strip()
    return ""


def _task_contract_planner_reason(task_contract: Any) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get("reason") or "").strip()
    return ""


def _has_only_optional_web_discovery_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    has_successful_fetch_sources = _has_successful_fetched_web_source_artifact(execution_result)
    for item in failed_evidence:
        if item.name in {"web_search", "web_research"}:
            continue
        if item.name == "web_fetch" and has_successful_fetch_sources:
            continue
        if _is_non_exposed_permission_block(item):
            continue
        return False
    return True


def _requires_web_research_evidence(task_contract: Any) -> bool:
    if getattr(task_contract, "task_type", None) == "web_research":
        return True
    return any(
        getattr(requirement, "tool_group", None) == "web_research"
        for requirement in getattr(task_contract, "requirements", ())
    )


def _has_successful_fetched_web_source_artifact(execution_result: ExecutionResult) -> bool:
    for artifact in execution_result.task_artifacts:
        if artifact.kind != "web_source" or not artifact.ok:
            continue
        sources = artifact.metadata.get("sources") if isinstance(artifact.metadata, dict) else None
        if artifact.source_tool in {"web_fetch", "browser_navigate", "browser_snapshot"} and isinstance(sources, list) and sources:
            return True
        if artifact.source_tool == "web_research" and _web_research_artifact_has_successful_fetch(artifact):
            return True
    return False


def _is_non_exposed_permission_block(evidence: ToolEvidence) -> bool:
    permission = evidence.metadata.get("permission") if isinstance(evidence.metadata, dict) else None
    return bool(
        isinstance(permission, dict)
        and permission.get("blocked") is True
        and permission.get("exposed") is False
    )


def _web_research_artifact_has_successful_fetch(artifact: TaskArtifact) -> bool:
    metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
    coverage = metadata.get("coverage") if isinstance(metadata.get("coverage"), dict) else {}
    if int(coverage.get("fetched_count") or 0) > 0:
        return True
    sources = metadata.get("sources")
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("tool_name") != "web_fetch":
            continue
        if source.get("blocked_or_challenge") or source.get("is_too_short"):
            continue
        if int(source.get("content_chars") or 0) > 0 or source.get("has_main_content"):
            return True
    return False


def _looks_complete(response_text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (response_text or "").strip().lower())
    if not lowered:
        return False
    if _looks_incomplete(response_text):
        return False
    return any(marker in lowered for marker in _COMPLETE_MARKERS)


def _contract_has_completion_criteria(task_contract: Any) -> bool:
    return bool(getattr(task_contract, "requirements", ()) or getattr(task_contract, "acceptance_criteria", ()))


def _looks_incomplete(response_text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (response_text or "").strip().lower())
    return any(marker in lowered for marker in _INCOMPLETE_MARKERS) or _looks_like_pending_action_response(lowered)


def _looks_like_pending_action_response(normalized_lowered: str) -> bool:
    text = normalized_lowered.strip()
    if not text:
        return False
    pending_patterns = (
        r"\blet\s*(?:me|us)?\b.*\b(?:search|look up|check|fetch|research)\b",
        r"\bi(?:'ll| will)\b.*\b(?:search|look up|check|fetch|research)\b",
        r"^.{0,20}\blet.*先.*(?:搜尋|搜寻|查詢|查询|查一下|查)",
        r"^.{0,16}(?:我|現在|现在)?(?:先|來|来).*(?:搜尋|搜寻|查詢|查询|查一下)",
        r"^.{0,20}(?:透過|通过).*(?:網路|网络|web).*(?:搜尋|搜寻|查詢|查询)",
    )
    return any(re.search(pattern, text) for pattern in pending_patterns)


def _looks_like_direct_reply_instruction(objective: str) -> bool:
    normalized = re.sub(r"\s+", " ", (objective or "").strip().lower())
    return any(marker in normalized for marker in _DIRECT_REPLY_INSTRUCTION_MARKERS)


def _review_evidence(delegated_tasks: tuple[StoredDelegatedTask, ...]) -> dict[str, Any]:
    prompt_types: list[str] = []
    finding_count = 0
    attempted = False
    clean_review_recorded = False
    problematic_review_recorded = False
    summary = ""
    first_finding = ""
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
        if not first_finding:
            first_finding = _first_review_finding(structured)
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
        "first_finding": first_finding,
    }


def _workflow_gate_outcome(
    *,
    task_intent: TaskIntent,
    workflow_outcomes: tuple[dict[str, Any], ...],
    verification_required: bool,
    verification_attempted: bool,
    verification_passed: bool,
) -> dict[str, Any] | None:
    relevant_outcomes = [
        outcome
        for outcome in workflow_outcomes
        if isinstance(outcome, dict) and str(outcome.get("workflow") or "").strip()
    ]
    if not relevant_outcomes:
        return None
    workflow = relevant_outcomes[-1]
    workflow_id = str(workflow.get("workflow") or "").strip()
    workflow_status = str(workflow.get("status") or "").strip()
    review_attempted = bool(workflow.get("review_attempted"))
    review_passed = bool(workflow.get("review_passed"))
    review_finding_count = int(workflow.get("review_finding_count") or 0)
    workflow_verification_attempted = bool(workflow.get("verification_attempted"))
    workflow_verification_passed = bool(workflow.get("verification_passed"))
    workflow_review_summary = str(workflow.get("review_summary") or "").strip()
    workflow_review_first_finding = str(workflow.get("review_first_finding") or "").strip()
    next_step_id = str(workflow.get("next_step_id") or "").strip()
    next_step_label = str(workflow.get("next_step_label") or "").strip()
    next_step_prompt_type = str(workflow.get("next_step_prompt_type") or "").strip()
    metadata = {
        "workflow": workflow_id,
        "review_attempted": review_attempted,
        "review_passed": review_passed,
        "review_finding_count": review_finding_count,
        "review_summary": workflow_review_summary,
        "verification_attempted": workflow_verification_attempted,
        "verification_passed": workflow_verification_passed,
        **(
            {
                "next_step_id": next_step_id,
                "next_step_label": next_step_label,
                "next_step_prompt_type": next_step_prompt_type,
            }
            if next_step_id or next_step_label or next_step_prompt_type
            else {}
        ),
    }

    if workflow_status in {"failed", "cancelled"}:
        detail = _workflow_follow_up_detail(workflow_id, workflow_status, workflow)
        return {
            **metadata,
            "status": "blocked" if workflow_status == "failed" else "incomplete",
            "reason": f"workflow {workflow_id} did not complete successfully",
            "detail": detail,
        }

    if workflow_id == "research_then_outline":
        return {
            **metadata,
            "status": "complete",
            "reason": "workflow research_then_outline completed all required steps",
        }

    if workflow_id in {"implement_then_review", "bugfix_then_test_then_review"}:
        if not review_attempted:
            review_step = _workflow_review_follow_up_fields(workflow_id)
            return {
                **metadata,
                "status": "needs_review",
                "reason": f"workflow {workflow_id} completed but review evidence is missing",
                "detail": "Run or rerun a delegated review step for the changed code before treating the workflow as complete.",
                **review_step,
            }
        if not review_passed or review_finding_count > 0:
            fix_step = _workflow_fix_follow_up_fields(workflow_id)
            return {
                **metadata,
                "status": "needs_review",
                "reason": f"workflow {workflow_id} completed but review findings still require follow-up",
                "detail": workflow_review_first_finding
                or workflow_review_summary
                or str(workflow.get("summary") or "").strip(),
                **fix_step,
            }
        if verification_required and not (verification_passed or workflow_verification_passed):
            return {
                **metadata,
                "status": "needs_verification",
                "reason": "workflow completed but required verification evidence is still missing",
                "detail": str(workflow.get("summary") or "").strip(),
            }
        return {
            **metadata,
            "status": "complete",
            "reason": f"workflow {workflow_id} completed with clean review evidence",
        }

    if verification_required and not (verification_passed or workflow_verification_passed):
        return {
            **metadata,
            "status": "needs_verification",
            "reason": "workflow completed but required verification evidence is still missing",
            "detail": str(workflow.get("summary") or "").strip(),
        }

    if task_intent.kind in {"analysis", "review"}:
        return {
            **metadata,
            "status": "complete",
            "reason": f"workflow {workflow_id} completed all required steps",
        }

    return None


def _first_review_finding(structured_output: Any) -> str:
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


def _review_follow_up_detail(review: dict[str, Any]) -> str | None:
    if not review.get("attempted"):
        return "Run or rerun a delegated review step for the changed code before treating the task as complete."
    detail = str(review.get("first_finding") or review.get("summary") or "").strip()
    return detail or "Address the delegated review findings before treating the task as complete."


def _workflow_follow_up_detail(workflow_id: str, workflow_status: str, workflow: dict[str, Any]) -> str:
    step_label = str(workflow.get("next_step_label") or workflow.get("next_step_id") or "").strip()
    error = str(workflow.get("error") or "").strip()
    summary = str(workflow.get("summary") or "").strip()
    if workflow_status == "cancelled":
        if step_label and summary:
            return f"Resume with the {step_label} step in {workflow_id}. {summary}"
        if step_label:
            return f"Resume with the {step_label} step in {workflow_id}."
        if summary:
            return f"Finish the remaining workflow steps for {workflow_id}. {summary}"
        return f"Finish the remaining workflow steps for {workflow_id}."
    if step_label and error:
        return f"Resolve the {step_label} step failure in {workflow_id}: {error}"
    if step_label:
        return f"Resolve the {step_label} step failure in {workflow_id}."
    return error or summary


def _workflow_review_follow_up_fields(workflow_id: str) -> dict[str, str]:
    if workflow_id in {"implement_then_review", "bugfix_then_test_then_review"}:
        return {
            "next_step_id": "review",
            "next_step_label": "Code review",
            "next_step_prompt_type": "code-reviewer",
        }
    return {}


def _workflow_fix_follow_up_fields(workflow_id: str) -> dict[str, str]:
    if workflow_id == "implement_then_review":
        return {
            "next_step_id": "implement",
            "next_step_label": "Implement",
            "next_step_prompt_type": "implementer",
        }
    if workflow_id == "bugfix_then_test_then_review":
        return {
            "next_step_id": "bugfix",
            "next_step_label": "Bug fix",
            "next_step_prompt_type": "bug-fixer",
        }
    return {}


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _verification_follow_up(task_intent: TaskIntent, execution_result: ExecutionResult) -> dict[str, Any]:
    touched_paths = _normalized_touched_paths(execution_result.touched_paths)
    lowered_objective = str(task_intent.objective or "").lower()
    test_paths = tuple(path for path in touched_paths if path.startswith("tests/") and path.endswith(".py"))
    if any(path.startswith("apps/web/") or path == "apps/web" for path in touched_paths) and any(
        marker in lowered_objective for marker in ("test:smoke", "smoke test", "smoke", "contract check")
    ):
        return {"action": "web_smoke", "path": "apps/web", "pytest_args": ()}
    if any(path.startswith("apps/web/") or path == "apps/web" for path in touched_paths) or any(
        marker in lowered_objective for marker in ("web build", "vite build", "frontend build", "frontend", "vite")
    ):
        return {"action": "web_build", "path": "apps/web", "pytest_args": ()}
    if test_paths or any(marker in lowered_objective for marker in ("pytest", "run tests", "run the tests", "test", "tests", "測試")):
        return {"action": "pytest", "path": ".", "pytest_args": test_paths}
    if any(path.endswith(".py") for path in touched_paths) or any(marker in lowered_objective for marker in ("compile", "syntax", "編譯")):
        return {
            "action": "python_compile",
            "path": _common_verification_path(touched_paths) or ".",
            "pytest_args": (),
        }
    return {
        "action": "auto",
        "path": _common_verification_path(touched_paths) or ".",
        "pytest_args": (),
    }


def _normalized_touched_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized = [str(path or "").replace("\\", "/").strip("/") for path in paths]
    return tuple(path for path in normalized if path)


def _common_verification_path(paths: tuple[str, ...]) -> str | None:
    if not paths:
        return None
    parts_list = [path.split("/") for path in paths if path]
    if not parts_list:
        return None
    common: list[str] = []
    for segments in zip(*parts_list):
        if len(set(segments)) != 1:
            break
        common.append(segments[0])
    if not common:
        return "."
    if len(common) == len(parts_list[0]) and not paths[0].endswith("/"):
        return "/".join(common[:-1]) or "."
    return "/".join(common) or "."
