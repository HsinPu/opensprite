from opensprite.evals.harness_profiles import (
    HARNESS_PROFILE_EVAL_CASES,
    evaluate_harness_profile_case,
    run_harness_profile_eval,
)


def test_harness_profile_eval_runs_fixed_cases():
    payload = run_harness_profile_eval()

    assert payload["ok"] is True
    assert payload["live"] is False
    assert payload["kind"] == "harness_profiles"
    assert payload["summary"] == {
        "passed_cases": 5,
        "total_cases": 5,
        "passed_checks": 70,
        "total_checks": 70,
    }
    assert {case["id"] for case in payload["cases"]} == {
        "chat_question",
        "research_sources",
        "coding_change",
        "media_artifact",
        "operations_approval",
    }


def test_harness_profile_eval_covers_profile_contract_and_policy_expectations():
    cases = {case["id"]: case for case in run_harness_profile_eval()["cases"]}

    assert cases["chat_question"]["profile"]["name"] == "chat"
    assert cases["chat_question"]["policy"]["name"] == "chat_guidance_policy"
    assert cases["chat_question"]["contract"]["allow_no_tool_final"] is True
    assert any(check["id"] == "scenario" and check["ok"] for check in cases["chat_question"]["checks"])

    assert cases["research_sources"]["profile"]["required_evidence"] == ["web_source", "source_reference"]
    assert cases["research_sources"]["contract"]["task_type"] == "web_research"
    assert any(item["kind"] == "source_reference" for item in cases["research_sources"]["contract"]["acceptance_criteria"])

    assert cases["coding_change"]["policy"]["name"] == "workspace_change_guidance_policy"
    assert "configuration" in cases["coding_change"]["policy"]["approval_required_risk_levels"]
    assert any(item["kind"] == "verification_or_gap" for item in cases["coding_change"]["contract"]["acceptance_criteria"])

    assert cases["media_artifact"]["profile"]["name"] == "media"
    assert cases["media_artifact"]["contract"]["selected_resources"]
    assert any(item["kind"] == "media_artifact" for item in cases["media_artifact"]["contract"]["acceptance_criteria"])

    assert cases["operations_approval"]["profile"]["name"] == "ops"
    assert "mcp" in cases["operations_approval"]["policy"]["approval_required_risk_levels"]
    assert any(item["kind"] == "operation_report" for item in cases["operations_approval"]["contract"]["acceptance_criteria"])


def test_harness_profile_eval_reports_expected_sensor_ids():
    cases = {case["id"]: case for case in run_harness_profile_eval()["cases"]}

    assert cases["chat_question"]["expected_sensor_ids"] == ["chat.no_unexpected_tools", "completion.final_answer"]
    assert cases["research_sources"]["expected_sensor_ids"] == [
        "research.source_coverage",
        "research.freshness",
        "completion.source_grounding",
    ]
    assert all(any(check["id"] == "expected_sensors" and check["ok"] for check in case["checks"]) for case in cases.values())


def test_harness_profile_eval_reports_failed_expectations():
    case = {**HARNESS_PROFILE_EVAL_CASES[0], "expected_profile": "research"}

    payload = evaluate_harness_profile_case(case)

    assert payload["ok"] is False
    assert payload["score"] == {"passed": 13, "total": 14}
    assert {check["id"] for check in payload["checks"] if not check["ok"]} == {"profile"}
