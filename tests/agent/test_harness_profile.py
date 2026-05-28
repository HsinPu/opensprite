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


def test_default_chat_profile_no_longer_routes_by_user_text_markers():
    profile = HarnessProfileService().default_chat_profile()

    assert profile.name == "chat"
    assert profile.selection_signals == ("default:chat",)


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


@pytest.mark.anyio
async def test_task_contract_planner_falls_back_for_invalid_json_web_request():
    planner = TaskContractPlanner(Config.load_agent_template_config().task_contract_llm)
    intent = TaskIntentService().classify("Find the latest stock price for TSMC")
    provider = _FakePlannerProvider("I think this needs web research, but this is not JSON.")

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        task_intent=intent,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert contract.planner_metadata["planner_status"] == "fallback"
    assert "invalid JSON" in contract.planner_metadata["reason"]
    assert any(item.tool_group == "web_research" for item in contract.requirements)


@pytest.mark.anyio
async def test_task_contract_planner_repairs_invalid_json_with_second_llm_call():
    planner = TaskContractPlanner(Config.load_agent_template_config().task_contract_llm)
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
        task_intent=intent,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "pure_answer"
    assert contract.allow_no_tool_final is True
    assert contract.planner_metadata["planner_status"] == "validated"
    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_task_contract_planner_falls_back_to_pure_answer_for_invalid_json_plain_request():
    planner = TaskContractPlanner(Config.load_agent_template_config().task_contract_llm)
    intent = TaskIntentService().classify("Plan a 30 minute Python study session without web.")
    provider = _FakePlannerProvider("This is a simple planning answer, no tools needed.")

    contract = await planner.plan(
        provider=provider,
        model="planner-model",
        task_intent=intent,
        current_message=intent.objective,
        history=[],
    )

    assert contract.task_type == "pure_answer"
    assert contract.allow_no_tool_final is True
    assert contract.requirements == ()
    assert contract.planner_metadata["planner_status"] == "fallback"
