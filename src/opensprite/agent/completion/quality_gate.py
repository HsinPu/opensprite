"""Deterministic answer-quality checks for completion gating."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...context.message_history import (
    HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON,
    history_retrieval_metadata_has_results,
    history_retrieval_metadata_reports_empty,
    is_history_retrieval_tool_name,
)
from ...media import count_media_artifacts, is_media_artifact_kind
from ...tools.evidence import (
    GATHERED_SOURCE_REFERENCE_MISSING_REASON,
    SOURCE_ARTIFACTS_NOT_TRACEABLE_REASON,
    SOURCE_MATERIAL_INSUFFICIENT_REASON,
    UNGATHERED_SOURCE_REFERENCED_REASON,
    VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON,
    is_web_source_artifact_kind,
    ungrounded_response_source_urls,
    web_source_has_substantive_detail,
    web_source_is_referenced,
)
from ..execution import ExecutionResult
from ..execution_support.artifacts import TASK_ARTIFACTS_NOT_PRODUCED_REASON
from ..task.contract import (
    AcceptanceCriterion,
    COMMAND_VERSION_QUALITY_CHECK,
    TaskContract,
    TaskIntent,
    is_history_retrieval_task_type,
    is_itemized_output_criterion,
    is_media_artifact_criterion,
    is_media_extraction_task_type,
    is_operation_report_criterion,
    is_source_artifact_criterion,
    is_source_detail_criterion,
    is_source_reference_criterion,
    is_substantive_final_answer_criterion,
    is_verification_or_gap_criterion,
    is_workspace_location_criterion,
    is_workspace_read_task_type,
    neutral_task_contract,
)
from ..task.resources import ResourceIndex
from .response_quality import (
    COMMAND_VERSION_MISSING_REASON,
    ITEMIZED_OUTPUT_MISSING_REASON,
    OPERATION_VALIDATION_OR_RISK_MISSING_REASON,
    TERSE_FINAL_ANSWER_REASON,
    WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON,
    WORKSPACE_LOCATION_MISSING_REASON,
    command_version_missing_detail,
    contains_workspace_location_clue,
    execution_confuses_command_version_with_repo_state,
    execution_has_failed_command_evidence,
    is_operations_task_type,
    normalized_response_text,
    response_has_minimum_text_length,
    response_item_count,
    response_references_workspace_path,
    response_reports_tool_result_preview,
    workspace_paths,
)
from .source_material import (
    execution_web_sources,
    web_research_coverage_gap_detail,
)
from .status import (
    COMPLETE_COMPLETION_STATUS,
    INCOMPLETE_COMPLETION_STATUS,
    NEEDS_VERIFICATION_COMPLETION_STATUS,
)
from .value_utils import coerce_int as _coerce_int


@dataclass(frozen=True)
class QualityGateResult:
    """Verdict for deterministic response-quality checks."""

    passed: bool
    reason: str = ""
    status: str = COMPLETE_COMPLETION_STATUS
    active_task_detail: str | None = None


class QualityGateService:
    """Evaluate answer-shape quality rules that are independent of tool evidence."""

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
        task_contract: TaskContract | None = None,
    ) -> QualityGateResult:
        contract = task_contract or execution_result.task_contract or neutral_task_contract(task_intent)
        artifact_result = _evaluate_media_artifacts(contract, execution_result)
        if artifact_result is not None:
            return artifact_result
        command_version_result = _evaluate_command_version_answer(contract, response_text, execution_result)
        if command_version_result is not None:
            return command_version_result
        if is_history_retrieval_task_type(contract.task_type) and _history_retrieval_was_empty(execution_result):
            history_result = _evaluate_history_grounding(contract, response_text, execution_result)
            if history_result is not None:
                return history_result
        for criterion in contract.acceptance_criteria:
            if is_itemized_output_criterion(criterion):
                result = _evaluate_itemized_output(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif is_substantive_final_answer_criterion(criterion):
                result = _evaluate_substantive_final_answer(criterion, response_text)
                if result is not None:
                    return result
            elif is_source_artifact_criterion(criterion):
                result = _evaluate_source_artifact(criterion, execution_result)
                if result is not None:
                    return result
            elif is_source_detail_criterion(criterion):
                result = _evaluate_source_detail(criterion, execution_result)
                if result is not None:
                    return result
            elif is_source_reference_criterion(criterion):
                result = _evaluate_source_reference(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif is_media_artifact_criterion(criterion):
                result = _evaluate_media_artifact_criterion(criterion, contract, execution_result)
                if result is not None:
                    return result
            elif is_verification_or_gap_criterion(criterion):
                result = _evaluate_verification_or_gap(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif is_operation_report_criterion(criterion):
                result = _evaluate_operation_report(criterion, response_text, execution_result)
                if result is not None:
                    return result
        workspace_result = _evaluate_workspace_grounding(contract, response_text)
        if workspace_result is not None:
            return workspace_result
        history_result = _evaluate_history_grounding(contract, response_text, execution_result)
        if history_result is not None:
            return history_result
        return QualityGateResult(passed=True)


def _evaluate_itemized_output(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if execution_result.executed_tool_calls > 0:
        return None
    normalized = normalized_response_text(response_text)
    max_response_chars = max(0, int(getattr(criterion, "max_response_chars", 0) or 0))
    if not normalized or (max_response_chars and len(normalized) > max_response_chars):
        return None
    if response_item_count(response_text) >= max(1, int(getattr(criterion, "min_count", 1) or 1)):
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=ITEMIZED_OUTPUT_MISSING_REASON,
    )


def _evaluate_media_artifacts(contract: TaskContract, execution_result: ExecutionResult) -> QualityGateResult | None:
    if not is_media_extraction_task_type(contract.task_type) or not contract.selected_resources:
        return None
    aliases = ResourceIndex.aliases_for(contract.selected_resources)
    covered = {
        alias
        for artifact in execution_result.task_artifacts
        if artifact.ok and is_media_artifact_kind(artifact.kind)
        for resource_id in artifact.resource_ids
        for alias in aliases.get(resource_id, {resource_id})
    }
    missing = tuple(resource.id for resource in contract.selected_resources if resource.id not in covered)
    if not missing:
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TASK_ARTIFACTS_NOT_PRODUCED_REASON,
        active_task_detail="\n".join(f"- Missing artifact for {resource_id}" for resource_id in missing),
    )


def _evaluate_substantive_final_answer(
    criterion: AcceptanceCriterion,
    response_text: str,
) -> QualityGateResult | None:
    min_response_chars = max(1, int(getattr(criterion, "min_response_chars", 0) or 1))
    if response_has_minimum_text_length(response_text, min_response_chars):
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TERSE_FINAL_ANSWER_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_source_artifact(
    criterion: AcceptanceCriterion,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    artifact_count = sum(
        1
        for artifact in execution_result.task_artifacts
        if artifact.ok and is_web_source_artifact_kind(artifact.kind)
    )
    traceable_count = len(execution_web_sources(execution_result))
    if traceable_count >= min_count:
        return None
    if artifact_count > 0:
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=SOURCE_ARTIFACTS_NOT_TRACEABLE_REASON,
            active_task_detail=(
                "- Missing traceable source metadata: url plus title/snippet "
                f"(need {min_count}, found {traceable_count})"
            ),
        )
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TASK_ARTIFACTS_NOT_PRODUCED_REASON,
        active_task_detail=f"- Missing source artifact: web_source (need {min_count}, found {artifact_count})",
    )


def _evaluate_source_detail(
    criterion: AcceptanceCriterion,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    sources = execution_web_sources(execution_result)
    if not sources:
        return None
    detailed_count = sum(1 for source in sources if web_source_has_substantive_detail(source))
    if detailed_count >= min_count:
        coverage_detail = web_research_coverage_gap_detail(execution_result)
        if coverage_detail is None:
            return None
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=SOURCE_MATERIAL_INSUFFICIENT_REASON,
            active_task_detail=coverage_detail,
        )
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=SOURCE_MATERIAL_INSUFFICIENT_REASON,
        active_task_detail=(
            "- Fetch or inspect at least one source page before finalizing; "
            "search snippets and too-short fetches do not count "
            f"(need {min_count}, found {detailed_count})"
        ),
    )


def _evaluate_source_reference(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    sources = execution_web_sources(execution_result)
    if not sources:
        return None
    ungrounded_urls = ungrounded_response_source_urls(response_text, sources)
    if ungrounded_urls:
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=UNGATHERED_SOURCE_REFERENCED_REASON,
            active_task_detail=(
                "- Remove or verify source URLs that were not gathered in this run: "
                + ", ".join(ungrounded_urls[:3])
            ),
        )
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    referenced_count = sum(1 for source in sources if web_source_is_referenced(source, response_text))
    if referenced_count >= min_count:
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=GATHERED_SOURCE_REFERENCE_MISSING_REASON,
        active_task_detail=(
            "- Reference at least one gathered source by URL, domain, or title "
            f"(need {min_count}, found {referenced_count})"
        ),
    )


def _evaluate_media_artifact_criterion(
    criterion: AcceptanceCriterion,
    contract: TaskContract,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if not contract.selected_resources:
        return None
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    artifact_count = count_media_artifacts(execution_result.task_artifacts)
    if artifact_count >= min_count:
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TASK_ARTIFACTS_NOT_PRODUCED_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_verification_or_gap(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    del response_text
    if execution_result.file_change_count <= 0 or execution_result.verification_attempted:
        return None
    return QualityGateResult(
        passed=False,
        status=NEEDS_VERIFICATION_COMPLETION_STATUS,
        reason=VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_operation_report(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if any(evidence.ok for evidence in execution_result.tool_evidence):
        return None
    if _response_reports_tool_result(response_text, execution_result):
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=OPERATION_VALIDATION_OR_RISK_MISSING_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def contract_requests_quality_check(contract: TaskContract, check_name: str) -> bool:
    metadata = contract.planner_metadata or {}
    raw_checks = metadata.get("quality_checks")
    if isinstance(raw_checks, str):
        checks = (raw_checks,)
    elif isinstance(raw_checks, list | tuple | set):
        checks = tuple(str(item) for item in raw_checks)
    else:
        checks = ()
    return check_name in {item.strip() for item in checks if item.strip()}


def _evaluate_command_version_answer(
    contract: TaskContract,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if not is_operations_task_type(contract.task_type):
        return None
    if not contract_requests_quality_check(contract, COMMAND_VERSION_QUALITY_CHECK):
        return None
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None
    if execution_has_failed_command_evidence(execution_result):
        return None
    if _response_reports_tool_result(response_text, execution_result):
        return None
    detail = command_version_missing_detail(
        inspected_repository_state=execution_confuses_command_version_with_repo_state(execution_result)
    )
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=COMMAND_VERSION_MISSING_REASON,
        active_task_detail=detail,
    )


def _response_reports_tool_result(response_text: str, execution_result: ExecutionResult) -> bool:
    if not str(response_text or "").strip():
        return False
    for evidence in execution_result.tool_evidence:
        if not evidence.ok:
            continue
        if response_reports_tool_result_preview(response_text, evidence.result_preview):
            return True
    for artifact in execution_result.task_artifacts:
        if not artifact.ok:
            continue
        if response_reports_tool_result_preview(response_text, artifact.content_preview):
            return True
    return False


def _evaluate_workspace_grounding(contract: TaskContract, response_text: str) -> QualityGateResult | None:
    if not is_workspace_read_task_type(contract.task_type):
        return None
    objective = str(contract.objective or "")
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None

    requested_paths = workspace_paths(objective)
    if requested_paths and not any(response_references_workspace_path(path, normalized_response) for path in requested_paths):
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON,
            active_task_detail="- Reference the inspected workspace path or filename in the final answer.",
        )

    requires_location = any(
        is_workspace_location_criterion(criterion) for criterion in contract.acceptance_criteria
    )
    if requires_location and not contains_workspace_location_clue(
        normalized_response,
        has_workspace_path=bool(workspace_paths(normalized_response)),
    ):
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=WORKSPACE_LOCATION_MISSING_REASON,
            active_task_detail="- Include a file path, symbol, or matching config/code clue from the workspace inspection.",
        )
    return None


def _evaluate_history_grounding(
    contract: TaskContract,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if not is_history_retrieval_task_type(contract.task_type):
        return None
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None

    if _history_retrieval_was_empty(execution_result):
        return None

    requested_count = _history_itemized_min_count(contract)
    if requested_count > 1 and response_item_count(response_text) < requested_count:
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON,
            active_task_detail=f"- Provide at least {requested_count} recalled item(s) from the retrieved context.",
        )
    return None


def media_artifact_gap_detail(contract: TaskContract, execution_result: ExecutionResult) -> str | None:
    """Return the missing media artifact detail for a contract, when available."""
    result = _evaluate_media_artifacts(contract, execution_result)
    if result is not None:
        return result.active_task_detail or result.reason
    for criterion in contract.acceptance_criteria:
        if not is_media_artifact_criterion(criterion):
            continue
        result = _evaluate_media_artifact_criterion(criterion, contract, execution_result)
        if result is not None:
            return result.active_task_detail or result.reason
    return None


def _history_retrieval_was_empty(execution_result: ExecutionResult) -> bool:
    evidence = [
        item
        for item in execution_result.tool_evidence
        if item.ok and is_history_retrieval_tool_name(item.name)
    ]
    if not evidence:
        return False
    saw_explicit_empty = False
    for item in evidence:
        if history_retrieval_metadata_has_results(item.metadata):
            return False
        if history_retrieval_metadata_reports_empty(item.metadata):
            saw_explicit_empty = True
    return saw_explicit_empty


def _history_itemized_min_count(contract: TaskContract) -> int:
    counts = [
        _coerce_int(getattr(criterion, "min_count", 0), default=0)
        for criterion in contract.acceptance_criteria
        if is_itemized_output_criterion(criterion)
    ]
    return max(counts, default=0)
