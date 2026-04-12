import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"

    def build_system_prompt(self, chat_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message, "images": current_images}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return messages

    def add_assistant_message(self, messages, content, tool_calls=None):
        return messages


class FakeProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048):
        raise AssertionError("provider.chat should not be called")

    def get_default_model(self) -> str:
        return "fake-model"


class DummyTool(Tool):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "dummy"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


def test_call_llm_replaces_direct_image_payload_with_tool_hint(tmp_path):
    registry = ToolRegistry()
    registry.register(DummyTool())
    agent = AgentLoop(
        config=AgentConfig(),
        provider=FakeProvider(),
        storage=None,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(enabled=False),
    )

    captured = {}

    async def fake_execute(log_id, chat_messages, *, allow_tools, tool_result_chat_id=None, tool_registry=None):
        captured["content"] = chat_messages[0].content
        return "ok"

    agent._execute_messages = fake_execute  # type: ignore[method-assign]

    result = asyncio.run(
        agent.call_llm(
            "telegram:user-a",
            current_message="What is in this image?",
            channel="telegram",
            user_images=["img-a"],
        )
    )

    assert result == "ok"
    assert "User attached 1 image(s). Use analyze_image if visual understanding is needed." in captured["content"]
