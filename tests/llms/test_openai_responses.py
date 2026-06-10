import asyncio
from types import SimpleNamespace

from opensprite.llms import ChatMessage
from opensprite.llms.openai_responses import OpenAIResponsesLLM


def _response(content="final answer", model="gpt-5.1-codex"):
    return SimpleNamespace(
        output_text=content,
        model=model,
        output=[],
        usage=None,
        status="completed",
    )


class RecordingResponses:
    def __init__(self, response=None):
        self.response = response or _response()
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _make_provider(responses):
    provider = object.__new__(OpenAIResponsesLLM)
    provider.api_key = "secret-key"
    provider.base_url = None
    provider.default_model = "gpt-5.1-codex"
    provider._client_kwargs = {"api_key": "secret-key"}
    provider.client = SimpleNamespace(responses=responses)
    return provider


def test_openai_responses_sends_minimal_request_payload_without_optional_params():
    responses = RecordingResponses()
    provider = _make_provider(responses)

    result = asyncio.run(provider.chat([ChatMessage(role="user", content="hello")]))

    assert result.content == "final answer"
    assert responses.calls == [
        {
            "model": "gpt-5.1-codex",
            "input": [{"role": "user", "content": "hello"}],
        }
    ]


def test_openai_responses_sends_max_output_tokens_only_when_set():
    responses = RecordingResponses()
    provider = _make_provider(responses)

    result = asyncio.run(
        provider.chat(
            [ChatMessage(role="user", content="hello")],
            model="gpt-5.1-codex-override",
            max_tokens=1234,
        )
    )

    assert result.content == "final answer"
    assert responses.calls[0]["model"] == "gpt-5.1-codex-override"
    assert responses.calls[0]["max_output_tokens"] == 1234
    assert "max_tokens" not in responses.calls[0]


def test_openai_responses_sends_reasoning_effort_without_summary():
    responses = RecordingResponses()
    provider = _make_provider(responses)
    provider.reasoning_config = {"enabled": True, "effort": "high"}

    result = asyncio.run(provider.chat([ChatMessage(role="user", content="hello")]))

    assert result.content == "final answer"
    assert responses.calls[0]["reasoning"] == {"effort": "high"}
    assert "summary" not in responses.calls[0]["reasoning"]


def test_openai_responses_sends_reasoning_none_when_disabled():
    responses = RecordingResponses()
    provider = _make_provider(responses)
    provider.reasoning_config = {"enabled": False}

    result = asyncio.run(provider.chat([ChatMessage(role="user", content="hello")]))

    assert result.content == "final answer"
    assert responses.calls[0]["reasoning"] == {"effort": "none"}


def test_openai_responses_converts_tools_without_tool_choice():
    responses = RecordingResponses()
    provider = _make_provider(responses)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Find a record.",
                "parameters": {"type": "object"},
            },
        }
    ]

    result = asyncio.run(provider.chat([ChatMessage(role="user", content="use tool")], tools=tools))

    assert result.content == "final answer"
    assert responses.calls[0]["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Find a record.",
            "parameters": {"type": "object"},
        }
    ]
    assert "tool_choice" not in responses.calls[0]


def test_openai_responses_streaming_sends_stream_flag():
    class FakeStream:
        def __aiter__(self):
            self._events = iter(
                [
                    SimpleNamespace(type="response.output_text.delta", delta="final "),
                    SimpleNamespace(type="response.output_text.delta", delta="answer"),
                ]
            )
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    responses = RecordingResponses(response=FakeStream())
    provider = _make_provider(responses)
    deltas = []

    async def on_delta(delta):
        deltas.append(delta)

    result = asyncio.run(
        provider.chat(
            [ChatMessage(role="user", content="hello")],
            response_delta_callback=on_delta,
        )
    )

    assert result.content == "final answer"
    assert deltas == ["final ", "answer"]
    assert responses.calls[0]["stream"] is True
