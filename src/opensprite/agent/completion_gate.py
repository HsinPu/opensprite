"""Deterministic completion checks for one agent turn."""

from __future__ import annotations

from typing import Any

from ..config import DocumentLlmConfig
from ..tool_names import BATCH_TOOL_NAME, WORKSPACE_DISCOVERY_TOOL_NAMES
from ..documents.active_task import (
    ACTIVE_ACTIVE_TASK_STATUS,
    BLOCKED_ACTIVE_TASK_STATUS,
    DONE_ACTIVE_TASK_STATUS,
    WAITING_USER_ACTIVE_TASK_STATUS,
)
from .task.capabilities import VERIFICATION_REQUIREMENT_KIND
from .execution import ExecutionResult
from .execution_support.events import is_max_tool_iterations_stop_reason
from .task.contract import (
    TaskIntent,
    accepts_final_response_task_type,
    intent_supports_fallback_active_task_update,
    is_analysis_response_intent_kind,
    is_generic_task_response_intent_kind,
    is_one_turn_intent_kind,
    is_plain_answer_task_type,
    is_read_only_blocking_requirement_kind,
    is_read_only_blocking_tool_name,
    is_read_only_task_type,
)
from .task.contract import (
    PLANNER_BLOCKED_STATUS,
    PLANNER_INVALID_STATUS,
    PLANNER_METADATA_REASON_FIELD,
    TaskContract,
    contract_expects_file_change,
    task_planner_reason,
    task_planner_status,
)
from ..context.message_history import is_history_retrieval_tool_name
from ..tools.evidence import (
    is_fetched_web_source_artifact_tool,
    is_web_discovery_tool,
    is_web_fetch_source_record_tool,
    is_web_research_source_artifact_tool,
    is_web_source_artifact_kind,
)
from ..tools.evidence import (
    REQUIRED_VERIFICATION_FAILED_REASON,
    SKIPPED_VERIFICATION_STATUS,
    VERIFICATION_STATUS_METADATA_FIELD,
    is_verification_result_artifact_kind,
    is_verification_tool_name,
    required_verification_completion_reason,
)

