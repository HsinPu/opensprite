"""Task scorecard assembly and trace-health helpers."""

from __future__ import annotations

from typing import Any

from .capabilities import TaskScorecard, TaskSensorResult, evaluate_task_sensors


def task_contract_type(task_contract: Any) -> str:
    return str(getattr(task_contract, "task_type", "") or "").strip()


def task_scorecard_metadata(
    *,
    aggregate_result: Any,
    completion_result: Any,
) -> dict[str, Any]:
    task_contract = getattr(aggregate_result, "task_contract", None)
    contract_metadata = task_contract.to_metadata() if task_contract is not None else {}
    task_type = task_contract_type(task_contract)
    sensors = evaluate_task_sensors(
        task_type=_scorecard_sensor_task_type(task_type),
        execution_result=aggregate_result,
        completion_result=completion_result,
    )
    scorecard = TaskScorecard(
        contract=contract_metadata,
        tools={
            "executed_tool_calls": aggregate_result.executed_tool_calls,
            "had_tool_error": aggregate_result.had_tool_error,
            "file_change_count": aggregate_result.file_change_count,
            "tool_evidence_count": len(aggregate_result.tool_evidence),
            "task_artifact_count": len(aggregate_result.task_artifacts),
        },
        tool_selection={
            "tool_selection": dict(aggregate_result.tool_selection or {}),
        },
        sensors=sensors,
        completion=completion_result.to_metadata(),
        trace_health=task_trace_health(
            has_contract=task_contract is not None,
            has_completion=bool(completion_result.status),
            sensors=sensors,
        ),
    )
    metadata = scorecard.to_metadata()
    metadata["kind"] = "task_scorecard"
    metadata["task"] = {"task_type": task_type}
    return metadata


def task_trace_health(
    *,
    has_contract: bool,
    has_completion: bool,
    sensors: tuple[TaskSensorResult, ...],
) -> dict[str, Any]:
    sensor_statuses = [sensor.status for sensor in sensors]
    missing_sections = [
        section
        for section, present in (
            ("contract", has_contract),
            ("completion", has_completion),
        )
        if not present
    ]
    status = "pass"
    if missing_sections or "fail" in sensor_statuses:
        status = "fail"
    elif "warn" in sensor_statuses or "not_applicable" in sensor_statuses:
        status = "warn"
    return {
        "status": status,
        "has_contract": has_contract,
        "has_completion": has_completion,
        "missing_sections": missing_sections,
        "sensor_counts": {
            "pass": sensor_statuses.count("pass"),
            "warn": sensor_statuses.count("warn"),
            "fail": sensor_statuses.count("fail"),
            "not_applicable": sensor_statuses.count("not_applicable"),
        },
    }


def _scorecard_sensor_task_type(task_type: str) -> str:
    normalized = str(task_type or "").strip()
    return {
        "workspace_read": "workspace_analysis",
        "analysis": "workspace_analysis",
        "task": "pure_answer",
        "planning": "pure_answer",
    }.get(normalized, normalized)
