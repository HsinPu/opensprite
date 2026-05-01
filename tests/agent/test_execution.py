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


class StreamingProvider:
    def __init__(self, content):
        self.content = content

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        callback = kwargs.get("response_delta_callback")
        if callback is not None:
            await callback(self.content[:5])
            await callback(self.content[5:])
        return LLMResponse(content=self.content, model="fake-model")


class ToolInputStreamingProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        callback = kwargs.get("tool_input_delta_callback")
        if callback is not None:
            await callback("call-1", "demo_tool", '{"value"', 1)
            await callback("call-1", "demo_tool", ':"abc"}', 2)
        return LLMResponse(content="done", model="fake-model")


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


class RetryableThenSuccessProvider:
    def __init__(self, final_response: str = "retry ok"):
        self.final_response = final_response
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        if len(self.calls) == 1:
            error = RuntimeError("rate limited")
            error.status_code = 429
            error.headers = {"retry-after-ms": "0"}
            raise error
        return LLMResponse(content=self.final_response, model="fake-model")


class LlmCompactionProvider:
    def __init__(self, compaction_response: str, final_response: str = "done"):
        self.compaction_response = compaction_response
        self.final_response = final_response
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({
            "messages": [ChatMessage(role=m.role, content=m.content, tool_call_id=m.tool_call_id, tool_calls=m.tool_calls) for m in messages],
            "tools": tools,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        if len(self.calls) == 1:
            return LLMResponse(content=self.compaction_response, model="fake-model")
        return LLMResponse(content=self.final_response, model="fake-model")


async def _save_message_collector(calls, session_id, role, content, tool_name=None, metadata=None):
    calls.append((session_id, role, content, tool_name, dict(metadata or {})))


def _make_engine(provider, registry, save_calls, tools_config=None, **engine_kwargs):
    chat_kwargs = Config.packaged_execution_engine_chat_kwargs()
    chat_kwargs.update(engine_kwargs)
    return ExecutionEngine(
        provider=provider,
        tools=registry,
        tools_config=tools_config or ToolsConfig(max_tool_iterations=3),
        empty_response_fallback="EMPTY",
        save_message=lambda session_id, role, content, tool_name=None, metadata=None: _save_message_collector(
            save_calls, session_id, role, content, tool_name, metadata
        ),
        format_log_preview=lambda text, max_chars=200: str(text)[:max_chars],
        summarize_messages=lambda messages, tail=4: f"count={len(messages)}",
        sanitize_response_content=lambda text: text.strip(),
        **chat_kwargs,
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
        engine.execute_messages("chat-1", messages, allow_tools=True, tool_result_session_id="chat-1")
    )

    assert result.content == "done"
    assert result.executed_tool_calls == 1
    assert result.used_configure_skill is False
    assert save_calls == [
        ("chat-1", "tool", "tool:abc", "demo_tool", {"tool_args": {"value": "abc"}})
    ]
    assert [message.role for message in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls[0]["function"]["name"] == "demo_tool"


def test_execution_engine_records_llm_step_usage_metadata():
    provider = FakeProvider([
        LLMResponse(
            content="done",
            model="fake-model",
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            finish_reason="stop",
        )
    ])
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "done"
    assert len(result.llm_step_events) == 1
    step = result.llm_step_events[0]
    assert step.iteration == 1
    assert step.attempt == 1
    assert step.status == "completed"
    assert step.model == "fake-model"
    assert step.output_tokens == 7
    assert step.total_tokens == 18
    assert step.finish_reason == "stop"
    assert step.estimated_input_tokens >= 1


def test_execution_engine_retries_transient_provider_errors_with_metadata():
    provider = RetryableThenSuccessProvider()
    engine = _make_engine(provider, ToolRegistry(), [])
    statuses = []

    async def on_status(message):
        statuses.append(message)

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=False,
            on_llm_status=on_status,
        )
    )

    assert result.content == "retry ok"
    assert len(provider.calls) == 2
    assert [event.status for event in result.llm_step_events] == ["error", "completed"]
    assert result.llm_step_events[0].retryable is True
    assert result.llm_step_events[0].retry_after_ms == 0
    assert result.llm_step_events[0].next_retry_at is not None
    assert statuses == [ExecutionEngine.PROVIDER_RETRY_STATUS_MESSAGE]


def test_execution_engine_projects_final_response_as_deltas():
    provider = FakeProvider([LLMResponse(content="hello streaming world", model="fake-model")])
    save_calls = []
    engine = _make_engine(provider, ToolRegistry(), save_calls)
    messages = [ChatMessage(role="user", content="hi")]
    deltas = []

    async def on_delta(part_id, delta, state, sequence):
        deltas.append((part_id, delta, state, sequence))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_response_delta=on_delta,
        )
    )

    assert result.content == "hello streaming world"
    assert "".join(item[1] for item in deltas) == "hello streaming world"
    assert deltas == [("assistant:chat-1:1", "hello streaming world", "completed", 1)]


