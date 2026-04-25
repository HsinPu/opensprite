import asyncio

from opensprite.agent.execution import ExecutionEngine
from opensprite.config.schema import Config, ToolsConfig
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

    async def _execute(self, value: str, **kwargs) -> str:
        return f"tool:{value}"


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)


class OverflowThenSuccessProvider:
    def __init__(self, final_response: str = "done"):
        self.final_response = final_response
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({
            "messages": [ChatMessage(role=m.role, content=m.content, tool_call_id=m.tool_call_id, tool_calls=m.tool_calls) for m in messages],
            "tools": tools,
        })
        if len(self.calls) == 1:
            raise RuntimeError("This model's maximum context length was exceeded")
        return LLMResponse(content=self.final_response, model="fake-model")


async def _save_message_collector(calls, chat_id, role, content, tool_name=None, metadata=None):
    calls.append((chat_id, role, content, tool_name, dict(metadata or {})))


def _make_engine(provider, registry, save_calls, tools_config=None):
    return ExecutionEngine(
        provider=provider,
        tools=registry,
        tools_config=tools_config or ToolsConfig(max_tool_iterations=3),
        empty_response_fallback="EMPTY",
        save_message=lambda chat_id, role, content, tool_name=None, metadata=None: _save_message_collector(
            save_calls, chat_id, role, content, tool_name, metadata
        ),
        format_log_preview=lambda text, max_chars=200: str(text)[:max_chars],
        summarize_messages=lambda messages, tail=4: f"count={len(messages)}",
        sanitize_response_content=lambda text: text.strip(),
        **Config.packaged_execution_engine_chat_kwargs(),
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

    assert result.content == "done"
    assert result.executed_tool_calls == 1
    assert result.used_configure_skill is False
    assert save_calls == [
        ("chat-1", "tool", "tool:abc", "demo_tool", {"tool_args": {"value": "abc"}})
    ]
    assert [message.role for message in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls[0]["function"]["name"] == "demo_tool"


def test_execution_engine_calls_on_tool_before_execute_before_tool_run():
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
    order = []

    async def before(name, args):
        order.append(("before", name, args))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=True,
            tool_result_chat_id="chat-1",
            on_tool_before_execute=before,
        )
    )

    assert result.content == "done"
    assert order == [("before", "demo_tool", {"value": "abc"})]


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

    assert result.content == "retry ok"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_uses_sanitized_empty_retry_message_for_hidden_only_content():
    provider = FakeProvider(
        [
            LLMResponse(content="<think>secret</think>", model="fake-model"),
            LLMResponse(content="retry ok", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = lambda text: "" if "<think>" in text else text.strip()

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "retry ok"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


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

    assert result.content == "EMPTY"
    assert len(provider.calls) == 2


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

    assert "超過了最大迭代次數（1次）" in result.content
    assert "demo_tool: tool:abc" in result.content


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
            return {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            }

        async def _execute(self, **kwargs) -> str:
            raise AssertionError("_execute should not be called when validation fails")

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

    assert "我重複嘗試呼叫工具，但工具參數仍然無效而無法繼續。" in result.content
    assert "Invalid arguments for write_file" in result.content
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

        async def _execute(self, **kwargs) -> str:
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

    assert result.content == "done"
    assert save_calls == [
        ("chat-1", "tool", "A" * 2000 + "TAIL", "verbose_tool", {"tool_args": {}})
    ]
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


class FakeConfigureSkillTool(Tool):
    """Minimal stand-in for configure_skill (mutation success path)."""

    @property
    def name(self) -> str:
        return "configure_skill"

    @property
    def description(self) -> str:
        return "configure skill"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
            },
            "required": ["action"],
        }

    async def _execute(self, action: str, **kwargs):
        return "Added skill 'demo-skill' at /tmp/SKILL.md"


def test_execution_refreshes_system_and_tools_after_configure_skill_success():
    registry = ToolRegistry()
    registry.register(FakeConfigureSkillTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="configure_skill",
                        arguments={"action": "add"},
                    )
                ],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    messages = [
        ChatMessage(role="system", content="SYSTEM_V1"),
        ChatMessage(role="user", content="hi"),
    ]
    state = {"n": 0}

    def refresh_system():
        state["n"] += 1
        return f"SYSTEM_V{state['n'] + 1}"

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=True,
            tool_result_chat_id="chat-1",
            refresh_system_prompt=refresh_system,
        )
    )

    assert result.content == "done"
    assert result.used_configure_skill is True
    assert result.executed_tool_calls == 1
    assert messages[0].content == "SYSTEM_V2"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][0].content == "SYSTEM_V2"
    second_tools = provider.calls[1]["tools"]
    assert second_tools is not None
    assert any(t["function"]["name"] == "configure_skill" for t in second_tools)


def test_execution_respects_max_tool_iterations_override():
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
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=99))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=True,
            max_tool_iterations=1,
        )
    )

    assert "超過了最大迭代次數（1次）" in result.content
    assert result.executed_tool_calls == 1


def test_execution_compacts_and_retries_after_context_overflow():
    provider = OverflowThenSuccessProvider("after compact")
    engine = _make_engine(provider, ToolRegistry(), [])
    statuses = []
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 5000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="tool", content="tool result " + "B" * 5000, tool_call_id="tc1"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    async def status_hook(text: str):
        statuses.append(text)

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_llm_status=status_hook,
        )
    )

    assert result.content == "after compact"
    assert result.context_compactions == 1
    assert len(provider.calls) == 2
    retried_messages = provider.calls[1]["messages"]
    assert [message.role for message in retried_messages] == ["system", "system", "user"]
    assert retried_messages[0].content == "SYSTEM"
    assert "# Compacted Conversation State" in retried_messages[1].content
    assert "latest instruction" in retried_messages[1].content
    assert "A" * 5000 not in retried_messages[1].content
    assert retried_messages[2].content == ExecutionEngine.CONTINUATION_AFTER_COMPACTION_MESSAGE
    assert statuses == ["上下文已接近上限，正在壓縮目前任務並繼續…"]


def test_execution_context_compaction_does_not_consume_tool_iteration():
    class ToolThenOverflowProvider:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
            self.calls.append({"messages": list(messages), "tools": tools})
            if len(self.calls) == 1:
                return LLMResponse(
                    content="need tool",
                    model="fake-model",
                    tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
                )
            if len(self.calls) == 2:
                raise RuntimeError("context_length_exceeded")
            return LLMResponse(content="done", model="fake-model")

    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = ToolThenOverflowProvider()
    save_calls = []
    engine = _make_engine(provider, registry, save_calls, tools_config=ToolsConfig(max_tool_iterations=2))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=True,
            tool_result_chat_id="chat-1",
        )
    )

    assert result.content == "done"
    assert result.executed_tool_calls == 1
    assert result.context_compactions == 1
    assert len(provider.calls) == 3
    assert save_calls == [
        ("chat-1", "tool", "tool:abc", "demo_tool", {"tool_args": {"value": "abc"}})
    ]


def test_execution_does_not_compact_unrelated_llm_errors():
    class BrokenProvider:
        async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
            raise RuntimeError("network unavailable")

    engine = _make_engine(BrokenProvider(), ToolRegistry(), [])

    try:
        asyncio.run(engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False))
    except RuntimeError as exc:
        assert str(exc) == "network unavailable"
    else:
        raise AssertionError("expected RuntimeError")
