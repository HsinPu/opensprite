import asyncio

import httpx

import opensprite.media.image as image_module
from opensprite.media.image import MiniMaxImageProvider, OpenAICompatibleImageProvider, create_image_analysis_provider


class _FakeMessage:
    content = "image summary"


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]


def test_minimax_image_provider_posts_to_coding_plan_vlm_endpoint():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"content": "a receipt"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MiniMaxImageProvider(
        api_key="secret-key",
        base_url="https://api.minimax.io/v1",
        client=client,
    )

    result = asyncio.run(provider.analyze("What is this?", ["data:image/jpeg;base64,abc"]))
    asyncio.run(client.aclose())

    assert result == "a receipt"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.minimax.io/v1/coding_plan/vlm"
    assert request.headers["authorization"] == "Bearer secret-key"
    assert request.read().decode("utf-8") == '{"prompt":"What is this?","image_url":"data:image/jpeg;base64,abc"}'


def test_minimax_image_provider_normalizes_cn_base_url():
    provider = MiniMaxImageProvider(api_key="secret-key", base_url="https://api.minimaxi.com/v1")

    assert provider.endpoint == "https://api.minimaxi.com/v1/coding_plan/vlm"


def test_create_image_analysis_provider_uses_minimax_provider_for_minimax_id():
    provider = create_image_analysis_provider(
        provider="minimax",
        api_key="secret-key",
        default_model="MiniMax-VL-01",
        base_url="https://api.minimax.io/v1",
    )

    assert isinstance(provider, MiniMaxImageProvider)


def test_create_image_analysis_provider_uses_openai_compatible_provider_for_openrouter():
    provider = create_image_analysis_provider(
        provider="openrouter",
        api_key="secret-key",
        default_model="google/gemini-3-flash-preview",
        base_url="https://openrouter.ai/api/v1",
    )

    assert isinstance(provider, OpenAICompatibleImageProvider)


def test_openai_compatible_provider_sends_openrouter_image_payload(monkeypatch):
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = type("FakeChat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(image_module, "AsyncOpenAI", FakeClient)
    provider = image_module.OpenAICompatibleImageProvider(
        api_key="secret-key",
        default_model="google/gemini-3-flash-preview",
        base_url="https://openrouter.ai/api/v1",
    )

    result = asyncio.run(provider.analyze("What is this?", ["data:image/jpeg;base64,abc"], max_tokens=123))

    assert result == "image summary"
    assert provider.client.kwargs == {"api_key": "secret-key", "base_url": "https://openrouter.ai/api/v1"}
    assert calls == [
        {
            "model": "google/gemini-3-flash-preview",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
                    ],
                }
            ],
            "max_tokens": 123,
        }
    ]