def test_execution_engine_forwards_tool_input_deltas():
    provider = ToolInputStreamingProvider()
    engine = _make_engine(provider, ToolRegistry(), [])
    deltas = []

    async def on_tool_input(call_id, tool_name, delta, sequence):
        deltas.append((call_id, tool_name, delta, sequence))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=False,
            on_tool_input_delta=on_tool_input,
        )
    )

    assert result.content == "done"
    assert deltas == [("call-1", "demo_tool", '{"value"', 1), ("call-1", "demo_tool", ':"abc"}', 2)]


def test_execution_engine_marks_provider_streamed_response_completed():
    provider = StreamingProvider("hello streaming world")
    save_calls = []
    engine = _make_engine(provider, ToolRegistry(), save_calls)
    messages = [ChatMessage(role="user", content="hi")]
    deltas = []

    async def on_delta(part_id, delta, state, sequence):
        deltas.append((part_id, delta, state, sequence))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_response_delta=on_delta,
        )
    )

    assert result.content == "hello streaming world"
    assert deltas == [
        ("assistant:chat-1:1", "hello", "running", 1),
        ("assistant:chat-1:1", " streaming world", "running", 2),
        ("assistant:chat-1:1", "", "completed", 3),
    ]


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
            tool_result_session_id="chat-1",
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


def test_execution_engine_stops_when_cancel_checker_requests_stop():
    provider = FakeProvider([LLMResponse(content="done", model="fake-model")])
    engine = _make_engine(provider, ToolRegistry(), [])

    try:
        asyncio.run(
            engine.execute_messages(
                "chat-1",
                [ChatMessage(role="user", content="hi")],
                allow_tools=False,
                should_cancel=lambda: True,
            )
        )
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError was not raised")


def test_execution_engine_records_aborted_tool_result_when_cancelled_after_tool_start():
    class CancellingTool(Tool):
        @property
        def name(self) -> str:
            return "cancel_tool"

        @property
        def description(self) -> str:
            return "Cancel after this tool runs"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def _execute(self, **kwargs) -> str:
            cancel_requested["value"] = True
            return "finished"

    registry = ToolRegistry()
    registry.register(CancellingTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="cancel_tool", arguments={})],
            ),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    cancel_requested = {"value": False}
    after_calls = []

    async def after(*args):
        after_calls.append(args)

    try:
        asyncio.run(
            engine.execute_messages(
                "chat-1",
                [ChatMessage(role="user", content="hi")],
                allow_tools=True,
                should_cancel=lambda: cancel_requested["value"],
                on_tool_after_execute=after,
            )
        )
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError was not raised")

    assert len(after_calls) == 1
    assert after_calls[0][:5] == (
        "cancel_tool",
        {},
        "Error: Tool execution aborted",
        "tc1",
        1,
    )
    assert after_calls[0][-2:] == ("error", True)


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
        engine.execute_messages("chat-1", messages, allow_tools=True, tool_result_session_id="chat-1")
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
            tool_result_session_id="chat-1",
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


def test_execution_proactively_compacts_before_llm_request_when_near_budget():
    provider = FakeProvider([LLMResponse(content="after compact", model="fake-model")])
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_window_tokens=200,
        context_output_reserve_tokens=80,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
    )
    statuses = []
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
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
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "proactive"
    assert event.strategy == "deterministic"
    assert event.outcome == "compacted"
    assert event.iteration == 1
    assert event.messages_before == 4
    assert event.messages_after == 4
    assert event.budget_tokens == 120
    assert event.context_window_tokens == 200
    assert event.output_reserve_tokens == 80
    assert event.threshold_tokens == 60
    assert event.estimated_tokens is not None and event.estimated_tokens > event.threshold_tokens
    assert event.compacted_tokens is not None and event.compacted_tokens < event.estimated_tokens
    assert event.tool_schema_tokens == 0
    assert len(provider.calls) == 1
    sent_messages = provider.calls[0]["messages"]
    assert [message.role for message in sent_messages] == ["system", "system", "assistant", "user"]
    assert sent_messages[0].content == "SYSTEM"
    assert "# Compacted Conversation State" in sent_messages[1].content
    assert "approaching the configured context budget" in sent_messages[1].content
    assert "## Preserved Recent Tail" in sent_messages[1].content
    assert "latest instruction" in sent_messages[1].content
    assert "A" * 2000 not in sent_messages[1].content
    assert sent_messages[2].content == "intermediate answer"
    assert sent_messages[3].content == "latest instruction"
    assert statuses == [ExecutionEngine.PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE]


