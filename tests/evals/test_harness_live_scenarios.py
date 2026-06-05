from opensprite.evals.harness_live_scenarios import run_controlled_harness_scenarios


def test_controlled_harness_scenarios_cover_runtime_policy_path():
    payload = run_controlled_harness_scenarios()

    assert payload["ok"] is True
    assert payload["live"] is False
    assert payload["kind"] == "controlled_harness_scenarios"
    assert payload["summary"] == {
        "passed_cases": 5,
        "total_cases": 5,
        "passed_checks": 32,
        "total_checks": 32,
    }


def test_controlled_harness_scenarios_report_expected_profiles_and_tools():
    cases = {case["id"]: case for case in run_controlled_harness_scenarios()["cases"]}

    assert cases["chat_guidance"]["profile"]["name"] == "chat"
    assert "edit_file" in cases["chat_guidance"]["visible_tools"]
    assert "verify" in cases["chat_guidance"]["visible_tools"]

    assert cases["research_sources"]["policy"]["name"] == "research_source_guidance_policy"
    assert "web_fetch" in cases["research_sources"]["visible_tools"]

    assert "edit_file" in cases["coding_analysis"]["visible_tools"]
    assert "edit_file" in cases["coding_change"]["visible_tools"]
    assert any(check["id"] == "approval:mcp_example_tool" for check in cases["ops_approval"]["checks"])


def test_controlled_harness_scenarios_report_expected_sensor_ids():
    cases = {case["id"]: case for case in run_controlled_harness_scenarios()["cases"]}

    assert cases["chat_guidance"]["expected_sensor_ids"] == ["chat.no_unexpected_tools", "completion.final_answer"]
    assert cases["coding_change"]["expected_sensor_ids"] == [
        "coding.file_change",
        "coding.verification",
        "completion.change_summary",
    ]
    assert all(any(check["id"] == "expected_sensors" and check["ok"] for check in case["checks"]) for case in cases.values())
