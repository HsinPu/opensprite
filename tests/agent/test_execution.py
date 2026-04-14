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
    provider = FakeProvider(
        [
            LLMResponse(content="   ", model="fake-model"),
            LLMResponse(content="retry ok", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result == "retry ok"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_falls_back_after_second_blank_visible_response():
    provider = FakeProvider(
        [
            LLMResponse(content="   ", model="fake-model"),
            LLMResponse(content="", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result == "EMPTY"
    assert len(provider.calls) == 2


def test_execution_engine_uses_sanitized_empty_retry_message_for_hidden_only_content():
    provider = FakeProvider(
        [
            LLMResponse(content="<think>secret</think>", model="fake-model"),
            LLMResponse(content="retry ok", model="fake-model"),
        ]
    )
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        tools_config=ToolsConfig(max_tool_iterations=3),
    )
    engine.sanitize_response_content = lambda text: "" if "<think>" in text else text.strip()

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result == "retry ok"
    assert len(provider.calls) == 2
    assert (
        provider.calls[1]["messages"][-1].content
        == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE
    )


def test_execution_engine_returns_tool_loop_specific_fallback_after_blank_final_answer():
    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            ),
            LLMResponse(content="<think>hidden</think>", model="fake-model"),
            LLMResponse(content="", model="fake-model"),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    engine.sanitize_response_content = lambda text: "" if "<think>" in text else text.strip()

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=True)
    )

    assert result == ExecutionEngine.TOOL_LOOP_EMPTY_RESPONSE_FALLBACK
    assert len(provider.calls) == 3


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


def test_execution_engine_stops_after_repeated_missing_required_tool_errors():
    class MissingArgsTool(Tool):
        @property
        def name(self) -> str:
            return "write_file"

        @property
        def description(self) -> str:
            return "write_file"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs) -> str:
            return (
                "Error: Missing required argument(s) for write_file: path, content. "
                "Call write_file with both 'path' and 'content'."
            )

    registry = ToolRegistry()
    registry.register(MissingArgsTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="write_file", arguments={})],
            ),
            LLMResponse(
                content="",
                model="fake-model",
                tool_calls=[ToolCall(id="tc2", name="write_file", arguments={})],
            ),
        ]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=10))

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=True)
    )

    assert "我重複嘗試呼叫工具，但仍然缺少必要參數而無法繼續。" in result
    assert "Missing required argument(s) for write_file" in result
    assert len(provider.calls) == 2


def test_execution_engine_slims_tool_result_for_context_but_persists_full_result():
    class VerboseTool(Tool):
        @property
        def name(self) -> str:
            return "verbose_tool"

        @property
        def description(self) -> str:
            return "Verbose tool"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs) -> str:
            return "A" * 2000 + "TAIL"

    registry = ToolRegistry()
    registry.register(VerboseTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="verbose_tool", arguments={})],
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
    assert save_calls == [("chat-1", "tool", "A" * 2000 + "TAIL", "verbose_tool")]
    assert messages[-1].role == "tool"
    assert "Output truncated for context" in messages[-1].content
    assert len(messages[-1].content) < len("A" * 2000 + "TAIL")
    assert messages[-1].content.endswith("TAIL")


def test_exec_tool_result_slimming_keeps_timeout_and_stderr_highlights():
    result = (
        "Error: Command timed out after 60s. The command may be waiting for interactive input or may be stuck.\n"
        "Partial output before timeout:\n"
        + ("line\n" * 400)
        + "[stderr] npm ERR! missing script: build\n"
        + "final line\n"
    )

    summary = ExecutionEngine._summarize_tool_result_for_context("exec", result)

    assert "Output truncated for context" in summary
    assert "Timeout/Error summary:" in summary
    assert "timed out after 60s" in summary
    assert "stderr highlights:" in summary
    assert "missing script: build" in summary
    assert "output tail:" in summary
    assert "final line" in summary


def test_exec_tool_result_slimming_prefers_tail_lines_for_long_output():
    result = "\n".join([f"line {i}" for i in range(300)])

    summary = ExecutionEngine._summarize_tool_result_for_context("exec", result)

    assert "output start:" in summary
    assert "line 0" in summary
    assert "output tail:" in summary
    assert "line 299" in summary
    assert len(summary) <= ExecutionEngine.EXEC_RESULT_MAX_CHARS + len("\n... (exec context summary truncated)")
