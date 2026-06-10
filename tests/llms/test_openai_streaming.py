import asyncio
from types import SimpleNamespace

from opensprite.llms.base import ChatMessage
from opensprite.llms.openai import OpenAILLM


class AsyncChunkStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **params):
        self.calls.append(params)
        return self.response


def _chunk(text, model="gpt-test", reasoning=None):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, reasoning_content=reasoning))],
    )


def _tool_chunk(index=0, call_id=None, name=None, arguments=None, model="gpt-test"):
    payload = {}
    if name is not None or arguments is not None:
        payload["function"] = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(delta=SimpleNamespace(tool_calls=[SimpleNamespace(index=index, id=call_id, **payload)]))],
    )


def _message_response(content="done", model="gpt-test"):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
    )


def _make_llm(completions):
    llm = object.__new__(OpenAILLM)
    llm.api_key = "test"
    llm.base_url = None
    llm.default_model = "gpt-test"
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm


def test_openai_streams_text_deltas_without_tools():
    completions = FakeCompletions(AsyncChunkStream([_chunk("hel"), _chunk("lo")]))
    llm = _make_llm(completions)
    deltas = []

    async def scenario():
        async def on_delta(delta):
            deltas.append(delta)

        return await llm.chat(
            [ChatMessage(role="user", content="hi")],
            response_delta_callback=on_delta,
        )

    response = asyncio.run(scenario())

    assert response.content == "hello"
    assert response.model == "gpt-test"
    assert deltas == ["hel", "lo"]
    assert completions.calls[0]["stream"] is True


def test_openai_chat_sends_minimal_request_payload_without_optional_params():
    completions = FakeCompletions(_message_response("final answer"))
    llm = _make_llm(completions)

    response = asyncio.run(llm.chat([ChatMessage(role="user", content="hello")]))

    assert response.content == "final answer"
    assert completions.calls == [
        {
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        }
    ]


def test_openai_chat_sends_max_tokens_only_when_set():
    completions = FakeCompletions(_message_response("final answer"))
    llm = _make_llm(completions)

    response = asyncio.run(
        llm.chat(
            [ChatMessage(role="user", content="hello")],
            model="gpt-override",
            max_tokens=1234,
        )
    )

    assert response.content == "final answer"
    assert completions.calls[0]["model"] == "gpt-override"
    assert completions.calls[0]["max_tokens"] == 1234


def test_openai_chat_sends_reasoning_effort_for_reasoning_models():
    completions = FakeCompletions(_message_response("final answer", model="gpt-5.5"))
    llm = _make_llm(completions)
    llm.reasoning_config = {"enabled": True, "effort": "high"}

    response = asyncio.run(llm.chat([ChatMessage(role="user", content="hello")], model="gpt-5.5"))

    assert response.content == "final answer"
    assert completions.calls[0]["reasoning_effort"] == "high"


def test_openai_chat_uses_max_completion_tokens_for_reasoning_models():
    completions = FakeCompletions(_message_response("final answer", model="gpt-5.5"))
    llm = _make_llm(completions)
    llm.reasoning_config = {"enabled": True}

    response = asyncio.run(
        llm.chat([ChatMessage(role="user", content="hello")], model="gpt-5.5", max_tokens=1234)
    )

    assert response.content == "final answer"
    assert completions.calls[0]["max_completion_tokens"] == 1234
    assert "max_tokens" not in completions.calls[0]


def test_openai_chat_omits_reasoning_effort_for_non_reasoning_models():
    completions = FakeCompletions(_message_response("final answer", model="gpt-4o-mini"))
    llm = _make_llm(completions)
    llm.reasoning_config = {"enabled": True, "effort": "high"}

    response = asyncio.run(llm.chat([ChatMessage(role="user", content="hello")], model="gpt-4o-mini"))

    assert response.content == "final answer"
    assert "reasoning_effort" not in completions.calls[0]


def test_openai_chat_sends_reasoning_none_when_disabled():
    completions = FakeCompletions(_message_response("final answer", model="gpt-5.1"))
    llm = _make_llm(completions)
    llm.reasoning_config = {"enabled": False}

    response = asyncio.run(llm.chat([ChatMessage(role="user", content="hello")], model="gpt-5.1"))

    assert response.content == "final answer"
    assert completions.calls[0]["reasoning_effort"] == "none"


def test_openai_chat_sends_tools_with_auto_tool_choice():
    completions = FakeCompletions(_message_response("final answer"))
    llm = _make_llm(completions)
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]

    response = asyncio.run(llm.chat([ChatMessage(role="user", content="use tool")], tools=tools))

    assert response.content == "final answer"
    assert completions.calls[0]["tools"] == tools
    assert completions.calls[0]["tool_choice"] == "auto"


def test_openai_streams_and_assembles_tool_calls():
    completions = FakeCompletions(
        AsyncChunkStream([
            _chunk("Checking "),
            _tool_chunk(index=0, call_id="call-1", name="demo", arguments='{"value"'),
            _tool_chunk(index=0, arguments=':"abc"}'),
            _chunk("done"),
        ])
    )
    llm = _make_llm(completions)
    deltas = []

    async def scenario():
        async def on_delta(delta):
            deltas.append(delta)

        async def on_tool_input(call_id, tool_name, delta, sequence):
            deltas.append((call_id, tool_name, delta, sequence))

        return await llm.chat(
            [ChatMessage(role="user", content="hi")],
            tools=[{"type": "function", "function": {"name": "demo", "parameters": {}}}],
            response_delta_callback=on_delta,
            tool_input_delta_callback=on_tool_input,
        )

    response = asyncio.run(scenario())

    assert response.content == "Checking done"
    assert deltas == ["Checking ", ("call-1", "demo", '{"value"', 1), ("call-1", "demo", ':"abc"}', 2), "done"]
    assert completions.calls[0]["stream"] is True
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "demo"
    assert response.tool_calls[0].arguments == {"value": "abc"}


def test_openai_streams_reasoning_deltas_without_visible_output():
    completions = FakeCompletions(AsyncChunkStream([_chunk("", reasoning="think "), _chunk("done", reasoning="more")]))
    llm = _make_llm(completions)
    reasoning = []

    async def scenario():
        async def on_delta(delta):
            pass

        async def on_reasoning(delta):
            reasoning.append(delta)

        return await llm.chat(
            [ChatMessage(role="user", content="hi")],
            response_delta_callback=on_delta,
            reasoning_delta_callback=on_reasoning,
        )

    response = asyncio.run(scenario())

    assert response.content == "done"
    assert reasoning == ["think ", "more"]
