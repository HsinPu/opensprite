import json

import pytest

from opensprite.agent.harness_profile import HarnessProfileService
from opensprite.agent.task_contract import EvidenceRequirement, TaskContract, TaskContractPlanner
from opensprite.agent.task_intent import TaskIntentService
from opensprite.config import Config


def _contract(task_type: str, *requirements: EvidenceRequirement) -> TaskContract:
    return TaskContract(
        objective="test objective",
        task_type=task_type,
        requirements=tuple(requirements),
        allow_no_tool_final=not requirements,
        contract_sources=("llm_planner",),
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


def test_harness_profile_derives_workspace_analysis_from_contract():
    contract = _contract(
        "workspace_read",
        EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),
    )

    profile = HarnessProfileService().from_contract(contract)

    assert profile.name == "coding"
    assert profile.task_type == "workspace_analysis"
    assert profile.required_tool_groups == ("workspace_read",)


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
    assert "configuration" in profile.approval_required_risk_levels


def test_legacy_select_no_longer_routes_by_user_text_markers():
    intent = TaskIntentService().classify("Find the latest stock price for TSMC")

    profile = HarnessProfileService().select(intent)

    assert profile.name == "chat"
    assert profile.selection_signals == ("legacy:fallback:chat",)


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakePlannerProvider:
    def __init__(self, payload: dict):
        self.payload = payload
        self.messages = []

    async def chat(self, messages, *, model=None, **kwargs):
        self.messages = messages
        return _FakeResponse(json.dumps(self.payload))


@pytest.mark.anyio
async def test_task_contract_planner_builds_web_contract_from_llm_json():
    planner = TaskContractPlanner(Config.load_agent_template_config().task_contract_llm)
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
        task_intent=intent,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "web_research"
    assert contract.contract_sources == ("llm_planner",)
    assert contract.allow_no_tool_final is False
    assert any(item.kind == "tool_group" and item.tool_group == "web_research" for item in contract.requirements)
    assert any(item.kind == "source_reference" for item in contract.acceptance_criteria)


@pytest.mark.anyio
async def test_task_contract_planner_builds_workspace_change_contract_from_llm_json():
    planner = TaskContractPlanner(Config.load_agent_template_config().task_contract_llm)
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
        task_intent=intent,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "code_change"
    assert any(item.kind == "tool_group" and item.tool_group == "workspace_read" for item in contract.requirements)
    assert any(item.kind == "file_change" for item in contract.requirements)
