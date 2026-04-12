"""OpenAI-compatible image analysis provider."""

from __future__ import annotations

from openai import AsyncOpenAI

from .base import ImageAnalysisProvider


class OpenAICompatibleImageProvider(ImageAnalysisProvider):
    """Image analysis provider backed by an OpenAI-compatible chat API."""

    def __init__(self, *, api_key: str, default_model: str, base_url: str | None = None):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.default_model = default_model

    async def analyze(
        self,
        instruction: str,
        images: list[str],
        *,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        user_content = [{"type": "text", "text": instruction or "Describe the provided image."}]
        for image in images:
            user_content.append({"type": "image_url", "image_url": {"url": image}})

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
