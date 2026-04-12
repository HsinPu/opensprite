"""Media provider interfaces for OpenSprite."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ImageAnalysisProvider(ABC):
    """Provider interface for image understanding."""

    @abstractmethod
    async def analyze(
        self,
        instruction: str,
        images: list[str],
        *,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """Analyze one or more images and return a text result."""
        raise NotImplementedError
