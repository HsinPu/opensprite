"""Video analysis tool for OpenSprite."""

from __future__ import annotations

from typing import Any, Callable

from ..media import MediaRouter
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


class AnalyzeVideoTool(Tool):
    """Tool to analyze video clips attached to the current user turn."""

    def __init__(
        self,
        media_router: MediaRouter,
        *,
        get_current_videos: Callable[[], list[str] | None],
    ):
        self._media_router = media_router
        self._get_current_videos = get_current_videos

    @property
    def name(self) -> str:
        return "analyze_video"

    @property
    def description(self) -> str:
        return (
            "Analyze one video clip from the current user turn. "
            "Use this when the user attached a video and the task requires understanding what happens in it."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Required. What to analyze in the video and what kind of answer is needed.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "video_index": {
                    "type": "integer",
                    "description": "Optional. Zero-based index into the current turn's attached video clips. Defaults to 0.",
                    "default": 0,
                    "minimum": 0,
                },
            },
            "required": ["instruction"],
        }

    async def _execute(self, instruction: str, video_index: int = 0, **kwargs: Any) -> str:
        videos = self._get_current_videos() or []
        return await self._media_router.analyze_video(
            instruction=instruction,
            videos=videos,
            video_index=video_index,
        )
