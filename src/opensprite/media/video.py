"""OpenAI-compatible video analysis provider."""

from __future__ import annotations

from openai import AsyncOpenAI

from .base import VideoAnalysisProvider


class OpenAICompatibleVideoProvider(VideoAnalysisProvider):
    """Video analysis provider backed by an OpenAI-compatible chat API."""

    def __init__(self, *, api_key: str, default_model: str, base_url: str | None = None):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.default_model = default_model

    async def analyze(
        self,
        instruction: str,
        video_data_url: str,
        *,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        user_content = [
            {"type": "text", "text": instruction or "Describe the provided video."},
            {"type": "video_url", "video_url": {"url": video_data_url}},
        ]

        response = await self.client.chat.completions.create(
            model=model or self.default_model,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=max_tokens,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = choices[0].message
        return getattr(message, "content", "") or ""
