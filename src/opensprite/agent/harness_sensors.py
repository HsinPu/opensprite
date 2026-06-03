"""Deterministic harness sensor checks for scorecards."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from .completion_status import is_complete_completion_status
from .harness_inventory import expected_sensor_ids_for_task_type
from .harness_scorecard import HarnessSensorResult
from .media_artifact_policy import count_media_artifacts
from .web_source_policy import is_web_source_artifact_kind, is_web_source_evidence_tool

if TYPE_CHECKING:
    from .completion_gate import CompletionGateResult
    from .execution import ExecutionResult


def evaluate_harness_sensors(
    *,
    task_type: str,
    execution_result: ExecutionResult,
    completion_result: CompletionGateResult,
) -> tuple[HarnessSensorResult, ...]:
    """Evaluate the expected sensors for a harness task type."""
    sensor_ids = expected_sensor_ids_for_task_type(task_type)
    return tuple(
        _evaluate_sensor(sensor_id, execution_result=execution_result, completion_result=completion_result)
        for sensor_id in sensor_ids
    )


def _evaluate_sensor(
    sensor_id: str,
    *,
    execution_result: ExecutionResult,
    completion_result: CompletionGateResult,
) -> HarnessSensorResult:
    if sensor_id == "chat.no_unexpected_tools":
        count = execution_result.executed_tool_calls
        return HarnessSensorResult(
            sensor_id,
            "pass" if count == 0 else "warn",
            "No tools were needed." if count == 0 else "Conversation turn used tools.",
            {"executed_tool_calls": count},
        )
    if sensor_id == "completion.final_answer":
        return _completion_sensor(sensor_id, completion_result)
    if sensor_id == "research.source_coverage":
        count = _artifact_count_matching(execution_result, is_web_source_artifact_kind)
        return HarnessSensorResult(
            sensor_id,
            "pass" if count > 0 else "fail",
            "Traceable web sources were recorded." if count > 0 else "No traceable web source artifact was recorded.",
            {"web_source_artifacts": count},
        )
    if sensor_id == "research.freshness":
        evidence_count = _web_tool_evidence_count(execution_result)
        return HarnessSensorResult(
            sensor_id,
            "pass" if evidence_count > 0 else "warn",
            "Live web evidence is present." if evidence_count > 0 else "No live web evidence was found.",
            {"web_tool_evidence": evidence_count},
        )
    if sensor_id == "completion.source_grounding":
        return _missing_evidence_sensor(sensor_id, completion_result)
    if sensor_id == "coding.workspace_evidence":
        evidence_count = len(execution_result.tool_evidence)
        return HarnessSensorResult(
            sensor_id,
            "pass" if evidence_count > 0 else "warn",
            "Workspace evidence was gathered." if evidence_count > 0 else "No workspace evidence was recorded.",
            {"tool_evidence": evidence_count},
        )
    if sensor_id == "coding.file_change":
        count = execution_result.file_change_count
        return HarnessSensorResult(
            sensor_id,
            "pass" if count > 0 else "fail",
            "File changes were recorded." if count > 0 else "No file changes were recorded.",
            {"file_change_count": count},
        )
    if sensor_id == "coding.verification":
        return HarnessSensorResult(
            sensor_id,
            "pass" if execution_result.verification_passed else "warn",
            "Verification passed." if execution_result.verification_passed else "Verification did not pass.",
            {
                "verification_attempted": execution_result.verification_attempted,
                "verification_passed": execution_result.verification_passed,
            },
        )
    if sensor_id == "completion.change_summary":
        return _completion_sensor(sensor_id, completion_result)
    if sensor_id == "completion.verification_or_gap":
        return _missing_evidence_sensor(sensor_id, completion_result)
    if sensor_id == "media.artifact":
        count = count_media_artifacts(execution_result.task_artifacts)
        return HarnessSensorResult(
            sensor_id,
            "pass" if count > 0 else "fail",
            "Media artifacts were recorded." if count > 0 else "No media artifact was recorded.",
            {"media_artifacts": count},
        )
    if sensor_id == "completion.media_summary":
        return _completion_sensor(sensor_id, completion_result)
    if sensor_id == "ops.audit_trace":
        return HarnessSensorResult(
            sensor_id,
            "pass" if execution_result.executed_tool_calls > 0 else "warn",
            "Operational tool activity was recorded." if execution_result.executed_tool_calls > 0 else "No operational tool activity was recorded.",
            {"executed_tool_calls": execution_result.executed_tool_calls},
        )
    if sensor_id == "ops.approval_boundary":
        return HarnessSensorResult(
            sensor_id,
            "pass",
            "Approval policy metadata was recorded.",
            {"has_harness_policy": bool(execution_result.harness_policy)},
        )
    if sensor_id == "completion.operation_report":
        return _completion_sensor(sensor_id, completion_result)
    return HarnessSensorResult(sensor_id, "not_applicable", "No deterministic check is defined.")


def _completion_sensor(sensor_id: str, completion_result: CompletionGateResult) -> HarnessSensorResult:
    complete = is_complete_completion_status(completion_result.status)
    return HarnessSensorResult(
        sensor_id,
        "pass" if complete else "fail",
        completion_result.reason,
        {"status": completion_result.status},
    )


def _missing_evidence_sensor(sensor_id: str, completion_result: CompletionGateResult) -> HarnessSensorResult:
    missing = tuple(completion_result.missing_evidence)
    return HarnessSensorResult(
        sensor_id,
        "pass" if not missing else "fail",
        "No missing evidence." if not missing else "Completion gate reported missing evidence.",
        {"missing_evidence": list(missing)},
    )


def _artifact_count_matching(execution_result: ExecutionResult, matches_kind: Callable[[str | None], bool]) -> int:
    return sum(1 for artifact in execution_result.task_artifacts if matches_kind(artifact.kind))


def _web_tool_evidence_count(execution_result: ExecutionResult) -> int:
    return sum(1 for evidence in execution_result.tool_evidence if is_web_source_evidence_tool(evidence.name))
