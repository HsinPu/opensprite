from opensprite.agent.harness_profile import HarnessProfileService
from opensprite.agent.task_contract import TaskContractService
from opensprite.agent.task_intent import TaskIntentService


def _profile(text: str):
    intent = TaskIntentService().classify(text)
    return HarnessProfileService().select(intent)


def test_harness_profile_selects_research_for_url_task():
    profile = _profile("請上網查 https://example.com 並整理來源")

    assert profile.name == "research"
    assert profile.task_type == "web_research"
    assert "web_source" in profile.required_evidence
    assert profile.verification_policy == "source_grounded"
    metadata = profile.to_metadata()
    assert metadata["selection"]["priority_order"][:2] == ["ops", "media"]
    assert "signal:url" in metadata["selection"]["matched_signals"]


def test_harness_profile_selects_coding_for_code_change_task():
    profile = _profile("Please fix the failing pytest in src/opensprite/agent/task_intent.py")

    assert profile.name == "coding"
    assert profile.task_type == "workspace_change"
    assert "workspace_read" in profile.required_tool_groups
    assert "workspace_write" in profile.required_tool_groups
    assert profile.verification_policy == "focused_if_possible"
    assert "pattern:code_path" in profile.to_metadata()["selection"]["matched_signals"]


def test_harness_profile_selects_ops_before_coding_for_configuration_task():
    profile = _profile("Update the MCP server configuration and restart the service")

    assert profile.name == "ops"
    assert profile.continuation_policy == "approval_bounded"
    assert "configuration" in profile.approval_required_risk_levels
    assert "marker:mcp server" in profile.to_metadata()["selection"]["matched_signals"]


def test_harness_profile_selects_chat_for_plain_question():
    profile = _profile("Harness 在 AI agent 裡是什麼意思？")

    assert profile.name == "chat"
    assert profile.continuation_policy == "minimal"
    assert profile.to_metadata()["selection"]["matched_signals"] == ["fallback:chat"]


def test_harness_profile_respects_explicit_no_web_and_no_file_constraints():
    profile = _profile("\u4e0d\u8981\u8b80\u6a94\u4e5f\u4e0d\u8981\u4e0a\u7db2\uff0c\u5e6b\u6211\u89e3\u91cb Python ModuleNotFoundError")
    metadata = profile.to_metadata()

    assert profile.name == "chat"
    assert "web_research" in profile.denied_tools
    assert "read_file" in profile.denied_tools
    assert "constraint:no_web" in metadata["selection"]["matched_signals"]
    assert "constraint:no_workspace" in metadata["selection"]["matched_signals"]


def test_harness_profile_no_web_direct_answer_overrides_api_key_ops_marker():
    profile = _profile("\u5ef6\u7e8c\u4e0a\u4e00\u984c\uff0c\u8acb\u518d\u7528\u4e00\u53e5\u8a71\u8aaa\u660e API key \u662f\u4ec0\u9ebc\uff0c\u4e5f\u4e0d\u8981\u8b80\u6a94\u6848\u6216\u4e0a\u7db2\u3002")
    metadata = profile.to_metadata()

    assert profile.name == "chat"
    assert "web_research" in profile.denied_tools
    assert "read_file" in profile.denied_tools
    assert "marker:api key" not in metadata["selection"]["matched_signals"]
    assert "constraint:no_web" in metadata["selection"]["matched_signals"]
    assert "constraint:no_workspace" in metadata["selection"]["matched_signals"]


def test_harness_profile_treats_api_key_code_question_as_coding_not_ops():
    intent = TaskIntentService().classify(
        "\u8acb\u53ea\u8b80\u5fc5\u8981\u7684\u5c08\u6848\u6a94\u6848\uff0c\u627e\u51fa harness profile selection \u70ba\u4ec0\u9ebc\u53ef\u80fd\u628a API key \u554f\u984c\u5224\u6210 ops\uff1b\u4e0d\u8981\u4fee\u6539\u6a94\u6848\uff0c\u53ea\u8981\u8aaa\u660e\u8def\u5f91\u8207\u539f\u56e0\u3002"
    )
    profile = HarnessProfileService().select(intent)

    assert profile.name == "coding"
    assert profile.task_type == "workspace_analysis"


