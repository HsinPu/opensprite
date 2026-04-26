import asyncio

from agent_test_helpers import DummyTool, make_agent_loop
from opensprite.bus.message import UserMessage
from opensprite.config.schema import ToolsConfig


def _make_agent(tmp_path, tools_config: ToolsConfig | None = None):
    return make_agent_loop(tmp_path, tools_config=tools_config)


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
    assert agent.mcp_lifecycle.connected is True
    assert "mcp_demo_echo" in agent.tools.tool_names


def test_connect_mcp_uses_retry_backoff_after_failure(tmp_path, monkeypatch):
    calls = []
    clock = {"now": 100.0}

    async def fake_connect(servers, registry, stack):
        calls.append(sorted(servers.keys()))
        raise RuntimeError("boom")

    monkeypatch.setattr("opensprite.tools.mcp.connect_mcp_servers", fake_connect)
    monkeypatch.setattr("opensprite.agent.mcp_lifecycle.time.monotonic", lambda: clock["now"])

    agent = _make_agent(
        tmp_path,
        ToolsConfig(mcp_servers={"demo": {"command": "npx", "args": ["demo-mcp"]}}),
    )

    asyncio.run(agent.connect_mcp())

    assert calls == [["demo"]]
    assert agent.mcp_lifecycle.connected is False
    assert agent.mcp_lifecycle.connect_failures == 1
    assert agent.mcp_lifecycle.retry_after > clock["now"]

    asyncio.run(agent.connect_mcp())
    assert calls == [["demo"]]

    clock["now"] = agent.mcp_lifecycle.retry_after
    asyncio.run(agent.connect_mcp())

    assert calls == [["demo"], ["demo"]]
    assert agent.mcp_lifecycle.connect_failures == 2


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

        async def fake_update_active_task(chat_id):
            order.append("active-task")

        async def fake_update_recent_summary(chat_id):
            order.append("recent-summary")

        agent.connect_mcp = fake_connect_mcp
        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_consolidate
        agent._maybe_update_recent_summary = fake_update_recent_summary
        agent._maybe_update_user_profile = fake_update_profile
        agent._maybe_update_active_task = fake_update_active_task

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

    assert order[:2] == ["connect", "call_llm"]
    assert set(order[2:]) == {"memory", "recent-summary", "profile", "active-task"}
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

    monkeypatch.setattr("opensprite.agent.mcp_lifecycle.AsyncExitStack", FakeStack)
    monkeypatch.setattr("opensprite.tools.mcp.connect_mcp_servers", fake_connect)

    agent = _make_agent(
        tmp_path,
        ToolsConfig(mcp_servers={"demo": {"command": "npx", "args": ["demo-mcp"]}}),
    )

    asyncio.run(agent.connect_mcp())
    stack = agent.mcp_lifecycle.stack

    assert stack is not None
    assert agent.mcp_lifecycle.connected is True

    asyncio.run(agent.close_mcp())

    assert stack.closed is True
    assert agent.mcp_lifecycle.stack is None
    assert agent.mcp_lifecycle.connected is False


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
    agent.mcp_lifecycle.connect_failures = 3
    agent.mcp_lifecycle.retry_after = 999999.0

    mcp_path.write_text(
        '{"other":{"command":"npx","args":["-y","other-mcp"]}}',
        encoding="utf-8",
    )

    result = asyncio.run(agent.reload_mcp_from_config())

    assert "MCP configuration reloaded." in result
    assert "mcp_other_echo" in agent.tools.tool_names
    assert "mcp_demo_echo" not in agent.tools.tool_names
    assert agent.mcp_lifecycle.connect_failures == 0
    assert agent.mcp_lifecycle.retry_after == 0.0
