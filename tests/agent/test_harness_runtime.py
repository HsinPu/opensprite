import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.bus.message import UserMessage
from opensprite.config.schema import Config, LogConfig, MemoryConfig, RecentSummaryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.llms.base import LLMResponse
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
    def __init__(self, content: str = "Harness runtime reply."):
        self.content = content
        self.tool_names_by_call: list[list[str]] = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        tool_names = [tool["function"]["name"] for tool in tools or []]
        self.tool_names_by_call.append(tool_names)
        return LLMResponse(content=self.content, model=model or "fake-model")

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
        provider = RecordingProvider()
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
        return response, provider.tool_names_by_call, events

    response, tool_names_by_call, events = asyncio.run(scenario())
    event_types = [event.event_type for event in events]
    checkpoint = next(event for event in events if event.event_type == "harness_checkpoint.recorded")

    assert response.text == "Harness runtime reply."
    assert tool_names_by_call[-1] == ["read_file"]
    assert "harness_policy.selected" in event_types
    assert checkpoint.payload["harness_profile"]["name"] == "chat"
    assert checkpoint.payload["harness_policy"]["name"] == "chat_read_policy"
    assert checkpoint.payload["completion"]["status"] == "complete"
    assert checkpoint.payload["next_action"] == "finalize"


def test_harness_runtime_applies_research_policy_to_llm_tools(tmp_path):
    async def scenario():
        provider = RecordingProvider(content="I will use source-grounded research.")
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

    assert tool_names_by_call == [["read_file", "web_search", "web_fetch"]]
    assert result.task_contract is not None
    assert result.task_contract.task_type == "web_research"
    assert result.harness_policy is not None
    assert result.harness_policy["name"] == "research_source_policy"