from .completion.status import (
    BLOCKED_COMPLETION_STATUS,
    COMPLETE_COMPLETION_STATUS,
    INCOMPLETE_COMPLETION_STATUS,
    NEEDS_REVIEW_COMPLETION_STATUS,
    NEEDS_VERIFICATION_COMPLETION_STATUS,
    WAITING_USER_COMPLETION_STATUS,
    allows_nonfinal_response_replacement,
    allows_workflow_resume,
    is_blocking_completion_status,
    is_complete_completion_status,
    is_continuable_completion_status,
    is_incomplete_completion_status,
    is_terminal_completion_status,
    needs_review_completion_status,
    needs_verification_completion_status,
    normalize_completion_status,
    requires_evidence_follow_up,
)
from .completion.path_rules import (
    WEB_APP_ROOT_PATH,
    common_verification_path,
    is_python_file_path,
    is_python_test_path,
    is_web_app_path,
    normalized_touched_paths,
    path_requires_delegated_review,
    strip_repo_snapshot_prefix,
)
from .completion.evidence_gate import (
    EvidenceGateResult,
    EvidenceGateService,
)
from .completion.quality_gate import (
    QualityGateResult,
    QualityGateService,
)
from .completion.results import (
    COMPLETION_GATE_DID_NOT_PASS_REASON,
    COMPLETION_RESULT_ACTIVE_TASK_DETAIL_FIELD,
    COMPLETION_RESULT_ACTIVE_TASK_STATUS_FIELD,
    COMPLETION_RESULT_CONFIDENCE_FIELD,
    COMPLETION_RESULT_FILE_CHANGE_REQUIRED_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_PROMPT_TYPE_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_STEP_ID_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_STEP_LABEL_FIELD,
    COMPLETION_RESULT_FOLLOW_UP_WORKFLOW_FIELD,
    COMPLETION_RESULT_ISSUES_FIELD,
    COMPLETION_RESULT_MISSING_EVIDENCE_FIELD,
    COMPLETION_RESULT_NEXT_ACTION_FIELD,
    COMPLETION_RESULT_NEXT_PROMPT_FIELD,
    COMPLETION_RESULT_PROGRESS_ONLY_RESPONSE_FIELD,
    COMPLETION_RESULT_REASON_FIELD,
    COMPLETION_RESULT_REVIEW_ATTEMPTED_FIELD,
    COMPLETION_RESULT_REVIEW_FINDING_COUNT_FIELD,
    COMPLETION_RESULT_REVIEW_PASSED_FIELD,
    COMPLETION_RESULT_REVIEW_REQUIRED_FIELD,
    COMPLETION_RESULT_REVIEW_SUMMARY_FIELD,
    COMPLETION_RESULT_SCHEMA_VERSION_FIELD,
    COMPLETION_RESULT_SHOULD_UPDATE_ACTIVE_TASK_FIELD,
    COMPLETION_RESULT_STATUS_FIELD,
    COMPLETION_RESULT_VERIFICATION_ACTION_FIELD,
    COMPLETION_RESULT_VERIFICATION_ATTEMPTED_FIELD,
    COMPLETION_RESULT_VERIFICATION_PASSED_FIELD,
    COMPLETION_RESULT_VERIFICATION_PATH_FIELD,
    COMPLETION_RESULT_VERIFICATION_PYTEST_ARGS_FIELD,
    COMPLETION_RESULT_VERIFICATION_REQUIRED_FIELD,
    COMPLETION_RESULT_VERIFIER_FIELD,
    CompletionBlockerMessages,
    CompletionGateResult,
    completion_blocker_response,
)
from .completion.response_quality import (
    COMMAND_VERSION_MISSING_REASON,
    ITEMIZED_OUTPUT_MISSING_REASON,
    OPERATION_VALIDATION_OR_RISK_MISSING_REASON,
    TERSE_FINAL_ANSWER_REASON,
    WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON,
    WORKSPACE_LOCATION_MISSING_REASON,
    command_inspects_git_repository_state,
    command_version_follow_up_instruction,
    command_version_missing_detail,
    contains_workspace_location_clue,
    execution_confuses_command_version_with_repo_state,
    execution_has_failed_command_evidence,
    is_command_execution_tool_name,
    is_operations_task_type,
    itemized_output_follow_up_instruction,
    normalized_response_text,
    response_has_minimum_text_length,
    response_item_count,
    response_references_workspace_path,
    response_reports_tool_result_preview,
    workspace_paths,
)
from .completion.value_utils import (
    QUALITY_TRUE_VALUES as _QUALITY_TRUE_VALUES,
    coerce_bool as _coerce_bool,
    coerce_confidence as _coerce_confidence,
    coerce_int as _coerce_int,
    coerce_non_negative_int as _coerce_non_negative_int,
    coerce_text as _coerce_text,
    truncate as _truncate,
)
from .completion.verifier import (
    COMPLETION_VERIFIER_MISSING_CONFIG_REASON,
    COMPLETION_VERIFIER_NEXT_ACTION_ASK_USER,
    COMPLETION_VERIFIER_NEXT_ACTION_CONTINUE_LLM,
    COMPLETION_VERIFIER_NEXT_ACTION_NONE,
    COMPLETION_VERIFIER_NEXT_ACTION_RESUME_WORKFLOW,
    COMPLETION_VERIFIER_NEXT_ACTION_RUN_VERIFICATION,
    COMPLETION_VERIFIER_UNAVAILABLE_REASON,
    CompletionVerifierError,
    CompletionVerifierService,
    CompletionVerifierVerdict,
    build_completion_verifier_facts,
    explicit_verifier_next_action as _explicit_verifier_next_action,
)
from .completion.workflow_gate import (
    REVIEW_EVIDENCE_ATTEMPTED_FIELD,
    REVIEW_EVIDENCE_FINDING_COUNT_FIELD,
    REVIEW_EVIDENCE_PASSED_FIELD,
    REVIEW_EVIDENCE_PROMPT_TYPES_FIELD,
    REVIEW_EVIDENCE_SUMMARY_FIELD,
    _review_evidence,
    _review_follow_up_detail,
    _workflow_gate_outcome,
    _workflow_gate_result_fields,
)

