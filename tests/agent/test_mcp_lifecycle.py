import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.bus.message import UserMessage
from opensprite.config.schema import AgentConfig, Config, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
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
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        raise AssertionError("provider.chat should not be called in this test")

    def get_default_model(self) -> str:
        return "fake-model"


class FakeStorage:
    def __init__(self):
        self.saved = []

    async def get_messages(self, chat_id, limit=None):
        return []

    async def add_message(self, chat_id, message: StoredMessage):
        self.saved.append((chat_id, message.role, message.content))

    async def clear_messages(self, chat_id):
        return None

    async def get_consolidated_index(self, chat_id):
        return 0

    async def set_consolidated_index(self, chat_id, index):
        return None

    async def get_all_chats(self):
        return []


class DummyTool(Tool):
    def __init__(self, name: str = "dummy"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs):
        return "ok"


def _make_agent(tmp_path: Path, tools_config: ToolsConfig | None = None) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(DummyTool())
    return AgentLoop(
        config=AgentConfig(),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=tools_config or ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )


def test_connect_mcp_registers_tools_once(tmp_path, monkeypatch):
    calls = []

    async def fake_connect(servers, registry, stack):
        calls.append(sorted(servers.keys()))
        registry.register(DummyTool("mcp_demo_echo"))

    monkeypatch.setattr("opensprite.tools.mcp.connect_mcp_servers", fake_connect)

    agent = _make_agent(
        tmp_path,
        ToolsConfig(mcp_servers={"demo": {"command": "npx", "args": ["demo-mcp"]}}),
    )

    asyncio.run(agent.connect_mcp())
    asyncio.run(agent.connect_mcp())

    assert calls == [["demo"]]
    assert agent._mcp_connected is True
    assert "mcp_demo_echo" in agent.tools.tool_names


def test_process_connects_mcp_before_saving_and_calling_llm(tmp_path):
    async def scenario():
        agent = _make_agent(tmp_path)
        order = []

        async def fake_connect_mcp():
            order.append("connect")
            assert agent.storage.saved == []

        async def fake_call_llm(chat_id, current_message, channel=None, user_images=None, allow_tools=True, **kwargs):
            from opensprite.agent.execution import ExecutionResult

            order.append("call_llm")
            assert order[0] == "connect"
            return ExecutionResult(content="assistant reply", executed_tool_calls=0, used_configure_skill=False)

        async def fake_consolidate(chat_id):
            order.append("memory")

        async def fake_update_profile(chat_id):
            order.append("profile")

        async def fake_update_recent_summary(chat_id):
            order.append("recent-summary")

        agent.connect_mcp = fake_connect_mcp
        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_consolidate
        agent._maybe_update_recent_summary = fake_update_recent_summary
        agent._maybe_update_user_profile = fake_update_profile

        response = await agent.process(
            UserMessage(
                text="hello",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
            )
        )
        await agent.wait_for_background_maintenance()
        return response, order

    response, order = asyncio.run(scenario())

    assert order == ["connect", "call_llm", "memory", "recent-summary", "profile"]
    assert response.text == "assistant reply"


def test_close_mcp_resets_state_and_closes_stack(tmp_path, monkeypatch):
    class FakeStack:
        def __init__(self):
            self.closed = False

        async def __aenter__(self):
            return self

        async def aclose(self):
            self.closed = True

    async def fake_connect(servers, registry, stack):
        return None

    monkeypatch.setattr("opensprite.agent.agent.AsyncExitStack", FakeStack)
    monkeypatch.setattr("opensprite.tools.mcp.connect_mcp_servers", fake_connect)

    agent = _make_agent(
        tmp_path,
        ToolsConfig(mcp_servers={"demo": {"command": "npx", "args": ["demo-mcp"]}}),
    )

    asyncio.run(agent.connect_mcp())
    stack = agent._mcp_stack

    assert stack is not None
    assert agent._mcp_connected is True

    asyncio.run(agent.close_mcp())

    assert stack.closed is True
    assert agent._mcp_stack is None
    assert agent._mcp_connected is False


def test_reload_mcp_from_config_replaces_registered_mcp_tools(tmp_path, monkeypatch):
    config_path = tmp_path / "opensprite.json"
    mcp_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        '{"llm":{"api_key":"key","model":"gpt","temperature":0.7,"max_tokens":2048},'
        '"storage":{"type":"memory","path":"memory.db"},'
        '"channels":{"telegram":{"enabled":false},"console":{"enabled":true}},'
        '"tools":{"mcp_servers_file":"mcp_servers.json"}}',
        encoding="utf-8",
    )
    mcp_path.write_text(
        '{"demo":{"command":"npx","args":["-y","demo-mcp"]}}',
        encoding="utf-8",
    )

    async def fake_connect(servers, registry, stack):
        for server_name in sorted(servers):
            registry.register(DummyTool(f"mcp_{server_name}_echo"))

    monkeypatch.setattr("opensprite.tools.mcp.connect_mcp_servers", fake_connect)

    agent = _make_agent(
        tmp_path,
        ToolsConfig(mcp_servers={"demo": {"command": "npx", "args": ["demo-mcp"]}}),
    )
    agent.config_path = config_path

    asyncio.run(agent.connect_mcp())
    assert "mcp_demo_echo" in agent.tools.tool_names

    mcp_path.write_text(
        '{"other":{"command":"npx","args":["-y","other-mcp"]}}',
        encoding="utf-8",
    )

    result = asyncio.run(agent.reload_mcp_from_config())

    assert "MCP configuration reloaded." in result
    assert "mcp_other_echo" in agent.tools.tool_names
    assert "mcp_demo_echo" not in agent.tools.tool_names
