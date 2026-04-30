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


def _chunk(text, model="gpt-test"):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
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


def test_openai_uses_non_streaming_call_when_tools_are_present():
    completions = FakeCompletions(_message_response("tool path"))
    llm = _make_llm(completions)

    async def scenario():
        async def on_delta(delta):
            raise AssertionError(f"unexpected delta: {delta}")

        return await llm.chat(
            [ChatMessage(role="user", content="hi")],
            tools=[{"type": "function", "function": {"name": "demo", "parameters": {}}}],
            response_delta_callback=on_delta,
        )

    response = asyncio.run(scenario())

    assert response.content == "tool path"
    assert "stream" not in completions.calls[0]
