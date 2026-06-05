import json

import pytest

from opensprite.agent.harness_profile import (
    CONTRACT_MEDIA_PROFILE_REASON,
    CONTRACT_OPERATIONS_PROFILE_REASON,
    CONTRACT_PLANNING_PROFILE_REASON,
    CONTRACT_PURE_ANSWER_PROFILE_REASON,
    CONTRACT_WEB_RESEARCH_PROFILE_REASON,
    CONTRACT_WORKSPACE_CHANGE_PROFILE_REASON,
    CONTRACT_WORKSPACE_EVIDENCE_PROFILE_REASON,
    DEFAULT_CHAT_PROFILE_REASON,
    HarnessProfileService,
    PREVIEW_CHAT_PROFILE_REASON,
    PREVIEW_MEDIA_PROFILE_REASON,
    PREVIEW_OPERATIONS_PROFILE_REASON,
    PREVIEW_WEB_RESEARCH_PROFILE_REASON,
    PREVIEW_WORKSPACE_ANALYSIS_PROFILE_REASON,
    PREVIEW_WORKSPACE_CHANGE_PROFILE_REASON,
    harness_profile_follow_up_instruction,
    is_chat_profile_name,
    is_coding_profile_name,
    is_media_profile_name,
    is_ops_profile_name,
    is_research_profile_name,
    normalize_profile_name,
)
from opensprite.agent.task_contract import (
    EvidenceRequirement,
    LLM_PLANNER_CONTRACT_SOURCES,
    OPERATION_REPORT_CRITERION_KIND,
    PLANNER_BLOCKED_STATUS,
    PLANNER_INVALID_STATUS,
    PLANNER_METADATA_REASON_FIELD,
    PLANNER_METADATA_STATUS_FIELD,
    PLANNER_VALIDATED_STATUS,
    TaskContract,
    TaskPlanner,
    WORKSPACE_LOCATION_CRITERION_KIND,
    WORKSPACE_LOCATION_QUALITY_CHECK,
)
from opensprite.agent.task_intent import TaskIntentService
from opensprite.config import Config


def _contract(task_type: str, *requirements: EvidenceRequirement) -> TaskContract:
    return TaskContract(
        objective="test objective",
        task_type=task_type,
        requirements=tuple(requirements),
        allow_no_tool_final=not requirements,
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
    )


def test_harness_profile_derives_research_from_contract():
    contract = _contract(
        "web_research",
        EvidenceRequirement(kind="tool_group", tool_group="web_research"),
    )

    profile = HarnessProfileService().from_contract(contract)

    assert profile.name == "research"
    assert profile.task_type == "web_research"
    assert profile.selection_signals == ("contract:web_research",)


def test_harness_profile_contract_selection_reasons_are_stable():
    assert CONTRACT_OPERATIONS_PROFILE_REASON == "task contract selected operations profile"
    assert CONTRACT_WEB_RESEARCH_PROFILE_REASON == "task contract requires web research evidence"
    assert CONTRACT_MEDIA_PROFILE_REASON == "task contract requires media evidence"
    assert CONTRACT_WORKSPACE_CHANGE_PROFILE_REASON == "task contract requires workspace changes"
    assert CONTRACT_WORKSPACE_EVIDENCE_PROFILE_REASON == "task contract requires workspace evidence"
    assert CONTRACT_PLANNING_PROFILE_REASON == "task contract selected planning mode"
    assert CONTRACT_PURE_ANSWER_PROFILE_REASON == "task contract does not require tool-backed evidence"
    assert DEFAULT_CHAT_PROFILE_REASON == "no task contract available; defaulting to neutral chat profile"


def test_harness_profile_preview_reasons_are_stable():
    assert PREVIEW_CHAT_PROFILE_REASON == "preview profile for low-risk chat turns"
    assert PREVIEW_WEB_RESEARCH_PROFILE_REASON == "preview profile for source-grounded web research turns"
    assert PREVIEW_WORKSPACE_ANALYSIS_PROFILE_REASON == "preview profile for workspace analysis turns"
    assert PREVIEW_WORKSPACE_CHANGE_PROFILE_REASON == "preview profile for workspace change turns"
    assert PREVIEW_MEDIA_PROFILE_REASON == "preview profile for media extraction turns"
    assert PREVIEW_OPERATIONS_PROFILE_REASON == "preview profile for operations turns"


def test_harness_profile_derives_workspace_analysis_from_contract():
    contract = _contract(
        "workspace_read",
        EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),
    )

    profile = HarnessProfileService().from_contract(contract)

    assert profile.name == "coding"
    assert profile.task_type == "workspace_analysis"
    assert profile.required_tool_groups == ("workspace_read",)


