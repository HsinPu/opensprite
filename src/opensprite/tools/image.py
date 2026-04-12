"""Image analysis tool for OpenSprite."""

from __future__ import annotations

from typing import Any, Callable

from ..media import MediaRouter
from .base import Tool


class AnalyzeImageTool(Tool):
    """Tool to analyze images attached to the current user turn."""

    def __init__(
        self,
        media_router: MediaRouter,
        *,
        get_current_images: Callable[[], list[str] | None],
    ):
        self._media_router = media_router
        self._get_current_images = get_current_images

    @property
    def name(self) -> str:
        return "analyze_image"

    @property
    def description(self) -> str:
        return (
            "Analyze one image from the current user turn. "
            "Use this when the user attached an image and you need visual understanding before answering."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Required. What to analyze in the image and what kind of answer is needed.",
                },
                "image_index": {
                    "type": "integer",
                    "description": "Optional. Zero-based index into the current turn's attached images. Defaults to 0.",
                    "default": 0,
                    "minimum": 0,
                },
            },
            "required": ["instruction"],
        }

    async def execute(self, instruction: str, image_index: int = 0, **kwargs: Any) -> str:
        images = self._get_current_images() or []
        if not instruction.strip():
            return "Error: instruction is required for analyze_image."
        return await self._media_router.analyze_image(
            instruction=instruction,
            images=images,
            image_index=image_index,
        )


class OCRImageTool(Tool):
    """Tool to extract visible text from images attached to the current user turn."""

    DEFAULT_INSTRUCTION = (
        "Extract all visible text from the image as accurately as possible. "
        "Preserve line breaks when helpful and do not add commentary unless asked."
    )

    def __init__(
        self,
        media_router: MediaRouter,
        *,
        get_current_images: Callable[[], list[str] | None],
    ):
        self._media_router = media_router
        self._get_current_images = get_current_images

    @property
    def name(self) -> str:
        return "ocr_image"

    @property
    def description(self) -> str:
        return (
            "Extract visible text from one image in the current user turn. "
            "Use this for screenshots, receipts, documents, or photos where the user mainly needs the text content."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_index": {
                    "type": "integer",
                    "description": "Optional. Zero-based index into the current turn's attached images. Defaults to 0.",
                    "default": 0,
                    "minimum": 0,
                },
                "instruction": {
                    "type": "string",
                    "description": "Optional. Extra OCR guidance, such as focusing on a section, language, or formatting need.",
                },
            },
        }

    async def execute(self, image_index: int = 0, instruction: str = "", **kwargs: Any) -> str:
        images = self._get_current_images() or []
        final_instruction = self.DEFAULT_INSTRUCTION
        if instruction.strip():
            final_instruction = f"{self.DEFAULT_INSTRUCTION}\n\nAdditional instruction: {instruction.strip()}"
        return await self._media_router.analyze_image(
            instruction=final_instruction,
            images=images,
            image_index=image_index,
        )
