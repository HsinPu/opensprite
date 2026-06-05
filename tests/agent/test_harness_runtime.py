import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.bus.message import UserMessage
from opensprite.config.schema import Config, LogConfig, MemoryConfig, RecentSummaryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.llms.base import LLMResponse
from opensprite.runs.events import (
    HARNESS_CHECKPOINT_RECORDED_EVENT,
    HARNESS_POLICY_SELECTED_EVENT,
    HARNESS_PROFILE_SELECTED_EVENT,
    HARNESS_SCORECARD_RECORDED_EVENT,
    TASK_CONTRACT_CREATED_EVENT,
    TASK_CONTRACT_PLANNED_EVENT,
    TASK_CONTRACT_VALIDATED_EVENT,
    TASK_CONTRACT_VALIDATION_FAILED_EVENT,
)
from opensprite.storage import MemoryStorage
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class HarnessRuntimeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def build_system_prompt(self, session_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, session_id=None):
        return [{"role": "user", "content": current_message}]


class NamedTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Dummy {self._name} tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs) -> str:
        return "ok"


class RecordingProvider:
    def __init__(
        self,
        content: str | list[str] = "Harness runtime reply.",
        *,
        planner_content: str | None = None,
    ):
        self.contents = list(content) if isinstance(content, list) else [content]
        self.planner_content = planner_content
        self.tool_names_by_call: list[list[str]] = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        tool_names = [tool["function"]["name"] for tool in tools or []]
        self.tool_names_by_call.append(tool_names)
        first_message = str(
            messages[0].get("content", "") if messages and isinstance(messages[0], dict) else getattr(messages[0], "content", "")
        )
        joined_messages = "\n".join(
            str(message.get("content", "") if isinstance(message, dict) else getattr(message, "content", ""))
            for message in messages
        )
        if "completion judge" in first_message:
            content = '{"status":"complete","reason":"test judge accepted response","active_task_status":"done"}'
        elif self.planner_content is not None and first_message.startswith("You are the OpenSprite task planner"):
            content = self.planner_content
        else:
            content = self.contents.pop(0) if self.contents else "Harness runtime reply."
        return LLMResponse(content=content, model=model or "fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("read_file", "web_search", "web_fetch", "edit_file", "verify"):
        registry.register(NamedTool(name))
    return registry


def _agent(tmp_path: Path, provider: RecordingProvider) -> AgentLoop:
    return AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=MemoryStorage(),
        context_builder=HarnessRuntimeContextBuilder(tmp_path / "workspace"),
        tools=_registry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )


def test_harness_runtime_applies_chat_policy_and_records_checkpoint(tmp_path):
    async def scenario():
        provider = RecordingProvider(
            "Harness runtime reply.",
            planner_content=(
                '{"task_type":"pure_answer","required_tool_groups":[],"allow_no_tool_final":true,'
                '"reason":"plain chat"}'
            ),
        )
        agent = _agent(tmp_path, provider)

        response = await agent.process(
            UserMessage(
                text="hello",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(agent.storage._runs.values()))
        events = await agent.storage.get_run_events("web:browser-1", run.run_id)
        parts = await agent.storage.get_run_parts("web:browser-1", run.run_id)
        return response, provider.tool_names_by_call, events, parts

    response, tool_names_by_call, events, parts = asyncio.run(scenario())
    event_types = [event.event_type for event in events]
    checkpoint = next(event for event in events if event.event_type == HARNESS_CHECKPOINT_RECORDED_EVENT)
    scorecard = next(event for event in events if event.event_type == HARNESS_SCORECARD_RECORDED_EVENT)
    checkpoint_part = next(part for part in parts if part.part_type == "harness_checkpoint")
    scorecard_part = next(part for part in parts if part.part_type == "harness_scorecard")

    assert response.text == "Harness runtime reply."
    assert tool_names_by_call[-1] == []
    assert TASK_CONTRACT_PLANNED_EVENT in event_types
    assert TASK_CONTRACT_VALIDATED_EVENT in event_types
    assert HARNESS_PROFILE_SELECTED_EVENT in event_types
    assert HARNESS_POLICY_SELECTED_EVENT in event_types
    effective_profile = next(event for event in events if event.event_type == HARNESS_PROFILE_SELECTED_EVENT)
    assert effective_profile.payload["name"] == "chat"
    assert effective_profile.payload["selection_phase"] == "contract"
    assert checkpoint.payload["harness_profile"]["name"] == "chat"
    assert checkpoint.payload["harness_policy"]["name"] == "chat_guidance_policy"
    assert checkpoint.payload["completion"]["status"] == "complete"
    assert checkpoint.payload["next_action"] == "finalize"
    assert scorecard.payload["profile"]["name"] == "chat"
    assert scorecard.payload["permissions"]["harness_policy"]["name"] == "chat_guidance_policy"
    assert scorecard.payload["trace_health"]["status"] == "pass"
    assert scorecard.payload["trace_health"]["sensor_counts"]["pass"] == 2
    assert [sensor["sensor_id"] for sensor in scorecard.payload["sensors"]] == [
        "chat.no_unexpected_tools",
        "completion.final_answer",
    ]
    assert checkpoint_part.metadata["harness_profile"]["name"] == "chat"
    assert checkpoint_part.metadata["completion"]["status"] == "complete"
    assert "profile=chat" in checkpoint_part.content
    assert scorecard_part.metadata["profile"]["name"] == "chat"
    assert scorecard_part.metadata["completion"]["status"] == "complete"
    assert "profile=chat" in scorecard_part.content


def test_harness_runtime_records_planning_error_contract_for_invalid_planner_json(tmp_path):
    async def scenario():
        provider = RecordingProvider(
            "I could not select a reliable tool profile.",
            planner_content="this is not valid planner json",
        )
        agent = _agent(tmp_path, provider)
        response = await agent.process(
            UserMessage(
                text="Find the latest stock price for TSMC",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(agent.storage._runs.values()))
        events = await agent.storage.get_run_events("web:browser-1", run.run_id)
        return response, [event.event_type for event in events]

    result, event_types = asyncio.run(scenario())

    assert result.text == "Harness runtime reply."
    assert TASK_CONTRACT_VALIDATION_FAILED_EVENT in event_types
    assert TASK_CONTRACT_CREATED_EVENT in event_types


def test_harness_runtime_applies_research_policy_to_llm_tools(tmp_path):
    async def scenario():
        provider = RecordingProvider(
            "I will use source-grounded research.",
            planner_content=(
                '{"task_type":"web_research","required_tool_groups":["web_research"],'
                '"allow_no_tool_final":false,"reason":"needs sources"}'
            ),
        )
        agent = _agent(tmp_path, provider)
        intent = agent.task_intents.classify("Search the web for the latest OpenSprite release and cite sources")

        result = await agent.call_llm(
            "web:browser-1",
            intent.objective,
            channel="web",
            external_chat_id="browser-1",
            task_intent=intent,
        )
        return result, provider.tool_names_by_call

    result, tool_names_by_call = asyncio.run(scenario())

    assert tool_names_by_call == [[], ["read_file", "web_search", "web_fetch", "edit_file", "verify", "save_memory"]]
    assert result.task_contract is not None
    assert result.task_contract.task_type == "web_research"
    assert result.harness_policy is not None
    assert result.harness_policy["name"] == "research_source_guidance_policy"


def test_harness_runtime_keeps_tool_schemas_disabled_when_contract_exists(tmp_path):
    async def scenario():
        provider = RecordingProvider(
            "Final answer from existing gathered sources.",
            planner_content=(
                '{"task_type":"history_retrieval","required_tool_groups":["search_history"],'
                '"allow_no_tool_final":false,"reason":"stale retry misclassification"}'
            ),
        )
        agent = _agent(tmp_path, provider)
        intent = agent.task_intents.classify("Find today's TSMC stock price and cite sources.")

        result = await agent.call_llm(
            "web:browser-1",
            "Continue using existing gathered web sources.",
            channel="web",
            external_chat_id="browser-1",
            task_intent=intent,
            allow_tools=False,
        )
        return result, provider.tool_names_by_call

    result, tool_names_by_call = asyncio.run(scenario())

    assert result.content == "Final answer from existing gathered sources."
    assert tool_names_by_call == [[], []]
    assert result.task_contract is not None
    assert result.task_contract.task_type == "history_retrieval"
    assert result.harness_policy is not None
    assert result.harness_policy["name"] == "chat_guidance_policy"