def test_execution_skips_proactive_compaction_below_budget():
    provider = FakeProvider([LLMResponse(content="done", model="fake-model")])
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=10000,
        context_compaction_threshold_ratio=0.9,
        context_compaction_min_messages=3,
    )
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail"),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=False))

    assert result.content == "done"
    assert result.context_compactions == 0
    assert result.context_compaction_events == []
    assert len(provider.calls) == 1
    assert provider.calls[0]["messages"] == messages


def test_execution_uses_llm_compactor_when_configured():
    provider = LlmCompactionProvider(
        "# Compacted Task State\n## Current Goal\nContinue the latest instruction with the important old detail.",
        "after llm compact",
    )
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
        context_compaction_strategy="llm",
        context_compaction_llm=Config.load_agent_template_config().context_compaction_llm,
    )
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=False))

    assert result.content == "after llm compact"
    assert result.context_compactions == 1
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "proactive"
    assert event.strategy == "llm"
    assert event.outcome == "compacted"
    assert event.fallback_reason is None
    assert event.messages_before == 4
    assert event.messages_after == 4
    assert len(provider.calls) == 2
    compactor_call = provider.calls[0]
    assert compactor_call["tools"] is None
    assert compactor_call["temperature"] == 0
    assert compactor_call["max_tokens"] == 4096
    assert compactor_call["messages"][0].role == "system"
    assert "context compaction engine" in compactor_call["messages"][0].content
    sent_messages = provider.calls[1]["messages"]
    assert [message.role for message in sent_messages] == ["system", "system", "assistant", "user"]
    assert sent_messages[0].content == "SYSTEM"
    assert "compacted by an LLM" in sent_messages[1].content
    assert "## Current Goal" in sent_messages[1].content
    assert "A" * 2000 not in sent_messages[1].content
    assert sent_messages[2].content == "intermediate answer"
    assert sent_messages[3].content == "latest instruction"


def test_execution_falls_back_when_llm_compactor_returns_empty():
    provider = LlmCompactionProvider("   ", "after fallback compact")
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
        context_compaction_strategy="llm",
        context_compaction_llm=Config.load_agent_template_config().context_compaction_llm,
    )
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=False))

    assert result.content == "after fallback compact"
    assert result.context_compactions == 1
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "proactive"
    assert event.strategy == "deterministic"
    assert event.outcome == "fallback"
    assert event.fallback_reason == "llm_empty"
    assert len(provider.calls) == 2
    sent_messages = provider.calls[1]["messages"]
    assert "approaching the configured context budget" in sent_messages[1].content
    assert "compacted by an LLM" not in sent_messages[1].content


def test_proactive_compaction_does_not_consume_overflow_retry():
    provider = OverflowThenSuccessProvider("after overflow retry")
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
    )
    statuses = []
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
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

    assert result.content == "after overflow retry"
    assert result.context_compactions == 2
    assert [event.trigger for event in result.context_compaction_events] == ["proactive", "overflow"]
    assert [event.strategy for event in result.context_compaction_events] == ["deterministic", "deterministic"]
    assert len(provider.calls) == 2
    assert statuses == [
        ExecutionEngine.PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE,
        ExecutionEngine.CONTEXT_OVERFLOW_STATUS_MESSAGE,
    ]


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
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "overflow"
    assert event.strategy == "deterministic"
    assert event.outcome == "compacted"
    assert event.iteration == 1
    assert event.messages_before == 5
    assert event.messages_after == 4
    assert event.estimated_tokens is not None
    assert event.compacted_tokens is not None and event.compacted_tokens < event.estimated_tokens
    assert "maximum context length" in (event.error or "")
    assert len(provider.calls) == 2
    retried_messages = provider.calls[1]["messages"]
    assert [message.role for message in retried_messages] == ["system", "system", "tool", "user"]
    assert retried_messages[0].content == "SYSTEM"
    assert "# Compacted Conversation State" in retried_messages[1].content
    assert "## Preserved Recent Tail" in retried_messages[1].content
    assert "latest instruction" in retried_messages[1].content
    assert "A" * 5000 not in retried_messages[1].content
    assert retried_messages[2].tool_call_id == "tc1"
    assert retried_messages[3].content == "latest instruction"
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
            tool_result_session_id="chat-1",
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