def test_harness_profile_derives_planning_from_contract_before_tool_groups():
    contract = _contract(
        "planning",
        EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),
    )

    profile = HarnessProfileService().from_contract(contract)

    assert profile.name == "chat"
    assert profile.task_type == "planning"
    assert profile.required_tool_groups == ()
    assert profile.selection_signals == ("contract:planning",)


def test_harness_profile_derives_workspace_change_from_contract():
    contract = _contract(
        "code_change",
        EvidenceRequirement(kind="tool_group", tool_group="workspace_write"),
        EvidenceRequirement(kind="file_change"),
    )

    profile = HarnessProfileService().from_contract(contract)

    assert profile.name == "coding"
    assert profile.task_type == "workspace_change"
    assert "workspace_write" in profile.required_tool_groups


def test_harness_profile_preserves_workspace_change_verification_requirement():
    contract = _contract(
        "code_change",
        EvidenceRequirement(kind="tool_group", tool_group="workspace_write"),
        EvidenceRequirement(kind="file_change"),
        EvidenceRequirement(kind="verification", tool_group="verification"),
    )

    profile = HarnessProfileService().from_contract(contract)

    assert profile.name == "coding"
    assert profile.task_type == "workspace_change"
    assert "verification" in profile.required_tool_groups
    assert "verification" in profile.required_evidence


def test_harness_profile_derives_media_from_contract():
    contract = _contract(
        "media_extraction",
        EvidenceRequirement(kind="tool_group", tool_group="media"),
    )

    profile = HarnessProfileService().from_contract(contract)

    assert profile.name == "media"
    assert profile.task_type == "media_extraction"


def test_harness_profile_derives_ops_from_contract():
    profile = HarnessProfileService().from_contract(_contract("operations"))

    assert profile.name == "ops"
    assert profile.task_type == "operations"
    assert profile.required_tool_groups == ("workspace_read",)
    assert "configuration" in profile.approval_required_risk_levels


def test_harness_profile_derives_ops_scheduling_from_contract():
    profile = HarnessProfileService().from_contract(
        _contract("operations", EvidenceRequirement(kind="tool_group", tool_group="scheduling"))
    )

    assert profile.name == "ops"
    assert profile.task_type == "operations"
    assert profile.required_tool_groups == ("scheduling", "workspace_read")


def test_harness_profile_derives_ops_execution_from_contract():
    profile = HarnessProfileService().from_contract(
        _contract("operations", EvidenceRequirement(kind="tool_group", tool_group="execution"))
    )

    assert profile.name == "ops"
    assert profile.task_type == "operations"
    assert profile.required_tool_groups == ("execution", "workspace_read")


def test_default_chat_profile_no_longer_routes_by_user_text_markers():
    profile = HarnessProfileService().default_chat_profile()

    assert profile.name == "chat"
    assert profile.selection_signals == ("default:chat",)


def test_harness_profile_name_helpers_are_centralized():
    assert normalize_profile_name(" research ") == "research"
    assert is_chat_profile_name("chat") is True
    assert is_chat_profile_name("research") is False
    assert is_research_profile_name("research") is True
    assert is_research_profile_name("coding") is False
    assert is_coding_profile_name("coding") is True
    assert is_coding_profile_name("media") is False
    assert is_media_profile_name("media") is True
    assert is_media_profile_name("ops") is False
    assert is_ops_profile_name("ops") is True
    assert is_ops_profile_name("chat") is False


def test_harness_profile_follow_up_instruction_matches_profiles():
    assert "Harness profile: research" in harness_profile_follow_up_instruction("research")
    assert "Harness profile: coding" in harness_profile_follow_up_instruction("coding")
    assert "Harness profile: media" in harness_profile_follow_up_instruction("media")
    assert "Harness profile: ops" in harness_profile_follow_up_instruction("ops")
    assert harness_profile_follow_up_instruction("chat") == ""


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakePlannerProvider:
    def __init__(self, payload: dict | str | list[dict | str]):
        self.payloads = list(payload) if isinstance(payload, list) else [payload]
        self._last_payload = self.payloads[-1]
        self.messages = []
        self.calls = []

    async def chat(self, messages, *, model=None, **kwargs):
        self.messages = messages
        self.calls.append(messages)
        payload = self.payloads.pop(0) if self.payloads else self._last_payload
        self._last_payload = payload
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return _FakeResponse(content)


class _FailingPlannerProvider:
    async def chat(self, messages, *, model=None, **kwargs):
        raise TimeoutError("planner timed out")


