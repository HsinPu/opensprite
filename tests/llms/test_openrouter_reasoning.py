import asyncio
from types import SimpleNamespace

from opensprite.llms import ChatMessage
from opensprite.llms.openrouter import OpenRouterLLM


def test_openrouter_client_uses_openrouter_headers_and_longer_timeout():
    provider = OpenRouterLLM(api_key="secret-key", default_model="qwen/qwen3.6-27b")

    headers = provider._client_kwargs["default_headers"]
    timeout = provider._client_kwargs["timeout"]

    assert headers["HTTP-Referer"] == "https://github.com/HsinPu/opensprite"
    assert headers["X-OpenRouter-Title"] == "OpenSprite"
    assert headers["X-Title"] == "OpenSprite"
    assert timeout.connect == 20.0
    assert timeout.read == 120.0


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
    assert calls[0]["extra_body"]["reasoning"] == {"effort": "medium"}
    assert calls[0]["messages"][0]["reasoning_details"] == [
        {"type": "reasoning.text", "text": "previous thinking"}
    ]


def test_openrouter_chat_sends_optional_request_settings_when_configured():
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
        reasoning_enabled=True,
        reasoning_effort="high",
        reasoning_exclude=True,
        provider_sort="throughput",
        require_parameters=True,
    )
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="think")]))

    assert response.content == "final answer"
    assert calls[0]["extra_body"]["reasoning"] == {"effort": "high", "exclude": True}
    assert calls[0]["extra_body"]["provider"] == {"sort": "throughput", "require_parameters": True}


def test_openrouter_chat_retries_without_reasoning_when_reasoning_fails():
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("extra_body", {}).get("reasoning"):
                raise RuntimeError("unsupported reasoning parameter")
            return SimpleNamespace(
                id="response-id",
                model="vendor/no-reasoning-model",
                object="chat.completion",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="fallback answer", tool_calls=None, reasoning_details=None),
                    )
                ],
            )

    provider = OpenRouterLLM(api_key="secret-key", default_model="vendor/no-reasoning-model")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="hello")]))

    assert response.content == "fallback answer"
    assert calls[0]["extra_body"]["reasoning"] == {"effort": "medium"}
    assert "extra_body" not in calls[1]
    assert provider.reasoning_enabled is False


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
