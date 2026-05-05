"""Image analysis providers."""

from __future__ import annotations

import httpx
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


class MiniMaxImageProvider(ImageAnalysisProvider):
    """Image analysis provider backed by MiniMax Coding Plan VLM API."""

    DEFAULT_BASE_URL = "https://api.minimax.io"
    DEFAULT_MODEL = "MiniMax-VL-01"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.default_model = default_model or self.DEFAULT_MODEL
        self.base_url = self._normalize_base_url(base_url)
        self.timeout = timeout
        self._client = client

    @classmethod
    def _normalize_base_url(cls, base_url: str | None) -> str:
        value = str(base_url or "").strip().rstrip("/") or cls.DEFAULT_BASE_URL
        for suffix in ("/anthropic/v1/messages", "/anthropic/v1", "/anthropic", "/v1"):
            if value.endswith(suffix):
                value = value[: -len(suffix)].rstrip("/")
                break
        return value or cls.DEFAULT_BASE_URL

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v1/coding_plan/vlm"

    async def _post(self, payload: dict[str, str]) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self._client is not None:
            response = await self._client.post(self.endpoint, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.endpoint, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {}
        base_resp = data.get("base_resp")
        if isinstance(base_resp, dict) and int(base_resp.get("status_code") or 0) != 0:
            message = str(base_resp.get("status_msg") or "MiniMax image analysis failed")
            raise RuntimeError(message)
        return data

    async def analyze(
        self,
        instruction: str,
        images: list[str],
        *,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        if not images:
            return ""
        payload = {
            "prompt": instruction or "Describe the image.",
            "image_url": images[0],
        }
        data = await self._post(payload)
        return str(data.get("content") or "")


def create_image_analysis_provider(
    *,
    provider: str,
    api_key: str,
    default_model: str,
    base_url: str | None = None,
) -> ImageAnalysisProvider:
    """Create the correct image provider for one configured media provider."""
    provider_id = str(provider or "").strip().lower()
    if provider_id == "minimax":
        return MiniMaxImageProvider(api_key=api_key, default_model=default_model, base_url=base_url)
    return OpenAICompatibleImageProvider(api_key=api_key, default_model=default_model, base_url=base_url)
