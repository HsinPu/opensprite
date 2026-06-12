from opensprite.agent.task.capabilities import TaskScorecard, TaskSensorResult
from opensprite.agent.task.scorecard import task_trace_health


def test_task_sensor_result_metadata_is_json_safe():
    result = TaskSensorResult(
        sensor_id="chat.no_unexpected_tools",
        status="pass",
        summary="No tools were called.",
        details={"tool_count": 0},
    )

    assert result.to_metadata() == {
        "sensor_id": "chat.no_unexpected_tools",
        "status": "pass",
        "summary": "No tools were called.",
        "details": {"tool_count": 0},
    }


def test_task_scorecard_metadata_has_stable_sections():
    scorecard = TaskScorecard(
        contract={"task_type": "pure_answer"},
        tools={"count": 0, "names": []},
        tool_selection={"selected_tools": []},
        sensors=(TaskSensorResult("chat.no_unexpected_tools", "pass"),),
        completion={"status": "complete"},
        trace_health={"status": "pass"},
    )

    payload = scorecard.to_metadata()

    assert payload["schema_version"] == 1
    assert payload["kind"] == "task_scorecard"
    assert set(payload) == {
        "schema_version",
        "kind",
        "contract",
        "tools",
        "tool_selection",
        "sensors",
        "completion",
        "trace_health",
    }
    assert payload["sensors"][0]["sensor_id"] == "chat.no_unexpected_tools"


def test_task_trace_health_uses_sensor_severity_and_missing_sections():
    passing = task_trace_health(
        has_contract=True,
        has_completion=True,
        sensors=(TaskSensorResult("completion.final_answer", "pass"),),
    )
    warning = task_trace_health(
        has_contract=True,
        has_completion=True,
        sensors=(TaskSensorResult("research.freshness", "warn"),),
    )
    failing = task_trace_health(
        has_contract=False,
        has_completion=True,
        sensors=(TaskSensorResult("research.source_coverage", "fail"),),
    )

    assert passing["status"] == "pass"
    assert warning["status"] == "warn"
    assert failing["status"] == "fail"
    assert failing["missing_sections"] == ["contract"]
    assert failing["sensor_counts"] == {"pass": 0, "warn": 0, "fail": 1, "not_applicable": 0}
