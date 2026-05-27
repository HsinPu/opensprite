from opensprite.agent.harness_scorecard import HarnessScorecard, HarnessSensorResult


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