_BLOCKING_PLANNER_STATUSES = frozenset({PLANNER_BLOCKED_STATUS, PLANNER_INVALID_STATUS})
_SKIPPED_VERIFICATION_STATUS = SKIPPED_VERIFICATION_STATUS
_VERIFICATION_STATUS_METADATA_FIELD = VERIFICATION_STATUS_METADATA_FIELD
OPTIONAL_WORKSPACE_BATCH_FAILURE_TOOL = BATCH_TOOL_NAME
MAX_TOOL_ITERATIONS_INCOMPLETE_REASON = "max tool iterations exhausted before completion"
MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL = (
    "The execution loop hit the configured max_tool_iterations limit and needs another bounded continuation pass."
)
INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON = "assistant only emitted internal control text"
TOOL_ERROR_WITHOUT_BLOCKER_REASON = "tool execution reported an error without a clear blocker handoff"
PLAIN_ANSWER_CONTRACT_COMPLETE_REASON = "plain-answer contract received a response"
TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON = "task contract accepted final response"
REQUIRED_FILE_CHANGES_AND_EVIDENCE_RECORDED_REASON = "required file changes and evidence were recorded"
ASSISTANT_RESPONSE_DID_NOT_COMPLETE_REASON = "assistant response did not explicitly complete the task"
GENERIC_TASK_COMPLETE_REASON = "generic task returned a response"
ANALYSIS_TASK_COMPLETE_REASON = "analysis-style task returned a substantive response"
EXPECTED_CODE_CHANGES_MISSING_REASON = "expected code changes were not recorded"
ONE_TURN_RESPONSE_COMPLETE_REASON = "one-turn intent received a response"
EMPTY_ASSISTANT_RESPONSE_REASON = "assistant response was empty"
TASK_CONTRACT_SATISFIED_REASON = "task contract was satisfied"
TASK_CONTRACT_PLANNER_UNVALIDATED_REASON = "task planner did not produce a validated contract"
DELEGATED_REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON = "delegated review reported findings that require follow-up"
DELEGATED_REVIEW_NOT_RECORDED_REASON = "delegated review was not recorded for code changes"


def one_turn_completion_reason(*, has_response: bool) -> str:
    return ONE_TURN_RESPONSE_COMPLETE_REASON if has_response else EMPTY_ASSISTANT_RESPONSE_REASON


def delegated_review_completion_reason(*, review_attempted: bool) -> str:
    return DELEGATED_REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON if review_attempted else DELEGATED_REVIEW_NOT_RECORDED_REASON


