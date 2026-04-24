"""Outbound media attachment tool for OpenSprite."""

from __future__ import annotations

from typing import Any, Callable

from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


class SendMediaTool(Tool):
    """Tool to attach media to the next assistant reply."""

    def __init__(
        self,
        *,
        queue_media: Callable[[str, str], str | None],
        get_current_images: Callable[[], list[str] | None],
        get_current_audios: Callable[[], list[str] | None],
        get_current_videos: Callable[[], list[str] | None],
    ):
        self._queue_media = queue_media
        self._get_current_images = get_current_images
        self._get_current_audios = get_current_audios
        self._get_current_videos = get_current_videos

    @property
    def name(self) -> str:
        return "send_media"

    @property
    def description(self) -> str:
        return (
            "Attach an image, voice message, audio file, or video to the next assistant reply. "
            "Use this when the user asks you to send or return media. Provide payload as a data URL, URL, "
            "or platform file id; omit payload to resend media attached to the current user turn."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["image", "voice", "audio", "video"],
                    "description": "Required media kind to attach to the reply.",
                },
                "payload": {
                    "type": "string",
                    "description": (
                        "Optional media payload: data URL, URL, or platform file id. "
                        "If omitted, the tool uses the current turn's attached media at media_index."
                    ),
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "media_index": {
                    "type": "integer",
                    "description": "Zero-based index of current turn media to resend when payload is omitted. Defaults to 0.",
                    "default": 0,
                    "minimum": 0,
                },
            },
            "required": ["kind"],
        }

    def _current_media_for_kind(self, kind: str) -> list[str]:
        if kind == "image":
            return self._get_current_images() or []
        if kind in {"voice", "audio"}:
            return self._get_current_audios() or []
        if kind == "video":
            return self._get_current_videos() or []
        return []

    async def _execute(self, kind: str, payload: str = "", media_index: int = 0, **kwargs: Any) -> str:
        final_payload = payload.strip()
        if not final_payload:
            media_items = self._current_media_for_kind(kind)
            if not media_items:
                return f"Error: No current {kind} media is available to send."
            if media_index >= len(media_items):
                return f"Error: media_index {media_index} is out of range for {len(media_items)} current {kind} item(s)."
            final_payload = media_items[media_index]

        error = self._queue_media(kind, final_payload)
        if error:
            return error
        return f"Queued {kind} media for the next assistant reply."
