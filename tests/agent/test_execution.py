import asyncio

from opensprite.agent.execution import ExecutionEngine
from opensprite.config.schema import ToolsConfig
from opensprite.llms.base import ChatMessage, LLMResponse, ToolCall
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class DummyTool(Tool):
    @property
    def name(self) -> str:
        return "demo_tool"

    @property
    def description(self) -> str:
        return "Demo tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    async def execute(self, value: str, **kwargs) -> str:
        return f"tool:{value}"


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)


async def _save_message_collector(calls, chat_id, role, content, tool_name=None):
    calls.append((chat_id, role, content, tool_name))


def _make_engine(provider, registry, save_calls, tools_config=None):
    return ExecutionEngine(
        provider=provider,
        tools=registry,
        tools_config=tools_config or ToolsConfig(max_tool_iterations=3),
        empty_response_fallback="EMPTY",
        save_message=lambda chat_id, role, content, tool_name=None: _save_message_collector(
            save_calls, chat_id, role, content, tool_name
        ),
        format_log_preview=lambda text, max_chars=200: str(text)[:max_chars],
        summarize_messages=lambda messages, tail=4: f"count={len(messages)}",
        sanitize_response_content=lambda text: text.strip(),
    )


def test_execution_engine_runs_tool_loop_and_persists_tool_result():
    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    messages = [ChatMessage(role="user", content="hi")]

    result = asyncio.run(
        engine.execute_messages("chat-1", messages, allow_tools=True, tool_result_chat_id="chat-1")
    )

    assert result == "done"
    assert save_calls == [("chat-1", "tool", "tool:abc", "demo_tool")]
    assert [message.role for message in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls[0]["function"]["name"] == "demo_tool"


def test_execution_engine_uses_empty_fallback_for_blank_visible_response():
    provider = FakeProvider([LLMResponse(content="   ", model="fake-model")])
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result == "EMPTY"


def test_execution_engine_returns_max_iteration_message_when_tool_loop_never_finishes():
    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="loop",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            )
        ]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=1))

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=True)
    )

    assert "超過了最大迭代次數（1次）" in result
    assert "demo_tool: tool:abc" in result
