"""Deterministic completion checks for one agent turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import DocumentLlmConfig
from ..storage.base import StoredDelegatedTask
from .evidence_gate import EvidenceGateService
from .execution import ExecutionResult
from .completion_judge import (
    CompletionJudgeError,
    CompletionJudgeService,
    CompletionJudgeVerdict,
    build_completion_judge_facts,
)
from .quality_gate import QualityGateService
from .task_contract import contract_expects_file_change
from .task_intent import TaskIntent

_WORKSPACE_DISCOVERY_TOOLS = frozenset({"read_file", "list_dir", "grep_files", "glob_files", "code_navigation"})
_REVIEW_PROMPT_TYPES = frozenset({"code-reviewer", "security-reviewer", "async-concurrency-reviewer"})
_BLOCKING_PLANNER_STATUSES = frozenset({"blocked", "invalid"})
_UNSUCCESSFUL_WORKFLOW_STATUSES = frozenset({"failed", "cancelled"})
_NO_FALLBACK_ACTIVE_TASK_UPDATE_TYPES = frozenset({"pure_answer", "planning_error"})
_READ_ONLY_TASK_TYPES = frozenset({"analysis", "operations", "workspace_read", "history_retrieval", "web_research"})
_ONE_TURN_INTENT_KINDS = frozenset({"conversation", "question", "command", "media_upload"})
_FINAL_RESPONSE_ACCEPTED_TASK_TYPES = frozenset({"analysis", "planning", "task"})
_READ_ONLY_BLOCKING_REQUIREMENT_KINDS = frozenset({"file_change", "verification"})
_READ_ONLY_BLOCKING_TOOL_GROUPS = frozenset({"workspace_write", "execution", "verification", "scheduling"})
_OPTIONAL_WEB_DISCOVERY_FAILURE_TOOLS = frozenset({"web_search", "web_research"})
_FETCHED_WEB_SOURCE_ARTIFACT_TOOLS = frozenset({"web_fetch", "browser_navigate", "browser_snapshot"})
_ANALYSIS_RESPONSE_INTENT_KIND = "analysis"
_GENERIC_TASK_RESPONSE_INTENT_KIND = "task"
_WORKFLOW_COMPLETION_INTENT_KINDS = frozenset({"analysis", "review"})
_REVIEW_WORKFLOW_IDS = frozenset({"implement_then_review", "bugfix_then_test_then_review"})
_WORKFLOW_FIX_STEPS = {
    "implement_then_review": {
        "next_step_id": "implement",
        "next_step_label": "Implement",
        "next_step_prompt_type": "implementer",
    },
    "bugfix_then_test_then_review": {
        "next_step_id": "bugfix",
        "next_step_label": "Bug fix",
        "next_step_prompt_type": "bug-fixer",
    },
}
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
    file_change_required: bool = False
    missing_evidence: tuple[str, ...] = ()
    progress_only_response: bool = False
    judge_metadata: dict[str, Any] = field(default_factory=dict)

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
            "file_change_required": self.file_change_required,
            "missing_evidence": list(self.missing_evidence),
            "progress_only_response": self.progress_only_response,
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
        if self.judge_metadata:
            payload["judge"] = dict(self.judge_metadata)
        return payload


class CompletionGateService:
    """Evaluate completion without calling the LLM or continuing autonomously."""

    def __init__(
        self,
        *,
        llm_config: DocumentLlmConfig | None = None,
        judge_service: CompletionJudgeService | None = None,
        evidence_gate: EvidenceGateService | None = None,
        quality_gate: QualityGateService | None = None,
    ):
        self.llm_config = llm_config
        self.judge_service = judge_service or (CompletionJudgeService(llm_config) if llm_config is not None else None)
        self.evidence_gate = evidence_gate or EvidenceGateService()
        self.quality_gate = quality_gate or QualityGateService()

    async def evaluate_with_judge(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
        provider: Any,
        model: str | None,
    ) -> CompletionGateResult:
        """Return the LLM judge verdict for the current turn."""
        judge = self.judge_service
        if judge is None:
            return _completion_judge_blocked_result("completion judge unavailable: missing llm config")
        facts = build_completion_judge_facts(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
        )
        try:
            verdict = await judge.judge(provider=provider, model=model, facts=facts)
        except CompletionJudgeError as exc:
            return _completion_judge_blocked_result(str(exc))
        except Exception as exc:
            return _completion_judge_blocked_result(f"completion judge failed: {type(exc).__name__}")
        return _completion_result_from_judge_verdict(verdict, execution_result=execution_result)

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
    ) -> CompletionGateResult:
        """Return the safest completion verdict for the current turn."""
        contract_allows_plain_answer = _contract_allows_plain_answer(execution_result.task_contract)
        verification_required = False if contract_allows_plain_answer else _requires_verification(execution_result.task_contract)
        expects_code_change = (
            False
            if contract_allows_plain_answer or _contract_is_read_only(execution_result.task_contract)
            else contract_expects_file_change(execution_result.task_contract) or execution_result.file_change_count > 0
        )
        verification_attempted = execution_result.verification_attempted
        verification_passed = execution_result.verification_passed or _verification_skipped_with_reported_gap(execution_result)
        verification_follow_up = _verification_follow_up(execution_result)
        review = _review_evidence(execution_result.delegated_tasks)
        review_required = (
            expects_code_change
            and execution_result.file_change_count > 0
            and _requires_delegated_review(execution_result.touched_paths)
        )
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
                file_change_required=True,
            )

        planner_status = _task_contract_planner_status(execution_result.task_contract)
        if _is_blocking_planner_status(planner_status):
            reason = "task contract planner did not produce a validated contract"
            detail = _task_contract_planner_reason(execution_result.task_contract) or reason
            return CompletionGateResult(
                status="blocked",
                reason=reason,
                active_task_status="blocked",
                active_task_detail=detail,
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    execution_result.task_contract,
                ),
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
                should_update_active_task=workflow_gate["status"] == "complete"
                and _intent_supports_fallback_active_task_update(task_intent, execution_result.task_contract),
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

        if (
            contract_allows_plain_answer
            and not _contract_has_completion_criteria(execution_result.task_contract)
            and response_text.strip()
        ):
            return CompletionGateResult(
                status="complete",
                reason="plain-answer contract received a response",
                active_task_status=(
                    "done"
                    if _intent_supports_fallback_active_task_update(task_intent, execution_result.task_contract)
                    else None
                ),
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    execution_result.task_contract,
                ),
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

        if _contract_accepts_final_response(evidence_result.task_contract) and response_text.strip():
            return CompletionGateResult(
                status="complete",
                reason="task contract accepted final response",
                active_task_status="done",
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

        if _is_one_turn_intent_kind(task_intent.kind):
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

        if _is_analysis_response_intent_kind(task_intent.kind) and response_text.strip():
            return CompletionGateResult(
                status="complete",
                reason="analysis-style task returned a substantive response",
                active_task_status="done",
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    evidence_result.task_contract,
                ),
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

        if _is_generic_task_response_intent_kind(task_intent.kind) and not expects_code_change and response_text.strip():
            return CompletionGateResult(
                status="complete",
                reason="generic task returned a response",
                active_task_status=(
                    "done"
                    if _intent_supports_fallback_active_task_update(task_intent, evidence_result.task_contract)
                    else None
                ),
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    evidence_result.task_contract,
                ),
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

        if _contract_has_completion_criteria(evidence_result.task_contract) and response_text.strip():
            should_update_active_task = _intent_supports_fallback_active_task_update(
                task_intent,
                evidence_result.task_contract,
            )
            return CompletionGateResult(
                status="complete",
                reason="task contract was satisfied",
                active_task_status="done" if should_update_active_task else None,
                should_update_active_task=should_update_active_task,
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

        if (
            expects_code_change
            and execution_result.file_change_count > 0
            and response_text.strip()
            and (not verification_required or verification_passed)
            and (not review_required or review["passed"])
        ):
            should_update_active_task = not task_intent.needs_clarification
            return CompletionGateResult(
                status="complete",
                reason="required file changes and evidence were recorded",
                active_task_status="done" if should_update_active_task else None,
                should_update_active_task=should_update_active_task,
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
        """Allow exploratory discovery failures after required evidence is satisfied."""
        has_optional_web_failures = _has_only_optional_web_discovery_failures(execution_result)
        has_optional_workspace_failures = _has_only_optional_workspace_discovery_failures(execution_result)
        has_optional_history_failures = _has_only_optional_history_retrieval_failures(execution_result)
        if not (has_optional_web_failures or has_optional_workspace_failures or has_optional_history_failures):
            return False

        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=verification_passed,
        )
        if not evidence_result.passed:
            return False

        if has_optional_web_failures and not _has_successful_fetched_web_source_artifact(execution_result):
            return False

        quality_result = self.quality_gate.evaluate(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            task_contract=evidence_result.task_contract,
        )
        return quality_result.passed


def _completion_result_from_judge_verdict(
    verdict: CompletionJudgeVerdict,
    *,
    execution_result: ExecutionResult,
) -> CompletionGateResult:
    file_change_required = (
        contract_expects_file_change(execution_result.task_contract)
        and execution_result.file_change_count <= 0
    )
    return CompletionGateResult(
        status=verdict.status,
        reason=verdict.reason,
        active_task_status=verdict.active_task_status,
        active_task_detail=verdict.active_task_detail,
        follow_up_workflow=verdict.follow_up_workflow,
        follow_up_step_id=verdict.follow_up_step_id,
        follow_up_step_label=verdict.follow_up_step_label,
        follow_up_prompt_type=verdict.follow_up_prompt_type,
        verification_action=verdict.verification_action,
        verification_path=verdict.verification_path,
        verification_pytest_args=verdict.verification_pytest_args,
        should_update_active_task=bool(verdict.active_task_status),
        verification_required=verdict.verification_required,
        verification_attempted=verdict.verification_attempted,
        verification_passed=verdict.verification_passed,
        review_required=verdict.review_required,
        review_attempted=verdict.review_attempted,
        review_passed=verdict.review_passed,
        review_summary=verdict.review_summary,
        review_prompt_types=verdict.review_prompt_types,
        review_finding_count=verdict.review_finding_count,
        file_change_required=file_change_required,
        missing_evidence=verdict.missing_evidence,
        progress_only_response=verdict.progress_only_response,
        judge_metadata={
            **dict(verdict.metadata),
            "raw_response_preview": verdict.raw_response_preview,
        },
    )


def _completion_judge_blocked_result(reason: str) -> CompletionGateResult:
    return CompletionGateResult(
        status="blocked",
        reason=reason or "completion judge unavailable",
        active_task_status="blocked",
        active_task_detail=reason or "completion judge unavailable",
        should_update_active_task=True,
        judge_metadata={
            "method": "llm",
            "error": reason or "completion judge unavailable",
        },
    )


def _intent_supports_fallback_active_task_update(task_intent: TaskIntent, task_contract: Any) -> bool:
    if task_intent.needs_clarification:
        return False
    task_type = str(getattr(task_contract, "task_type", "") or "").strip()
    if not task_type:
        return False
    return task_type not in _NO_FALLBACK_ACTIVE_TASK_UPDATE_TYPES


def _requires_verification(task_contract: Any) -> bool:
    if _contract_requires_verification(task_contract):
        return True
    task_type = str(getattr(task_contract, "task_type", "") or "")
    if _is_read_only_task_type(task_type):
        return False
    return False


def _verification_skipped_with_reported_gap(execution_result: ExecutionResult) -> bool:
    if not execution_result.verification_attempted:
        return False
    if not _requires_delegated_review(execution_result.touched_paths) and not execution_result.had_tool_error:
        return True
    if not _has_skipped_verification_artifact(execution_result):
        return False
    return True


def _has_skipped_verification_artifact(execution_result: ExecutionResult) -> bool:
    for artifact in execution_result.task_artifacts:
        if artifact.kind != "verification_result" or not artifact.ok:
            continue
        if _verification_status_is_skipped(artifact.metadata):
            return True
    for evidence in execution_result.tool_evidence:
        if evidence.name != "verify" or not evidence.ok:
            continue
        if _verification_status_is_skipped(evidence.metadata):
            return True
    return False


def _verification_status_is_skipped(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get("verification_status") or "").strip().lower() == "skipped"


def _requires_delegated_review(touched_paths: tuple[str, ...]) -> bool:
    paths = _normalized_touched_paths(touched_paths)
    if not paths:
        return True
    return any(_path_requires_delegated_review(path) for path in paths)


def _path_requires_delegated_review(path: str) -> bool:
    normalized = _strip_repo_snapshot_prefix(path).lower()
    if normalized.endswith((
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".cs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".php",
        ".rb",
        ".swift",
        ".sql",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
    )):
        return True
    return normalized in {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "vite.config.js",
        "vite.config.ts",
    }


def _contract_requires_verification(task_contract: Any) -> bool:
    return any(
        str(getattr(requirement, "kind", "") or "") == "verification"
        or str(getattr(requirement, "tool_group", "") or "") == "verification"
        for requirement in getattr(task_contract, "requirements", ()) or ()
    )


def _contract_allows_plain_answer(task_contract: Any) -> bool:
    return bool(
        task_contract is not None
        and getattr(task_contract, "task_type", None) == "pure_answer"
        and getattr(task_contract, "allow_no_tool_final", False)
        and not tuple(getattr(task_contract, "requirements", ()) or ())
    )


def _contract_is_read_only(task_contract: Any) -> bool:
    task_type = str(getattr(task_contract, "task_type", "") or "")
    if _is_read_only_task_type(task_type):
        return True
    for requirement in getattr(task_contract, "requirements", ()) or ():
        if _is_read_only_blocking_requirement_kind(str(getattr(requirement, "kind", "") or "")):
            return False
        tool_group = str(getattr(requirement, "tool_group", "") or "")
        if _is_read_only_blocking_tool_group(tool_group):
            return False
    return False


def _is_read_only_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() in _READ_ONLY_TASK_TYPES


def _is_one_turn_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in _ONE_TURN_INTENT_KINDS


def _is_analysis_response_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == _ANALYSIS_RESPONSE_INTENT_KIND


def _is_generic_task_response_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == _GENERIC_TASK_RESPONSE_INTENT_KIND


def _is_read_only_blocking_requirement_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in _READ_ONLY_BLOCKING_REQUIREMENT_KINDS


def _is_read_only_blocking_tool_group(tool_group: str | None) -> bool:
    return str(tool_group or "").strip() in _READ_ONLY_BLOCKING_TOOL_GROUPS


def _task_contract_planner_status(task_contract: Any) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get("planner_status") or "").strip()
    return ""


def _is_blocking_planner_status(status: str | None) -> bool:
    return str(status or "").strip().lower() in _BLOCKING_PLANNER_STATUSES


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
        if _is_optional_web_discovery_failure_tool(item.name):
            continue
        if item.name == "web_fetch" and has_successful_fetch_sources:
            continue
        if _is_non_exposed_permission_block(item):
            continue
        return False
    return True


def _has_only_optional_workspace_discovery_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    if not any(item.ok and item.name in _WORKSPACE_DISCOVERY_TOOLS for item in execution_result.tool_evidence):
        return False
    for item in failed_evidence:
        if item.name in _WORKSPACE_DISCOVERY_TOOLS:
            continue
        if item.name == "batch" and execution_result.file_change_count <= 0:
            continue
        if _is_non_exposed_permission_block(item):
            continue
        return False
    return True


def _has_only_optional_history_retrieval_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    if not any(item.ok and item.name == "search_history" for item in execution_result.tool_evidence):
        return False
    for item in failed_evidence:
        if item.name == "search_history":
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
        if _is_fetched_web_source_artifact_tool(artifact.source_tool) and isinstance(sources, list) and sources:
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


def _is_optional_web_discovery_failure_tool(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in _OPTIONAL_WEB_DISCOVERY_FAILURE_TOOLS


def _is_fetched_web_source_artifact_tool(source_tool: str | None) -> bool:
    return str(source_tool or "").strip() in _FETCHED_WEB_SOURCE_ARTIFACT_TOOLS


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


def _contract_has_completion_criteria(task_contract: Any) -> bool:
    return bool(getattr(task_contract, "requirements", ()) or getattr(task_contract, "acceptance_criteria", ()))


def _contract_accepts_final_response(task_contract: Any) -> bool:
    if task_contract is None or _contract_has_completion_criteria(task_contract):
        return False
    if not bool(getattr(task_contract, "final_answer_required", True)):
        return False
    if not bool(getattr(task_contract, "allow_no_tool_final", False)):
        return False
    task_type = str(getattr(task_contract, "task_type", "") or "").strip()
    return _accepts_final_response_task_type(task_type)


def _accepts_final_response_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() in _FINAL_RESPONSE_ACCEPTED_TASK_TYPES


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

    if _is_unsuccessful_workflow_status(workflow_status):
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

    if _is_review_workflow(workflow_id):
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

    if _is_workflow_completion_intent_kind(task_intent.kind):
        return {
            **metadata,
            "status": "complete",
            "reason": f"workflow {workflow_id} completed all required steps",
        }

    return None


def _is_unsuccessful_workflow_status(status: str | None) -> bool:
    return str(status or "").strip().lower() in _UNSUCCESSFUL_WORKFLOW_STATUSES


def _is_workflow_completion_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in _WORKFLOW_COMPLETION_INTENT_KINDS


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
    if _is_review_workflow(workflow_id):
        return {
            "next_step_id": "review",
            "next_step_label": "Code review",
            "next_step_prompt_type": "code-reviewer",
        }
    return {}


def _workflow_fix_follow_up_fields(workflow_id: str) -> dict[str, str]:
    return dict(_WORKFLOW_FIX_STEPS.get(str(workflow_id or "").strip(), {}))


def _is_review_workflow(workflow_id: str | None) -> bool:
    return str(workflow_id or "").strip() in _REVIEW_WORKFLOW_IDS


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _verification_follow_up(execution_result: ExecutionResult) -> dict[str, Any]:
    touched_paths = _normalized_touched_paths(execution_result.touched_paths)
    decision_paths = tuple(_strip_repo_snapshot_prefix(path) for path in touched_paths)
    test_paths = tuple(path for path in decision_paths if path.startswith("tests/") and path.endswith(".py"))
    has_web_touched = any(path.startswith("apps/web/") or path == "apps/web" for path in decision_paths)
    has_python_touched = any(path.endswith(".py") for path in decision_paths)
    if touched_paths and not has_web_touched and not has_python_touched:
        return {
            "action": "auto",
            "path": _common_verification_path(touched_paths) or ".",
            "pytest_args": (),
        }
    if has_web_touched:
        return {"action": "web_build", "path": "apps/web", "pytest_args": ()}
    if test_paths:
        return {"action": "pytest", "path": ".", "pytest_args": test_paths}
    if has_python_touched:
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


def _strip_repo_snapshot_prefix(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip("/")
    if normalized.startswith("repo/"):
        return normalized[5:]
    return normalized


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
