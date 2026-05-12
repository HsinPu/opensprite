"""Audio transcription tool for OpenSprite."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Callable

from ..media import MediaRouter
from .base import Tool
from .evidence import ToolEvidence, indexed_resource_id


SUPPORTED_AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/mp4",
}


def _load_saved_audio_data_url(workspace: Path, audio_path: str) -> str | None:
    """Load a saved session-workspace audio file as a base64 data URL."""
    relative_path = str(audio_path or "").strip().replace("\\", "/")
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
    if mime_type not in SUPPORTED_AUDIO_MIME_TYPES:
        return None
    b64 = base64.b64encode(target.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def _resolve_audios(
    *,
    current_audios: list[str] | None,
    workspace_resolver: Callable[[], Path] | None,
    audio_path: str = "",
) -> tuple[list[str], str | None]:
    """Resolve audio from either current turn attachments or a saved workspace path."""
    if not audio_path.strip():
        return list(current_audios or []), None
    if workspace_resolver is None:
        return [], "Error: saved audio lookup is unavailable because no session workspace is active."
    try:
        audio = _load_saved_audio_data_url(workspace_resolver(), audio_path)
    except OSError as exc:
        return [], f"Error: failed to read saved audio '{audio_path}': {exc}"
    if audio is None:
        return [], f"Error: saved audio '{audio_path}' was not found or is not a supported audio file."
    return [audio], None


class TranscribeAudioTool(Tool):
    """Tool to transcribe audio clips attached to the current user turn."""

    def __init__(
        self,
        media_router: MediaRouter,
        *,
        get_current_audios: Callable[[], list[str] | None],
        workspace_resolver: Callable[[], Path] | None = None,
    ):
        self._media_router = media_router
        self._get_current_audios = get_current_audios
        self._workspace_resolver = workspace_resolver

    @property
    def name(self) -> str:
        return "transcribe_audio"

    @property
    def description(self) -> str:
        return (
            "Transcribe one audio clip from the current user turn or a saved audio file in the session workspace into text. "
            "Use this for voice messages, spoken notes, recorded content, or earlier saved audio when you need the words."
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
                "audio_path": {
                    "type": "string",
                    "description": "Optional. Relative path to a saved audio file in the current session workspace, such as audios/inbound-....ogg. Use this to transcribe audio saved in an earlier turn.",
                },
            },
        }

    async def _execute(self, audio_index: int = 0, language: str | None = None, audio_path: str = "", **kwargs: Any) -> str:
        audios, error = _resolve_audios(
            current_audios=self._get_current_audios(),
            workspace_resolver=self._workspace_resolver,
            audio_path=audio_path,
        )
        if error:
            return error
        effective_index = 0 if audio_path.strip() else audio_index
        return await self._media_router.transcribe_audio(
            audios,
            audio_index=effective_index,
            language=language,
        )

    def build_evidence(self, params: Any, result: str, *, ok: bool) -> ToolEvidence:
        args = params if isinstance(params, dict) else {}
        audio_path = str(args.get("audio_path") or "").strip().replace("\\", "/")
        resource_id = f"audio:{audio_path}" if audio_path else indexed_resource_id("audio_index", args.get("audio_index"))
        return ToolEvidence(
            name=self.name,
            args=dict(args or {}),
            ok=ok,
            resource_ids=(resource_id,),
            result_preview=str(result or "")[:240],
        )
