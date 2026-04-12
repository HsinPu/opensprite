"""Routing helpers for media analysis providers."""

from __future__ import annotations

from .base import ImageAnalysisProvider


class MediaRouter:
    """Route media analysis calls to configured providers."""

    IMAGE_PROVIDER_UNAVAILABLE = (
        "Error: image analysis is unavailable because no vision provider is configured."
    )

    def __init__(self, *, image_provider: ImageAnalysisProvider | None = None):
        self.image_provider = image_provider

    async def analyze_image(
        self,
        instruction: str,
        images: list[str],
        *,
        image_index: int = 0,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """Analyze one image from the current turn."""
        if self.image_provider is None:
            return self.IMAGE_PROVIDER_UNAVAILABLE
        if not images:
            return "Error: no images are available in the current turn."
        if image_index < 0 or image_index >= len(images):
            return f"Error: image_index {image_index} is out of range for {len(images)} image(s)."
        return await self.image_provider.analyze(
            instruction,
            [images[image_index]],
            model=model,
            max_tokens=max_tokens,
        )
