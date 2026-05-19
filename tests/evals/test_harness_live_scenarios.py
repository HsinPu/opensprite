from opensprite.evals.harness_live_scenarios import run_controlled_harness_scenarios


def test_controlled_harness_scenarios_cover_runtime_policy_path():
    payload = run_controlled_harness_scenarios()

    assert payload["ok"] is True
    assert payload["live"] is False
    assert payload["kind"] == "controlled_harness_scenarios"
    assert payload["summary"] == {
        "passed_cases": 5,
        "total_cases": 5,
        "passed_checks": 25,
        "total_checks": 25,
    }


def test_controlled_harness_scenarios_report_expected_profiles_and_tools():
    cases = {case["id"]: case for case in run_controlled_harness_scenarios()["cases"]}

    assert cases["chat_read_only"]["profile"]["name"] == "chat"
    assert "edit_file" not in cases["chat_read_only"]["visible_tools"]
    assert "exec" not in cases["chat_read_only"]["visible_tools"]

    assert cases["research_sources"]["policy"]["name"] == "research_source_policy"
    assert "web_fetch" in cases["research_sources"]["visible_tools"]

    assert "edit_file" not in cases["coding_analysis"]["visible_tools"]
    assert "edit_file" in cases["coding_change"]["visible_tools"]
    assert any(check["id"] == "approval:mcp_example_tool" for check in cases["ops_approval"]["checks"])
