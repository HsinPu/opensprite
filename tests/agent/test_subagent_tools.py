import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.llms.base import LLMResponse, ToolCall
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"

    def build_system_prompt(self, chat_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return messages

    def add_assistant_message(self, messages, content, tool_calls=None):
        return messages


class FakeStorage:
    def __init__(self):
        self.saved = []

    async def get_messages(self, chat_id, limit=None):
        return []

    async def add_message(self, chat_id, message):
        self.saved.append((chat_id, message.role, message.content, message.tool_name))

    async def clear_messages(self, chat_id):
        return None

    async def get_consolidated_index(self, chat_id):
        return 0

    async def set_consolidated_index(self, chat_id, index):
        return None

    async def get_all_chats(self):
        return []


class DummyTool(Tool):
    def __init__(self, name: str = "demo_tool"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    async def _execute(self, value: str = "", **kwargs):
        return f"tool:{value}"


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048):
        self.calls.append({"messages": list(messages), "tools": tools})
        if len(self.calls) == 1:
            return LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            )
        return LLMResponse(content="done", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


def test_subagent_can_use_tools_but_not_delegate(tmp_path):
    provider = FakeProvider()
    storage = FakeStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("demo_tool"))
    registry.register(DummyTool("delegate"))
    registry.register(DummyTool("cron"))
    agent = AgentLoop(
        config=AgentConfig(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(enabled=False),
    )
    agent._current_chat_id.set("telegram:user-a")

    result = asyncio.run(agent.run_subagent("do the task", prompt_type="implementer"))

    assert result == "done"
    assert provider.calls[0]["tools"] is not None
    tool_names = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
    assert "demo_tool" in tool_names
    assert "delegate" not in tool_names
    assert "cron" not in tool_names
    assert storage.saved == [("telegram:user-a", "tool", "tool:abc", "demo_tool")]
