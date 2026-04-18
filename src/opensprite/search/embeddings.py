"""Embedding helpers for hybrid search reranking."""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import math
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


class LocalHashEmbeddingProvider(EmbeddingProvider):
    """Deterministic local embedding provider for testing and benchmarking."""

    def __init__(self, *, model: str = "local-hash-embedding", dimensions: int = 64, batch_size: int = 64):
        self.provider_name = "local"
        self.model_name = model
        self.dimensions = dimensions
        self.batch_size = batch_size

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(str(text or "")) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        buckets = [0.0] * self.dimensions
        tokens = text.lower().split()
        if not tokens:
            return buckets
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            buckets[bucket] += sign
        norm = math.sqrt(sum(value * value for value in buckets))
        if norm == 0:
            return buckets
        return [value / norm for value in buckets]