class CompletionGateService:
    """Evaluate completion without calling the LLM or continuing autonomously."""

    def __init__(
        self,
        *,
        llm_config: DocumentLlmConfig | None = None,
        verifier_service: CompletionVerifierService | None = None,
        evidence_gate: EvidenceGateService | None = None,
        quality_gate: QualityGateService | None = None,
    ):
        self.llm_config = llm_config
        self.verifier_service = verifier_service or (
            CompletionVerifierService(llm_config) if llm_config is not None else None
        )
        self.evidence_gate = evidence_gate or EvidenceGateService()
        self.quality_gate = quality_gate or QualityGateService()

    async def evaluate_with_verifier(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
        user_message_text: str = "",
        provider: Any,
        model: str | None,
    ) -> CompletionGateResult:
        """Return the LLM verifier verdict for the current turn."""
        verifier = self.verifier_service
        if verifier is None:
            return _completion_gate_blocked_result(COMPLETION_VERIFIER_MISSING_CONFIG_REASON)
        facts = build_completion_verifier_facts(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            user_message_text=user_message_text,
        )
        try:
            verdict = await verifier.verify(provider=provider, model=model, facts=facts)
        except CompletionVerifierError as exc:
            return _completion_gate_blocked_result(str(exc))
        except Exception as exc:
            return _completion_gate_blocked_result(f"completion verifier failed: {type(exc).__name__}")
        result = _completion_result_from_verifier_verdict(verdict, execution_result=execution_result)
        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=result.verification_passed,
        )
        if is_complete_completion_status(result.status) and not evidence_result.passed:
            return _completion_result_with_evidence_failure(result, evidence_result)
        if (
            result.status == BLOCKED_COMPLETION_STATUS
            and not evidence_result.passed
            and execution_result.executed_tool_calls <= 0
            and not execution_result.had_tool_error
        ):
            return _completion_result_with_evidence_failure(result, evidence_result)
        return result

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
    ) -> CompletionGateResult:
        """Return the safest completion verdict for the current turn."""
        contract_allows_plain_answer = _contract_allows_plain_answer(execution_result.task_contract)
        verification_required = (
            False if contract_allows_plain_answer else _contract_requires_verification(execution_result.task_contract)
        )
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
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
                file_change_required=True,
            )

        planner_status = task_planner_status(execution_result.task_contract)
        if _is_blocking_planner_status(planner_status):
            reason = TASK_CONTRACT_PLANNER_UNVALIDATED_REASON
            detail = task_planner_reason(execution_result.task_contract) or reason
            return CompletionGateResult(
                status=BLOCKED_COMPLETION_STATUS,
                reason=reason,
                active_task_status=BLOCKED_ACTIVE_TASK_STATUS,
                active_task_detail=detail,
                should_update_active_task=intent_supports_fallback_active_task_update(
                    task_intent,
                    execution_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if is_max_tool_iterations_stop_reason(execution_result.stop_reason):
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=MAX_TOOL_ITERATIONS_INCOMPLETE_REASON,
                active_task_detail=MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if execution_result.had_tool_error:
            if verification_required and verification_attempted and not verification_passed:
                return CompletionGateResult(
                    status=NEEDS_VERIFICATION_COMPLETION_STATUS,
                    reason=REQUIRED_VERIFICATION_FAILED_REASON,
                    verification_required=True,
                    verification_attempted=True,
                    verification_passed=False,
                    verification_action=verification_follow_up["action"],
                    verification_path=verification_follow_up["path"],
                    verification_pytest_args=verification_follow_up["pytest_args"],
                    review_required=review_required,
                    review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                    review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                    review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                    review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                    review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
                )
            if not self._tool_errors_are_non_blocking(
                task_intent=task_intent,
                response_text=response_text,
                execution_result=execution_result,
                verification_passed=verification_passed,
            ):
                return CompletionGateResult(
                    status=INCOMPLETE_COMPLETION_STATUS,
                    reason=TOOL_ERROR_WITHOUT_BLOCKER_REASON,
                    verification_required=verification_required,
                    verification_attempted=verification_attempted,
                    verification_passed=verification_passed,
                    review_required=review_required,
                    review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                    review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                    review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                    review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                    review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
                )

        if workflow_gate is not None:
            return CompletionGateResult(
                **_workflow_gate_result_fields(
                    workflow_gate,
                    task_intent=task_intent,
                    task_contract=execution_result.task_contract,
                    verification_required=verification_required,
                    verification_attempted=verification_attempted,
                    verification_passed=verification_passed,
                    verification_follow_up=verification_follow_up,
                    review_required=review_required,
                    review=review,
                )
            )

        if (
            contract_allows_plain_answer
            and not _contract_has_completion_criteria(execution_result.task_contract)
            and response_text.strip()
        ):
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=PLAIN_ANSWER_CONTRACT_COMPLETE_REASON,
                active_task_status=(
                    DONE_ACTIVE_TASK_STATUS
                    if intent_supports_fallback_active_task_update(task_intent, execution_result.task_contract)
                    else None
                ),
                should_update_active_task=intent_supports_fallback_active_task_update(
                    task_intent,
                    execution_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if expects_code_change and execution_result.file_change_count <= 0:
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=EXPECTED_CODE_CHANGES_MISSING_REASON,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=False,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if verification_required and not verification_passed:
            reason = required_verification_completion_reason(verification_attempted=verification_attempted)
            return CompletionGateResult(
                status=NEEDS_VERIFICATION_COMPLETION_STATUS,
                reason=reason,
                verification_required=True,
                verification_attempted=verification_attempted,
                verification_passed=False,
                verification_action=verification_follow_up["action"],
                verification_path=verification_follow_up["path"],
                verification_pytest_args=verification_follow_up["pytest_args"],
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if review_required and not review[REVIEW_EVIDENCE_PASSED_FIELD]:
            reason = delegated_review_completion_reason(review_attempted=bool(review[REVIEW_EVIDENCE_ATTEMPTED_FIELD]))
            return CompletionGateResult(
                status=NEEDS_REVIEW_COMPLETION_STATUS,
                reason=reason,
                active_task_detail=_review_follow_up_detail(review),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=True,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=False,
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=verification_passed,
        )
        if not evidence_result.passed:
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=evidence_result.reason,
                active_task_detail=evidence_result.active_task_detail,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
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
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if _contract_accepts_final_response(evidence_result.task_contract) and response_text.strip():
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS,
                should_update_active_task=True,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if is_one_turn_intent_kind(task_intent.kind):
            has_response = bool(response_text.strip())
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS if has_response else INCOMPLETE_COMPLETION_STATUS,
                reason=one_turn_completion_reason(has_response=has_response),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if is_analysis_response_intent_kind(task_intent.kind) and response_text.strip():
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=ANALYSIS_TASK_COMPLETE_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS,
                should_update_active_task=intent_supports_fallback_active_task_update(
                    task_intent,
                    evidence_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if is_generic_task_response_intent_kind(task_intent.kind) and not expects_code_change and response_text.strip():
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=GENERIC_TASK_COMPLETE_REASON,
                active_task_status=(
                    DONE_ACTIVE_TASK_STATUS
                    if intent_supports_fallback_active_task_update(task_intent, evidence_result.task_contract)
                    else None
                ),
                should_update_active_task=intent_supports_fallback_active_task_update(
                    task_intent,
                    evidence_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if _contract_has_completion_criteria(evidence_result.task_contract) and response_text.strip():
            should_update_active_task = intent_supports_fallback_active_task_update(
                task_intent,
                evidence_result.task_contract,
            )
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=TASK_CONTRACT_SATISFIED_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS if should_update_active_task else None,
                should_update_active_task=should_update_active_task,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if (
            expects_code_change
            and execution_result.file_change_count > 0
            and response_text.strip()
            and (not verification_required or verification_passed)
            and (not review_required or review[REVIEW_EVIDENCE_PASSED_FIELD])
        ):
            should_update_active_task = not task_intent.needs_clarification
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=REQUIRED_FILE_CHANGES_AND_EVIDENCE_RECORDED_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS if should_update_active_task else None,
                should_update_active_task=should_update_active_task,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        return CompletionGateResult(
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=ASSISTANT_RESPONSE_DID_NOT_COMPLETE_REASON,
            verification_required=verification_required,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
            review_required=review_required,
            review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
            review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
            review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
            review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
            review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
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
        has_optional_web_failures = has_only_optional_web_discovery_failures(execution_result)
        has_optional_workspace_failures = has_only_optional_workspace_discovery_failures(execution_result)
        has_optional_history_failures = has_only_optional_history_retrieval_failures(execution_result)
        if not (has_optional_web_failures or has_optional_workspace_failures or has_optional_history_failures):
            return False

        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=verification_passed,
        )
        if not evidence_result.passed:
            return False

        if has_optional_web_failures and not has_successful_fetched_web_source_artifact(execution_result):
            return False

        quality_result = self.quality_gate.evaluate(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            task_contract=evidence_result.task_contract,
        )
        return quality_result.passed


def _completion_result_with_evidence_failure(
    result: CompletionGateResult,
    evidence_result: EvidenceGateResult,
) -> CompletionGateResult:
    return CompletionGateResult(
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=evidence_result.reason,
        confidence=result.confidence,
        issues=result.issues,
        next_action=_next_action_for_evidence_failure(result),
        next_prompt=result.next_prompt,
        active_task_detail=evidence_result.active_task_detail,
        verification_required=result.verification_required,
        verification_attempted=result.verification_attempted,
        verification_passed=result.verification_passed,
        verification_action=result.verification_action,
        verification_path=result.verification_path,
        verification_pytest_args=result.verification_pytest_args,
        review_required=result.review_required,
        review_attempted=result.review_attempted,
        review_passed=result.review_passed,
        review_summary=result.review_summary,
        review_prompt_types=result.review_prompt_types,
        review_finding_count=result.review_finding_count,
        missing_evidence=evidence_result.missing_evidence,
        progress_only_response=result.progress_only_response,
        verifier_metadata=result.verifier_metadata,
    )


def _next_action_for_evidence_failure(result: CompletionGateResult) -> str:
    if result.verification_required and not result.verification_passed and result.verification_action:
        return COMPLETION_VERIFIER_NEXT_ACTION_RUN_VERIFICATION
    return COMPLETION_VERIFIER_NEXT_ACTION_CONTINUE_LLM


def _completion_result_from_verifier_verdict(
    verdict: CompletionVerifierVerdict,
    *,
    execution_result: ExecutionResult,
) -> CompletionGateResult:
    file_change_required = (
        contract_expects_file_change(execution_result.task_contract)
        and execution_result.file_change_count <= 0
    )
    status = verdict.status
    active_task_status = verdict.active_task_status
    should_update_active_task = bool(active_task_status)
    if verdict.review_required and not verdict.review_passed and is_complete_completion_status(status):
        status = NEEDS_REVIEW_COMPLETION_STATUS
        active_task_status = None
        should_update_active_task = False
    active_task_detail = verdict.active_task_detail
    if verdict.next_action == COMPLETION_VERIFIER_NEXT_ACTION_ASK_USER:
        status = WAITING_USER_COMPLETION_STATUS
        active_task_status = WAITING_USER_ACTIVE_TASK_STATUS
        should_update_active_task = True
        active_task_detail = active_task_detail or verdict.next_prompt or verdict.reason
    return CompletionGateResult(
        status=status,
        reason=verdict.reason,
        confidence=verdict.confidence,
        issues=verdict.issues,
        next_action=verdict.next_action,
        next_prompt=verdict.next_prompt,
        active_task_status=active_task_status,
        active_task_detail=active_task_detail,
        follow_up_workflow=verdict.follow_up_workflow,
        follow_up_step_id=verdict.follow_up_step_id,
        follow_up_step_label=verdict.follow_up_step_label,
        follow_up_prompt_type=verdict.follow_up_prompt_type,
        verification_action=verdict.verification_action,
        verification_path=verdict.verification_path,
        verification_pytest_args=verdict.verification_pytest_args,
        should_update_active_task=should_update_active_task,
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
        verifier_metadata={
            **dict(verdict.metadata),
            "raw_response_preview": verdict.raw_response_preview,
        },
    )


def _completion_gate_blocked_result(reason: str) -> CompletionGateResult:
    detail = reason or COMPLETION_VERIFIER_UNAVAILABLE_REASON
    return CompletionGateResult(
        status=BLOCKED_COMPLETION_STATUS,
        reason=detail,
        active_task_status=BLOCKED_ACTIVE_TASK_STATUS,
        active_task_detail=detail,
        should_update_active_task=True,
        verifier_metadata={
            "method": "llm",
            "role": "verifier",
            "error": detail,
        },
    )


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
        if not is_verification_result_artifact_kind(artifact.kind) or not artifact.ok:
            continue
        if _verification_status_is_skipped(artifact.metadata):
            return True
    for evidence in execution_result.tool_evidence:
        if not is_verification_tool_name(evidence.name) or not evidence.ok:
            continue
        if _verification_status_is_skipped(evidence.metadata):
            return True
    return False


def _verification_status_is_skipped(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get(_VERIFICATION_STATUS_METADATA_FIELD) or "").strip().lower() == _SKIPPED_VERIFICATION_STATUS


def _requires_delegated_review(touched_paths: tuple[str, ...]) -> bool:
    paths = normalized_touched_paths(touched_paths)
    if not paths:
        return True
    return any(path_requires_delegated_review(path) for path in paths)


def _contract_requires_verification(task_contract: Any) -> bool:
    if any(
        str(getattr(requirement, "kind", "") or "").strip() == VERIFICATION_REQUIREMENT_KIND
        for requirement in getattr(task_contract, "requirements", ()) or ()
    ):
        return True
    return any(
        is_verification_tool_name(tool_name)
        for tool_name in getattr(task_contract, "required_tools", ()) or ()
    )


def _contract_allows_plain_answer(task_contract: Any) -> bool:
    return bool(
        task_contract is not None
        and is_plain_answer_task_type(getattr(task_contract, "task_type", None))
        and getattr(task_contract, "allow_no_tool_final", False)
        and not tuple(getattr(task_contract, "requirements", ()) or ())
    )


def _contract_is_read_only(task_contract: Any) -> bool:
    task_type = str(getattr(task_contract, "task_type", "") or "")
    if is_read_only_task_type(task_type):
        return True
    for requirement in getattr(task_contract, "requirements", ()) or ():
        if is_read_only_blocking_requirement_kind(str(getattr(requirement, "kind", "") or "")):
            return False
    for tool_name in getattr(task_contract, "required_tools", ()) or ():
        if is_read_only_blocking_tool_name(tool_name):
            return False
    return False


def _is_blocking_planner_status(status: str | None) -> bool:
    return str(status or "").strip().lower() in _BLOCKING_PLANNER_STATUSES


def has_only_optional_web_discovery_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    has_successful_fetch_sources = has_successful_fetched_web_source_artifact(execution_result)
    for item in failed_evidence:
        if is_web_discovery_tool(item.name):
            continue
        if is_web_fetch_source_record_tool(item.name) and has_successful_fetch_sources:
            continue
        return False
    return True


def has_only_optional_workspace_discovery_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    if not any(item.ok and item.name in WORKSPACE_DISCOVERY_TOOL_NAMES for item in execution_result.tool_evidence):
        return False
    for item in failed_evidence:
        if item.name in WORKSPACE_DISCOVERY_TOOL_NAMES:
            continue
        if is_optional_workspace_batch_failure_tool(item.name) and execution_result.file_change_count <= 0:
            continue
        return False
    return True


def has_only_optional_history_retrieval_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    if not any(item.ok and is_history_retrieval_tool_name(item.name) for item in execution_result.tool_evidence):
        return False
    for item in failed_evidence:
        if is_history_retrieval_tool_name(item.name):
            continue
        return False
    return True


def has_successful_fetched_web_source_artifact(execution_result: ExecutionResult) -> bool:
    for artifact in execution_result.task_artifacts:
        if not is_web_source_artifact_kind(artifact.kind) or not artifact.ok:
            continue
        sources = artifact.metadata.get("sources") if isinstance(artifact.metadata, dict) else None
        if is_fetched_web_source_artifact_tool(artifact.source_tool) and isinstance(sources, list) and sources:
            return True
        if (
            is_web_research_source_artifact_tool(artifact.source_tool)
            and web_research_artifact_has_successful_fetch(artifact)
        ):
            return True
    return False


def is_optional_workspace_batch_failure_tool(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() == OPTIONAL_WORKSPACE_BATCH_FAILURE_TOOL


def web_research_artifact_has_successful_fetch(artifact: TaskArtifact) -> bool:
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
        if not is_web_fetch_source_record_tool(source.get("tool_name")):
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
    return accepts_final_response_task_type(task_type)


def _verification_follow_up_fields(
    action: str,
    path: str | None,
    *,
    pytest_args: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "action": action,
        "path": path or ".",
        "pytest_args": pytest_args,
    }


def _verification_follow_up(execution_result: ExecutionResult) -> dict[str, Any]:
    touched_paths = normalized_touched_paths(execution_result.touched_paths)
    decision_paths = tuple(strip_repo_snapshot_prefix(path) for path in touched_paths)
    test_paths = tuple(path for path in decision_paths if is_python_test_path(path))
    has_web_touched = any(is_web_app_path(path) for path in decision_paths)
    has_python_touched = any(is_python_file_path(path) for path in decision_paths)
    if touched_paths and not has_web_touched and not has_python_touched:
        return _verification_follow_up_fields("auto", common_verification_path(touched_paths))
    if has_web_touched:
        return _verification_follow_up_fields("web_build", WEB_APP_ROOT_PATH)
    if test_paths:
        return _verification_follow_up_fields("pytest", ".", pytest_args=test_paths)
    if has_python_touched:
        return _verification_follow_up_fields("python_compile", common_verification_path(touched_paths))
    return _verification_follow_up_fields("auto", common_verification_path(touched_paths))


def _truthy(value: object) -> bool:
    return _coerce_bool(value, truthy_values=_QUALITY_TRUE_VALUES)
