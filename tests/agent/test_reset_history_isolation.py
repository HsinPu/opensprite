import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.storage.base import StoredMessage
from opensprite.storage.memory import MemoryStorage
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


class FakeProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048):
        raise AssertionError("provider.chat should not be called in this test")

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


class FakeSearchStore:
    def __init__(self):
        self.cleared = []

    async def clear_chat(self, chat_id: str) -> None:
        self.cleared.append(chat_id)


def test_reset_history_only_clears_target_session(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        await storage.add_message("telegram:user-a", StoredMessage(role="user", content="A1", timestamp=1.0))
        await storage.add_message("telegram:user-b", StoredMessage(role="user", content="B1", timestamp=2.0))

        search_store = FakeSearchStore()
        registry = ToolRegistry()
        registry.register(DummyTool())
        agent = AgentLoop(
            config=AgentConfig(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=FakeContextBuilder(tmp_path),
            tools=registry,
            memory_config=MemoryConfig(),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(enabled=False),
            search_store=search_store,
        )

        await agent.reset_history("telegram:user-a")

        messages_a = await storage.get_messages("telegram:user-a")
        messages_b = await storage.get_messages("telegram:user-b")
        return messages_a, messages_b, search_store.cleared

    messages_a, messages_b, cleared = asyncio.run(scenario())

    assert messages_a == []
    assert [message.content for message in messages_b] == ["B1"]
    assert cleared == ["telegram:user-a"]
