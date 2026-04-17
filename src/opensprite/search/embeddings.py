"""Embedding helpers for hybrid search reranking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from openai import AsyncOpenAI


class EmbeddingProvider(ABC):
    """Abstract embedding provider used by the SQLite search store."""

    provider_name: str
    model_name: str
    batch_size: int

    @abstractmethod
    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embeddings for the provided texts in the same order."""


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding provider."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider_name: str = "openai",
        base_url: str | None = None,
        batch_size: int = 16,
    ):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.provider_name = provider_name
        self.model_name = model
        self.batch_size = batch_size

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        cleaned = [str(text or "") for text in texts]
        if not cleaned:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(cleaned), self.batch_size):
            batch = cleaned[start : start + self.batch_size]
            response = await self.client.embeddings.create(model=self.model_name, input=batch)
            vectors.extend([list(item.embedding) for item in response.data])
        return vectors
