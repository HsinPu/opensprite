"""Response quality checks for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .execution import ExecutionResult
from .resource_index import ResourceIndex
from .task_contract import AcceptanceCriterion, TaskContract, TaskContractService
from .task_intent import TaskIntent


_MEDIA_ARTIFACT_KINDS = frozenset({"image_text", "image_analysis", "audio_transcript", "video_analysis"})


@dataclass(frozen=True)
class QualityGateResult:
    """Verdict for deterministic response-quality checks."""

    passed: bool
    reason: str = ""
    status: str = "complete"
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
        contract = task_contract or execution_result.task_contract or TaskContractService.build(
            task_intent=task_intent,
            current_message=task_intent.objective,
        )
        artifact_result = _evaluate_media_artifacts(contract, execution_result)
        if artifact_result is not None:
            return artifact_result
        for criterion in contract.acceptance_criteria:
            if criterion.kind == "itemized_output":
                result = _evaluate_itemized_output(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif criterion.kind == "substantive_final_answer":
                result = _evaluate_substantive_final_answer(criterion, response_text)
                if result is not None:
                    return result
        return QualityGateResult(passed=True)


def _evaluate_itemized_output(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if execution_result.executed_tool_calls > 0:
        return None
    normalized = re.sub(r"\s+", " ", (response_text or "").strip())
    max_response_chars = max(0, int(getattr(criterion, "max_response_chars", 0) or 0))
    if not normalized or (max_response_chars and len(normalized) > max_response_chars):
        return None
    if _response_item_count(response_text) >= max(1, int(getattr(criterion, "min_count", 1) or 1)):
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="assistant did not provide the requested itemized result",
    )


def _evaluate_media_artifacts(contract: TaskContract, execution_result: ExecutionResult) -> QualityGateResult | None:
    if contract.task_type != "media_extraction" or not contract.selected_resources:
        return None
    aliases = ResourceIndex.aliases_for(contract.selected_resources)
    covered = {
        alias
        for artifact in execution_result.task_artifacts
        if artifact.ok and artifact.kind in _MEDIA_ARTIFACT_KINDS
        for resource_id in artifact.resource_ids
        for alias in aliases.get(resource_id, {resource_id})
    }
    missing = tuple(resource.id for resource in contract.selected_resources if resource.id not in covered)
    if not missing:
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="required task artifacts were not produced",
        active_task_detail="\n".join(f"- Missing artifact for {resource_id}" for resource_id in missing),
    )


def _evaluate_substantive_final_answer(
    criterion: AcceptanceCriterion,
    response_text: str,
) -> QualityGateResult | None:
    normalized = re.sub(r"\s+", " ", (response_text or "").strip())
    min_response_chars = max(1, int(getattr(criterion, "min_response_chars", 0) or 1))
    if len(normalized) >= min_response_chars:
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="assistant final answer was too terse for the task",
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _response_item_count(response_text: str) -> int:
    lines = [line.strip() for line in str(response_text or "").splitlines() if line.strip()]
    item_like = 0
    for line in lines:
        if re.match(r"^(?:[-*]|\d+[.)]|\|)", line):
            item_like += 1
    return item_like
