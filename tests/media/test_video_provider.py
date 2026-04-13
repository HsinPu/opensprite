import asyncio
from types import SimpleNamespace

import opensprite.media.video as video_module


def test_openai_compatible_video_provider_builds_video_url_payload(monkeypatch):
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="video analysis"))]
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(video_module, "AsyncOpenAI", FakeAsyncOpenAI)

    provider = video_module.OpenAICompatibleVideoProvider(api_key="k", default_model="minimax-video")
    result = asyncio.run(provider.analyze("describe the clip", "data:video/mp4;base64,AAAA"))

    assert result == "video analysis"
    assert captured["model"] == "minimax-video"
    assert captured["messages"][0]["content"][0] == {"type": "text", "text": "describe the clip"}
    assert captured["messages"][0]["content"][1] == {
        "type": "video_url",
        "video_url": {"url": "data:video/mp4;base64,AAAA"},
    }
