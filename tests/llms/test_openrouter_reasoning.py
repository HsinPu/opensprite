import asyncio
from types import SimpleNamespace

from opensprite.llms import ChatMessage
from opensprite.llms.openrouter import OpenRouterLLM
from opensprite.llms.request_log_fields import request_param_log_fields


def _openrouter_response(content="final answer", model="anthropic/claude-sonnet-4.6"):
    return SimpleNamespace(
        id="response-id",
        model=model,
        object="chat.completion",
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content, tool_calls=None, reasoning_details=None),
            )
        ],
    )


class RecordingCompletions:
    def __init__(self, response=None):
        self.response = response or _openrouter_response()
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _make_provider(completions, default_model="anthropic/claude-sonnet-4.6"):
    provider = OpenRouterLLM(api_key="secret-key", default_model=default_model)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider


def test_openrouter_client_uses_openrouter_headers_and_longer_timeout():
    provider = OpenRouterLLM(api_key="secret-key", default_model="qwen/qwen3.6-27b")

    headers = provider._client_kwargs["default_headers"]
    timeout = provider._client_kwargs["timeout"]

    assert headers["HTTP-Referer"] == "https://github.com/HsinPu/opensprite"
    assert headers["X-OpenRouter-Title"] == "OpenSprite"
    assert headers["X-Title"] == "OpenSprite"
    assert timeout.connect == 20.0
    assert timeout.read == 120.0


def test_openrouter_request_log_fields_use_shared_sanitized_fields():
    fields = request_param_log_fields(
        {
            "model": "google/gemini-3-flash-preview",
            "messages": [{"role": "user", "content": "do not log this"}],
            "tools": [{"type": "function", "function": {"name": "secret_tool"}}],
            "tool_choice": "auto",
            "stream": True,
            "max_tokens": 123,
            "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
        }
    )

    assert fields == {
        "mode": "main_chat",
        "model": "google/gemini-3-flash-preview",
        "messages": 1,
        "tools": 1,
        "tool_choice": "auto",
        "stream": True,
        "max_tokens": 123,
        "reasoning": '{"effort":"high","enabled":true}',
    }
    assert "do not log this" not in str(fields)
    assert "secret_tool" not in str(fields)


def test_openrouter_chat_sends_reasoning_enabled_by_default():
    completions = RecordingCompletions()
    provider = _make_provider(completions)

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="hello")]))

    assert response.content == "final answer"
    assert completions.calls == [
        {
            "model": "anthropic/claude-sonnet-4.6",
            "messages": [{"role": "user", "content": "hello"}],
            "extra_body": {"reasoning": {"enabled": True}},
        }
    ]


def test_openrouter_chat_sends_max_tokens_only_when_set():
    completions = RecordingCompletions()
    provider = _make_provider(completions)

    response = asyncio.run(
        provider.chat(
            [ChatMessage(role="user", content="hello")],
            model="google/gemini-3-flash-preview",
            max_tokens=1234,
        )
    )

    assert response.content == "final answer"
    assert completions.calls[0]["model"] == "google/gemini-3-flash-preview"
    assert completions.calls[0]["max_tokens"] == 1234


def test_openrouter_chat_sends_tools_with_auto_tool_choice():
    completions = RecordingCompletions()
    provider = _make_provider(completions)
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="use tool")], tools=tools))

    assert response.content == "final answer"
    assert completions.calls[0]["tools"] == tools
    assert completions.calls[0]["tool_choice"] == "auto"


