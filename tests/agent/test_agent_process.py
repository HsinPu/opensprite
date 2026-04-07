import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.bus.message import UserMessage
from opensprite.storage.base import StoredMessage
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


class FakeStorage:
    def __init__(self):
        self.saved = []

    async def get_messages(self, chat_id, limit=None):
        return []

    async def add_message(self, chat_id, message: StoredMessage):
        self.saved.append((chat_id, message.role, message.content, dict(message.metadata)))

    async def clear_messages(self, chat_id):
        return None

    async def get_consolidated_index(self, chat_id):
        return 0

    async def set_consolidated_index(self, chat_id, index):
        return None

    async def get_all_chats(self):
        return []


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


def test_agent_process_persists_user_then_assistant_then_runs_maintenance(tmp_path):
    registry = ToolRegistry()
    registry.register(DummyTool())
    storage = FakeStorage()
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
    )

    call_order = []

    async def fake_call_llm(chat_id, current_message, channel=None, user_images=None, allow_tools=True):
        call_order.append(("call_llm", chat_id, current_message, channel, list(user_images or [])))
        assert storage.saved[0][1] == "user"
        return "assistant reply"

    async def fake_consolidate(chat_id):
        call_order.append(("memory", chat_id))

    async def fake_update_profile(chat_id):
        call_order.append(("profile", chat_id))

    agent.call_llm = fake_call_llm
    agent._maybe_consolidate_memory = fake_consolidate
    agent._maybe_update_user_profile = fake_update_profile

    response = asyncio.run(
        agent.process(
            UserMessage(
                text="hello",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
                sender_id="user-1",
                sender_name="alice",
                images=["img1"],
                metadata={"source": "test"},
            )
        )
    )

    assert [entry[1] for entry in storage.saved] == ["user", "assistant"]
    assert storage.saved[0][3]["sender_name"] == "alice"
    assert storage.saved[0][3]["images_count"] == 1
    assert storage.saved[1][3] == {"channel": "telegram", "transport_chat_id": "room-1"}
    assert call_order == [
        ("call_llm", "telegram:room-1", "hello", "telegram", ["img1"]),
        ("memory", "telegram:room-1"),
        ("profile", "telegram:room-1"),
    ]
    assert response.text == "assistant reply"
    assert response.channel == "telegram"
    assert response.session_chat_id == "telegram:room-1"
