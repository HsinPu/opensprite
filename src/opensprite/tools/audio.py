"""Audio transcription tool for OpenSprite."""

from __future__ import annotations

from typing import Any, Callable

from ..media import MediaRouter
from .base import Tool


class TranscribeAudioTool(Tool):
    """Tool to transcribe audio clips attached to the current user turn."""

    def __init__(
        self,
        media_router: MediaRouter,
        *,
        get_current_audios: Callable[[], list[str] | None],
    ):
        self._media_router = media_router
        self._get_current_audios = get_current_audios

    @property
    def name(self) -> str:
        return "transcribe_audio"

    @property
    def description(self) -> str:
        return (
            "Transcribe one audio clip from the current user turn into text. "
            "Use this for voice messages, spoken notes, or recorded content when you need the words."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "audio_index": {
                    "type": "integer",
                    "description": "Optional. Zero-based index into the current turn's attached audio clips. Defaults to 0.",
                    "default": 0,
                    "minimum": 0,
                },
                "language": {
                    "type": "string",
                    "description": "Optional language hint for transcription, such as en or zh.",
                },
            },
        }

    async def _execute(self, audio_index: int = 0, language: str | None = None, **kwargs: Any) -> str:
        audios = self._get_current_audios() or []
        return await self._media_router.transcribe_audio(
            audios,
            audio_index=audio_index,
            language=language,
        )
