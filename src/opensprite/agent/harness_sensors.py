"""Deterministic harness sensor checks for scorecards."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .harness_inventory import SENSOR_IDS_BY_TASK_TYPE
from .harness_scorecard import HarnessSensorResult

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
    sensor_ids = SENSOR_IDS_BY_TASK_TYPE.get(task_type, ())
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
        count = _artifact_count(execution_result, "web_source")
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
        count = _artifact_count_any(execution_result, ("image_text", "image_analysis", "audio_transcript", "video_analysis"))
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
    complete = completion_result.status == "complete"
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


def _artifact_count(execution_result: ExecutionResult, kind: str) -> int:
    return sum(1 for artifact in execution_result.task_artifacts if artifact.kind == kind)


def _artifact_count_any(execution_result: ExecutionResult, kinds: tuple[str, ...]) -> int:
    return sum(1 for artifact in execution_result.task_artifacts if artifact.kind in kinds)


def _web_tool_evidence_count(execution_result: ExecutionResult) -> int:
    return sum(1 for evidence in execution_result.tool_evidence if str(evidence.name).startswith("web_"))