@pytest.mark.anyio
async def test_task_planner_builds_web_contract_from_llm_json():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Find the latest stock price for TSMC")
    provider = _FakePlannerProvider(
        {
            "task_type": "web_research",
            "required_tool_groups": ["web_research"],
            "final_answer_required": True,
            "allow_no_tool_final": False,
            "reason": "Current market data requires web evidence.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "web_research"
    assert contract.contract_sources == LLM_PLANNER_CONTRACT_SOURCES
    assert contract.allow_no_tool_final is False
    assert any(item.kind == "tool_group" and item.tool_group == "web_research" for item in contract.requirements)
    assert any(item.kind == "source_reference" for item in contract.acceptance_criteria)


@pytest.mark.anyio
async def test_task_planner_builds_workspace_change_contract_from_llm_json():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Fix the failing test")
    provider = _FakePlannerProvider(
        {
            "task_type": "workspace_change",
            "required_tool_groups": ["workspace_read", "workspace_write"],
            "allow_no_tool_final": False,
            "reason": "The user asks for a code change.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "code_change"
    assert any(item.kind == "tool_group" and item.tool_group == "workspace_read" for item in contract.requirements)
    assert any(item.kind == "file_change" for item in contract.requirements)


@pytest.mark.anyio
async def test_task_planner_builds_scheduling_contract_from_llm_json():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Remind me tomorrow morning to check the report.")
    provider = _FakePlannerProvider(
        {
            "task_type": "ops",
            "required_tool_groups": ["scheduling"],
            "allow_no_tool_final": False,
            "reason": "The user asks to create a reminder.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "operations"
    assert any(item.kind == "tool_group" and item.tool_group == "scheduling" for item in contract.requirements)
    assert any(item.kind == OPERATION_REPORT_CRITERION_KIND for item in contract.acceptance_criteria)
    assert contract.allow_no_tool_final is False


@pytest.mark.anyio
async def test_task_planner_builds_execution_contract_from_llm_json():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Check whether git is installed and report the version.")
    provider = _FakePlannerProvider(
        {
            "task_type": "ops",
            "required_tool_groups": ["execution"],
            "allow_no_tool_final": False,
            "reason": "The user asks to inspect a local command version.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "operations"
    assert any(item.kind == "tool_group" and item.tool_group == "execution" for item in contract.requirements)
    assert any(item.kind == OPERATION_REPORT_CRITERION_KIND for item in contract.acceptance_criteria)
    assert contract.allow_no_tool_final is False


@pytest.mark.anyio
async def test_task_planner_honors_pure_answer_for_command_version_payload():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("確認這台目前 git 版本，只回答版本號。")
    provider = _FakePlannerProvider(
        {
            "task_type": "pure_answer",
            "required_tool_groups": [],
            "allow_no_tool_final": True,
            "reason": "The previous response already mentioned a git version.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[{"role": "assistant", "content": "`git --version` -> git version 2.47.0.windows.1"}],
    )

    assert contract.task_type == "pure_answer"
    assert contract.requirements == ()
    assert contract.allow_no_tool_final is True
    assert "override_reason" not in contract.planner_metadata


@pytest.mark.anyio
async def test_task_planner_honors_workspace_read_for_repository_status_payload():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    message = "幫我看目前 repo 是否有未提交的 source 改動，忽略 .tmp 這類測試暫存。"
    intent = TaskIntentService().classify(message)
    provider = _FakePlannerProvider(
        {
            "task_type": "workspace_read",
            "required_tool_groups": ["workspace_read"],
            "quality_checks": [WORKSPACE_LOCATION_QUALITY_CHECK],
            "allow_no_tool_final": False,
            "reason": "The user asks to inspect the repository files.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=message,
        history=[],
    )

    assert contract.task_type == "workspace_read"
    assert any(item.kind == "tool_group" and item.tool_group == "workspace_read" for item in contract.requirements)
    assert contract.planner_metadata["quality_checks"] == [WORKSPACE_LOCATION_QUALITY_CHECK]
    assert any(item.kind == WORKSPACE_LOCATION_CRITERION_KIND for item in contract.acceptance_criteria)
    assert contract.allow_no_tool_final is False
    assert "override_reason" not in contract.planner_metadata


@pytest.mark.anyio
async def test_task_planner_keeps_current_cli_usage_with_workspace_when_reading_allowed():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    message = "我想了解目前 OpenSprite 的 trace CLI 怎麼用，先不要改檔案，只給我測試指令與用途。"
    intent = TaskIntentService().classify(message)
    provider = _FakePlannerProvider(
        {
            "task_type": "workspace_read",
            "required_tool_groups": ["workspace_read"],
            "allow_no_tool_final": False,
            "reason": "The user mentioned current project CLI usage.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=message,
        history=[],
    )

    assert contract.task_type == "workspace_read"
    assert any(item.kind == "tool_group" and item.tool_group == "workspace_read" for item in contract.requirements)
    assert contract.allow_no_tool_final is False
    assert "override_reason" not in contract.planner_metadata


@pytest.mark.anyio
async def test_task_planner_keeps_workspace_read_when_planner_requires_it():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    message = "我想了解 trace CLI 怎麼用，不要讀檔，只給我一般測試指令與用途。"
    intent = TaskIntentService().classify(message)
    provider = _FakePlannerProvider(
        {
            "task_type": "workspace_read",
            "required_tool_groups": ["workspace_read"],
            "allow_no_tool_final": False,
            "reason": "The user mentioned CLI usage.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=message,
        history=[],
    )

    assert contract.task_type == "workspace_read"
    assert any(item.kind == "tool_group" and item.tool_group == "workspace_read" for item in contract.requirements)
    assert contract.allow_no_tool_final is False
    assert "override_reason" not in contract.planner_metadata


@pytest.mark.anyio
async def test_task_planner_does_not_override_workspace_change_for_command_usage_text():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    message = "Without reading the repo, explain how to change the opensprite trace CLI implementation."
    intent = TaskIntentService().classify(message)
    provider = _FakePlannerProvider(
        {
            "task_type": "code_change",
            "required_tool_groups": ["workspace_read", "workspace_write"],
            "allow_no_tool_final": False,
            "reason": "The user asked for an implementation change.",
        }
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=message,
        history=[],
    )

    assert contract.task_type == "code_change"
    assert any(item.kind == "file_change" for item in contract.requirements)
    assert contract.allow_no_tool_final is False
    assert "override_reason" not in contract.planner_metadata


@pytest.mark.anyio
async def test_task_planner_blocks_web_request_when_planner_json_is_invalid():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Find the latest stock price for TSMC")
    provider = _FakePlannerProvider("I think this needs web research, but this is not JSON.")

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "planning_error"
    assert contract.allow_no_tool_final is False
    assert contract.planner_metadata[PLANNER_METADATA_STATUS_FIELD] == PLANNER_INVALID_STATUS
    assert "invalid JSON" in contract.planner_metadata[PLANNER_METADATA_REASON_FIELD]
    assert contract.requirements == ()


@pytest.mark.anyio
async def test_task_planner_blocks_when_llm_call_fails():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Find current OpenRouter API parameter docs and cite sources")

    contract = await planner.plan(
        provider=_FailingPlannerProvider(),
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "planning_error"
    assert contract.allow_no_tool_final is False
    assert contract.planner_metadata[PLANNER_METADATA_STATUS_FIELD] == PLANNER_BLOCKED_STATUS
    assert "TimeoutError" in contract.planner_metadata[PLANNER_METADATA_REASON_FIELD]
    assert contract.requirements == ()


@pytest.mark.anyio
async def test_task_planner_blocks_workspace_request_when_planner_json_is_invalid():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("請看目前工作區，找出 CLI chat 相關測試檔案有哪些。")
    provider = _FakePlannerProvider("I should inspect files, but this is not JSON.")

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "planning_error"
    assert contract.allow_no_tool_final is False
    assert contract.planner_metadata[PLANNER_METADATA_STATUS_FIELD] == PLANNER_INVALID_STATUS
    assert "invalid JSON" in contract.planner_metadata[PLANNER_METADATA_REASON_FIELD]
    assert contract.requirements == ()


@pytest.mark.anyio
async def test_task_planner_repairs_invalid_json_with_second_llm_call():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Plan a 30 minute Python study session without web.")
    provider = _FakePlannerProvider(
        [
            "The user wants a simple planning answer, no tools are needed.",
            {
                "task_type": "pure_answer",
                "required_tool_groups": [],
                "final_answer_required": True,
                "allow_no_tool_final": True,
                "reason": "No external evidence is needed.",
            },
        ]
    )

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "pure_answer"
    assert contract.allow_no_tool_final is True
    assert contract.planner_metadata[PLANNER_METADATA_STATUS_FIELD] == PLANNER_VALIDATED_STATUS
    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_task_planner_blocks_plain_request_when_planner_json_is_invalid():
    planner = TaskPlanner(Config.load_agent_template_config().task_planner_llm)
    intent = TaskIntentService().classify("Plan a 30 minute Python study session without web.")
    provider = _FakePlannerProvider("This is a simple planning answer, no tools needed.")

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "planning_error"
    assert contract.allow_no_tool_final is False
    assert contract.requirements == ()
    assert contract.planner_metadata[PLANNER_METADATA_STATUS_FIELD] == PLANNER_INVALID_STATUS