def test_chinese_no_edit_and_no_test_constraints_downgrade_code_change():
    intent = TaskIntentService().classify(
        "\u5ef6\u7e8c\u525b\u525b\u7684\u7a0b\u5f0f\u78bc\u89c0\u5bdf\uff0c\u8acb\u8a2d\u8a08\u6700\u5c0f regression test \u6848\u4f8b\uff1b\u4e0d\u8981\u4fee\u6539\u6a94\u6848\u3001\u4e0d\u8981\u57f7\u884c\u6e2c\u8a66\u3002"
    )
    profile = HarnessProfileService().select(intent)

    assert intent.expects_code_change is False
    assert intent.expects_verification is False
    assert profile.name == "coding"
    assert profile.task_type == "workspace_analysis"


def test_task_contract_uses_research_harness_profile_for_source_requirements():
    intent = TaskIntentService().classify("請上網查 OpenAI Codex 的最新資料並附來源")
    profile = HarnessProfileService().select(intent)

    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert contract.task_type == "web_research"
    assert "harness_profile" in contract.contract_sources
    assert contract.allow_no_tool_final is False
    assert any(item.kind == "tool_group" and item.tool_group == "web_research" for item in contract.requirements)
    assert any(item.kind == "source_reference" for item in contract.acceptance_criteria)
    assert contract.to_metadata()["harness_profile"]["name"] == "research"


def test_task_contract_treats_web_named_code_file_as_workspace_not_web():
    intent = TaskIntentService().classify(
        "Read src/opensprite/tools/web_research.py and explain the fetch flow."
    )
    profile = HarnessProfileService().select(intent)

    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "coding"
    assert contract.task_type == "workspace_read"
    assert any(item.kind == "tool_group" and item.tool_group == "workspace_read" for item in contract.requirements)
    assert not any(item.kind == "tool_group" and item.tool_group == "web_research" for item in contract.requirements)


def test_no_edit_refactor_plan_requires_workspace_but_no_file_change():
    intent = TaskIntentService().classify(
        "Plan a refactor for src/opensprite/tools/web_research.py, but do not edit files."
    )
    profile = HarnessProfileService().select(intent)

    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "coding"
    assert profile.task_type == "workspace_analysis"
    assert contract.task_type == "workspace_read"
    assert any(item.kind == "tool_group" and item.tool_group == "workspace_read" for item in contract.requirements)
    assert not any(item.kind == "file_change" for item in contract.requirements)


def test_task_contract_adds_coding_harness_verification_gap_criterion():
    intent = TaskIntentService().classify("Please fix the failing pytest in src/opensprite/agent/task_intent.py")
    profile = HarnessProfileService().select(intent)

    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert contract.task_type == "code_change"
    assert any(item.kind == "file_change" for item in contract.requirements)
    assert any(item.kind == "verification_or_gap" for item in contract.acceptance_criteria)


def test_task_contract_adds_ops_harness_operation_report_criterion():
    intent = TaskIntentService().classify("Update the MCP server configuration and restart the service")
    profile = HarnessProfileService().select(intent)

    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert contract.task_type == "operations"
    assert any(item.kind == "operation_report" for item in contract.acceptance_criteria)


def test_task_contract_adds_media_artifact_criterion_for_selected_media():
    intent = TaskIntentService().classify("請幫我 OCR 這張圖片")
    profile = HarnessProfileService().select(intent)

    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message="User attached 1 image. 請幫我 OCR 這張圖片",
        current_image_files=["workspace/images/input.png"],
        harness_profile=profile,
    )

    assert contract.task_type == "media_extraction"
    assert contract.selected_resources
    assert any(item.kind == "media_artifact" for item in contract.acceptance_criteria)


def test_harness_profile_selects_research_for_chinese_source_request():
    profile = _profile("幫我上網查資料並附上引用來源")

    assert profile.name == "research"
    assert profile.task_type == "web_research"
    assert "marker:上網" in profile.to_metadata()["selection"]["matched_signals"]


def test_harness_profile_selects_coding_for_chinese_file_request():
    profile = _profile("幫我檢查 src/opensprite/agent/harness_profile.py 這個檔案")

    assert profile.name == "coding"
    assert profile.task_type == "workspace_analysis"
    assert "marker:檔案" in profile.to_metadata()["selection"]["matched_signals"]
    assert "pattern:code_path" in profile.to_metadata()["selection"]["matched_signals"]


def test_harness_profile_selects_ops_for_chinese_service_request():
    profile = _profile("幫我更新設定並重啟服務")

    assert profile.name == "ops"
    assert profile.task_type == "operations"
    assert "marker:設定" in profile.to_metadata()["selection"]["matched_signals"]
