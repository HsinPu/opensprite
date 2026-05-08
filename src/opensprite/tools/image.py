"""Image analysis tool for OpenSprite."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Callable

from ..media import MediaRouter
from .base import Tool
from .evidence import ToolEvidence, indexed_resource_id
from .validation import NON_EMPTY_STRING_PATTERN


SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


def _load_saved_image_data_url(workspace: Path, image_path: str) -> str | None:
    """Load a saved session-workspace image as a base64 data URL."""
    relative_path = str(image_path or "").strip().replace("\\", "/")
    if not relative_path:
        return None
    if relative_path.startswith("/") or ":" in Path(relative_path).parts[0]:
        return None

    workspace_root = Path(workspace).expanduser().resolve()
    target = (workspace_root / relative_path).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError:
        return None
    if not target.is_file():
        return None

    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        return None
    b64 = base64.b64encode(target.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def _resolve_images(
    *,
    current_images: list[str] | None,
    workspace_resolver: Callable[[], Path] | None,
    image_path: str = "",
) -> tuple[list[str], str | None]:
    """Resolve images from either current turn attachments or a saved workspace path."""
    if not image_path.strip():
        return list(current_images or []), None
    if workspace_resolver is None:
        return [], "Error: saved image lookup is unavailable because no session workspace is active."
    try:
        image = _load_saved_image_data_url(workspace_resolver(), image_path)
    except OSError as exc:
        return [], f"Error: failed to read saved image '{image_path}': {exc}"
    if image is None:
        return [], f"Error: saved image '{image_path}' was not found or is not a supported image file."
    return [image], None


def _image_tool_evidence(tool_name: str, args: dict[str, Any], result: str, *, ok: bool) -> ToolEvidence:
    resource_ids: list[str] = []
    image_path = str(args.get("image_path") or "").strip().replace("\\", "/")
    if image_path:
        resource_ids.append(f"image:{image_path}")
    else:
        resource_ids.append(indexed_resource_id("image_index", args.get("image_index")))
    return ToolEvidence(
        name=tool_name,
        args=dict(args or {}),
        ok=ok,
        resource_ids=tuple(dict.fromkeys(resource_ids)),
        result_preview=str(result or "")[:240],
    )


class AnalyzeImageTool(Tool):
    """Tool to analyze images attached to the current user turn."""

    def __init__(
        self,
        media_router: MediaRouter,
        *,
        get_current_images: Callable[[], list[str] | None],
        workspace_resolver: Callable[[], Path] | None = None,
    ):
        self._media_router = media_router
        self._get_current_images = get_current_images
        self._workspace_resolver = workspace_resolver

    @property
    def name(self) -> str:
        return "analyze_image"

    @property
    def description(self) -> str:
        return (
            "Analyze one image from the current user turn or a saved image in the session workspace. "
            "Use this when the user attached an image, or refers to an earlier saved photo, and you need visual understanding before answering."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Required. What to analyze in the image and what kind of answer is needed.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "image_index": {
                    "type": "integer",
                    "description": "Optional. Zero-based index into the current turn's attached images. Defaults to 0.",
                    "default": 0,
                    "minimum": 0,
                },
                "image_path": {
                    "type": "string",
                    "description": "Optional. Relative path to a saved image in the current session workspace, such as images/inbound-....jpg. Use this to inspect a photo saved in an earlier turn.",
                },
            },
            "required": ["instruction"],
        }

    async def _execute(self, instruction: str, image_index: int = 0, image_path: str = "", **kwargs: Any) -> str:
        images, error = _resolve_images(
            current_images=self._get_current_images(),
            workspace_resolver=self._workspace_resolver,
            image_path=image_path,
        )
        if error:
            return error
        effective_index = 0 if image_path.strip() else image_index
        return await self._media_router.analyze_image(
            instruction=instruction,
            images=images,
            image_index=effective_index,
        )

    def build_evidence(self, params: Any, result: str, *, ok: bool) -> ToolEvidence:
        args = params if isinstance(params, dict) else {}
        return _image_tool_evidence(self.name, args, result, ok=ok)


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
        workspace_resolver: Callable[[], Path] | None = None,
    ):
        self._media_router = media_router
        self._get_current_images = get_current_images
        self._workspace_resolver = workspace_resolver

    @property
    def name(self) -> str:
        return "ocr_image"

    @property
    def description(self) -> str:
        return (
            "Extract visible text from one image in the current user turn or a saved image in the session workspace. "
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
                "image_path": {
                    "type": "string",
                    "description": "Optional. Relative path to a saved image in the current session workspace, such as images/inbound-....jpg. Use this to OCR a photo saved in an earlier turn.",
                },
            },
        }

    async def _execute(self, image_index: int = 0, instruction: str = "", image_path: str = "", **kwargs: Any) -> str:
        images, error = _resolve_images(
            current_images=self._get_current_images(),
            workspace_resolver=self._workspace_resolver,
            image_path=image_path,
        )
        if error:
            return error
        effective_index = 0 if image_path.strip() else image_index
        final_instruction = self.DEFAULT_INSTRUCTION
        if instruction.strip():
            final_instruction = f"{self.DEFAULT_INSTRUCTION}\n\nAdditional instruction: {instruction.strip()}"
        return await self._media_router.ocr_image(
            instruction=final_instruction,
            images=images,
            image_index=effective_index,
        )

    def build_evidence(self, params: Any, result: str, *, ok: bool) -> ToolEvidence:
        args = params if isinstance(params, dict) else {}
        return _image_tool_evidence(self.name, args, result, ok=ok)
