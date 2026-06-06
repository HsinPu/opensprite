from opensprite.agent.harness_policy import HarnessScorecard, HarnessSensorResult
from opensprite.agent.turn_runner import _harness_trace_health


def test_harness_sensor_result_metadata_is_json_safe():
    result = HarnessSensorResult(
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


def test_harness_scorecard_metadata_has_stable_sections():
    scorecard = HarnessScorecard(
        profile={"name": "chat", "task_type": "conversation"},
        contract={"task_type": "pure_answer"},
        tools={"count": 0, "names": []},
        permissions={"effective_policy": {"approval_mode": "auto"}},
        sensors=(HarnessSensorResult("chat.no_unexpected_tools", "pass"),),
        completion={"status": "complete"},
        trace_health={"status": "pass"},
    )

    payload = scorecard.to_metadata()

    assert payload["schema_version"] == 1
    assert payload["kind"] == "harness_scorecard"
    assert set(payload) == {
        "schema_version",
        "kind",
        "profile",
        "contract",
        "tools",
        "permissions",
        "sensors",
        "completion",
        "trace_health",
    }
    assert payload["sensors"][0]["sensor_id"] == "chat.no_unexpected_tools"


def test_harness_trace_health_uses_sensor_severity_and_missing_sections():
    passing = _harness_trace_health(
        has_profile=True,
        has_contract=True,
        has_completion=True,
        sensors=(HarnessSensorResult("completion.final_answer", "pass"),),
    )
    warning = _harness_trace_health(
        has_profile=True,
        has_contract=True,
        has_completion=True,
        sensors=(HarnessSensorResult("research.freshness", "warn"),),
    )
    failing = _harness_trace_health(
        has_profile=True,
        has_contract=False,
        has_completion=True,
        sensors=(HarnessSensorResult("research.source_coverage", "fail"),),
    )

    assert passing["status"] == "pass"
    assert warning["status"] == "warn"
    assert failing["status"] == "fail"
    assert failing["missing_sections"] == ["contract"]
    assert failing["sensor_counts"] == {"pass": 0, "warn": 0, "fail": 1, "not_applicable": 0}