def test_openrouter_chat_preserves_reasoning_details_in_non_streaming_calls():
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="response-id",
                model="anthropic/claude-sonnet-4.6",
                object="chat.completion",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content="final answer",
                            tool_calls=None,
                            reasoning_details=[{"type": "reasoning.text", "text": "thinking"}],
                        ),
                    )
                ],
            )

    provider = OpenRouterLLM(api_key="secret-key", default_model="anthropic/claude-sonnet-4.6")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = asyncio.run(
        provider.chat(
            [
                ChatMessage(
                    role="assistant",
                    content="previous answer",
                    reasoning_details=[{"type": "reasoning.text", "text": "previous thinking"}],
                ),
                ChatMessage(role="user", content="continue"),
            ]
        )
    )

    assert response.content == "final answer"
    assert response.reasoning_details == [{"type": "reasoning.text", "text": "thinking"}]
    assert "provider" not in calls[0]
    assert "reasoning" not in calls[0]
    assert calls[0]["extra_body"] == {"reasoning": {"enabled": True}}
    assert calls[0]["messages"][0]["reasoning_details"] == [
        {"type": "reasoning.text", "text": "previous thinking"}
    ]


def test_openrouter_chat_does_not_send_provider_request_options():
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="response-id",
                model="anthropic/claude-sonnet-4.6",
                object="chat.completion",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="final answer", tool_calls=None, reasoning_details=None),
                    )
                ],
            )

    provider = OpenRouterLLM(
        api_key="secret-key",
        default_model="anthropic/claude-sonnet-4.6",
    )
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="think")]))

    assert response.content == "final answer"
    assert "provider" not in calls[0]
    assert "reasoning" not in calls[0]
    assert calls[0]["extra_body"] == {"reasoning": {"enabled": True}}


def test_openrouter_chat_sends_configured_reasoning_effort_for_supported_models():
    completions = RecordingCompletions(response=_openrouter_response(model="google/gemini-3-flash-preview"))
    provider = _make_provider(completions, default_model="google/gemini-3-flash-preview")
    provider.reasoning_effort = "high"
    provider.reasoning_config = {"enabled": True, "effort": "high"}

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="think")]))

    assert response.content == "final answer"
    assert completions.calls[0]["extra_body"] == {"reasoning": {"enabled": True, "effort": "high"}}


def test_openrouter_chat_sends_reasoning_disabled_when_configured():
    completions = RecordingCompletions(response=_openrouter_response(model="google/gemini-3-flash-preview"))
    provider = OpenRouterLLM(
        api_key="secret-key",
        default_model="google/gemini-3-flash-preview",
        reasoning_effort="none",
    )
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="think")]))

    assert response.content == "final answer"
    assert completions.calls[0]["extra_body"] == {"reasoning": {"enabled": False}}


def test_openrouter_chat_sends_reasoning_body_for_anthropic_models():
    completions = RecordingCompletions()
    provider = OpenRouterLLM(
        api_key="secret-key",
        default_model="anthropic/claude-sonnet-4.6",
        reasoning_effort="high",
    )
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="think")]))

    assert response.content == "final answer"
    assert completions.calls[0]["extra_body"] == {"reasoning": {"enabled": True, "effort": "high"}}


def test_openrouter_stream_collects_reasoning_details_and_emits_reasoning_delta():
    class FakeStream:
        def __aiter__(self):
            self._chunks = iter(
                [
                    SimpleNamespace(
                        model="google/gemini-3-flash-preview",
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content="",
                                    reasoning_details=[{"type": "reasoning.text", "text": "thinking"}],
                                )
                            )
                        ],
                    ),
                    SimpleNamespace(
                        model="google/gemini-3-flash-preview",
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="final answer"))],
                    ),
                ]
            )
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["stream"] is True
            return FakeStream()

    reasoning_deltas = []

    async def on_reasoning_delta(delta: str):
        reasoning_deltas.append(delta)

    async def on_response_delta(delta: str):
        _ = delta

    provider = OpenRouterLLM(api_key="secret-key", default_model="google/gemini-3-flash-preview")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = asyncio.run(
        provider.chat(
            [ChatMessage(role="user", content="think")],
            response_delta_callback=on_response_delta,
            reasoning_delta_callback=on_reasoning_delta,
        )
    )

    assert response.content == "final answer"
    assert response.reasoning_details == [{"type": "reasoning.text", "text": "thinking"}]
    assert reasoning_deltas == ["thinking"]
