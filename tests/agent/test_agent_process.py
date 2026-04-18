import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, LogConfig, MemoryConfig, RecentSummaryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.bus.message import UserMessage
from opensprite.storage.base import StoredMessage
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.last_history = None

    def build_system_prompt(self, chat_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, chat_id=None):
        self.last_history = list(history)
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


class HistoryStorage(FakeStorage):
    def __init__(self, messages):
        super().__init__()
        self.messages = list(messages)

    async def get_messages(self, chat_id, limit=None):
        if limit is None:
            return list(self.messages)
        return list(self.messages[-limit:])


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

    async def _execute(self, **kwargs):
        return "ok"


class LargeSchemaTool(Tool):
    @property
    def name(self) -> str:
        return "large"

    @property
    def description(self) -> str:
        return "large schema tool"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "string",
                    "description": "x" * 2000,
                }
            },
        }

    async def _execute(self, **kwargs):
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

    async def fake_call_llm(chat_id, current_message, channel=None, user_images=None, allow_tools=True, **kwargs):
        call_order.append(("call_llm", chat_id, current_message, channel, list(user_images or [])))
        assert storage.saved[0][1] == "user"
        return "assistant reply"

    async def fake_consolidate(chat_id):
        call_order.append(("memory", chat_id))

    async def fake_update_profile(chat_id):
        call_order.append(("profile", chat_id))

    async def fake_update_recent_summary(chat_id):
        call_order.append(("recent-summary", chat_id))

    agent.call_llm = fake_call_llm
    agent._maybe_consolidate_memory = fake_consolidate
    agent._maybe_update_recent_summary = fake_update_recent_summary
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
        ("recent-summary", "telegram:room-1"),
        ("profile", "telegram:room-1"),
    ]
    assert response.text == "assistant reply"
    assert response.channel == "telegram"
    assert response.session_chat_id == "telegram:room-1"


def test_call_llm_trims_old_history_to_token_budget(tmp_path):
    context_builder = FakeContextBuilder(tmp_path)
    storage = HistoryStorage(
        [
            StoredMessage(role="user", content="old message " * 40, timestamp=1.0),
            StoredMessage(role="assistant", content="recent message", timestamp=2.0),
        ]
    )
    agent = AgentLoop(
        config=AgentConfig(history_token_budget=120),
        provider=FakeProvider(),
        storage=storage,
        context_builder=context_builder,
        tools=ToolRegistry(),
        memory_config=MemoryConfig(),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(enabled=False),
        recent_summary_config=RecentSummaryConfig(enabled=False),
    )

    captured = {}

    async def fake_execute_messages(
        log_id, chat_messages, *, allow_tools, tool_result_chat_id=None, tool_registry=None, on_tool_before_execute=None
    ):
        captured["messages"] = list(chat_messages)
        return "ok"

    agent._execute_messages = fake_execute_messages

    result = asyncio.run(agent.call_llm("telegram:room-1", "current input", channel="telegram", allow_tools=False))

    assert result == "ok"
    assert context_builder.last_history == [{"role": "assistant", "content": "recent message"}]
    assert [message.role for message in captured["messages"]] == ["user"]


def test_load_history_uses_agent_max_history(tmp_path):
    storage = HistoryStorage(
        [
            StoredMessage(role="user", content="first", timestamp=1.0),
            StoredMessage(role="assistant", content="second", timestamp=2.0),
            StoredMessage(role="user", content="third", timestamp=3.0),
        ]
    )
    agent = AgentLoop(
        config=AgentConfig(max_history=2),
        provider=FakeProvider(),
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(enabled=False),
        recent_summary_config=RecentSummaryConfig(enabled=False),
    )

    history = asyncio.run(agent._load_history("telegram:room-1"))

    assert [message.content for message in history] == ["second", "third"]


def test_trim_history_reports_base_tokens_without_history(tmp_path):
    agent = AgentLoop(
        config=AgentConfig(history_token_budget=500),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(enabled=False),
        recent_summary_config=RecentSummaryConfig(enabled=False),
    )

    history, base_tokens, history_tokens, final_tokens = agent._trim_history_to_token_budget(
        history=[],
        current_message="hello",
        channel="telegram",
        chat_id="telegram:room-1",
    )

    assert history == []
    assert base_tokens > 0
    assert history_tokens == 0
    assert final_tokens == base_tokens


def test_tool_schema_tokens_reduce_history_budget(tmp_path):
    storage = HistoryStorage([StoredMessage(role="assistant", content="recent message", timestamp=1.0)])
    registry = ToolRegistry()
    registry.register(LargeSchemaTool())
    agent = AgentLoop(
        config=AgentConfig(history_token_budget=150),
        provider=FakeProvider(),
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path),
        tools=registry,
        memory_config=MemoryConfig(),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(enabled=False),
        recent_summary_config=RecentSummaryConfig(enabled=False),
    )

    tool_tokens = agent._estimate_tool_schema_tokens(allow_tools=True)
    assert tool_tokens > 0

    kept_without_tools, _, _, _ = agent._trim_history_to_token_budget(
        history=[{"role": "assistant", "content": "recent message"}],
        current_message="hello",
        channel="telegram",
        chat_id="telegram:room-1",
        tool_schema_tokens=0,
    )
    kept_with_tools, _, _, _ = agent._trim_history_to_token_budget(
        history=[{"role": "assistant", "content": "recent message"}],
        current_message="hello",
        channel="telegram",
        chat_id="telegram:room-1",
        tool_schema_tokens=tool_tokens,
    )

    assert kept_without_tools == [{"role": "assistant", "content": "recent message"}]
    assert kept_with_tools == []
