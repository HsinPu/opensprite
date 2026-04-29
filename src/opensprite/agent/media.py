"""Media handling helpers for agent turns."""

from __future__ import annotations

import base64
import binascii
import time
from pathlib import Path
from typing import Callable

from ..context.paths import get_session_workspace
from ..utils.log import logger


OUTBOUND_MEDIA_KEYS = {
    "image": "images",
    "voice": "voices",
    "audio": "audios",
    "video": "videos",
}

INBOUND_IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

INBOUND_AUDIO_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/mp4": "m4a",
}

INBOUND_VIDEO_EXTENSIONS = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "video/x-matroska": "mkv",
}


class AgentMediaService:
    """Decode, persist, and format media attached to agent turns."""

    def __init__(
        self,
        *,
        workspace_root_getter: Callable[[], Path],
        app_home_getter: Callable[[], Path | None],
    ):
        self._workspace_root_getter = workspace_root_getter
        self._app_home_getter = app_home_getter

    @staticmethod
    def decode_data_url(payload: str, media_prefix: str) -> tuple[str, bytes] | None:
        """Decode a media data URL into a MIME type and bytes."""
        value = str(payload or "").strip()
        if not value.startswith("data:"):
            return None

        header, separator, encoded = value.partition(",")
        if not separator or ";base64" not in header.lower():
            return None

        mime_type = header[5:].split(";", 1)[0].strip().lower()
        if not mime_type.startswith(f"{media_prefix}/"):
            return None

        try:
            return mime_type, base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None

    def persist_inbound_media(
        self,
        session_id: str,
        media_items: list[str] | None,
        *,
        media_prefix: str,
        directory_name: str,
        extensions: dict[str, str],
    ) -> list[str]:
        """Persist inbound media data URLs under a session workspace directory."""
        if not media_items:
            return []

        workspace = get_session_workspace(
            session_id,
            workspace_root=self._workspace_root_getter(),
            app_home=self._app_home_getter(),
        )
        media_dir = workspace / directory_name
        saved_files: list[str] = []

        for index, item in enumerate(media_items, start=1):
            decoded = self.decode_data_url(item, media_prefix)
            if decoded is None:
                logger.warning(
                    "[{}] inbound.{}.persist.skip | index={} reason=unsupported-payload",
                    session_id,
                    media_prefix,
                    index,
                )
                continue

            mime_type, media_bytes = decoded
            extension = extensions.get(mime_type, "bin")
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                filename = f"inbound-{timestamp}-{time.time_ns()}-{index}.{extension}"
                target = media_dir / filename
                target.write_bytes(media_bytes)
                saved_files.append(target.relative_to(workspace).as_posix())
                logger.info(
                    "[{}] inbound.{}.persisted | file={}",
                    session_id,
                    media_prefix,
                    target,
                )
            except Exception as exc:
                logger.warning(
                    "[{}] inbound.{}.persist.failed | index={} error={}",
                    session_id,
                    media_prefix,
                    index,
                    exc,
                )

        return saved_files

    def persist_inbound_images(self, session_id: str, images: list[str] | None) -> list[str]:
        """Persist inbound image data URLs under the session workspace images directory."""
        return self.persist_inbound_media(
            session_id,
            images,
            media_prefix="image",
            directory_name="images",
            extensions=INBOUND_IMAGE_EXTENSIONS,
        )

    def persist_inbound_audios(self, session_id: str, audios: list[str] | None) -> list[str]:
        """Persist inbound audio data URLs under the session workspace audios directory."""
        return self.persist_inbound_media(
            session_id,
            audios,
            media_prefix="audio",
            directory_name="audios",
            extensions=INBOUND_AUDIO_EXTENSIONS,
        )

    def persist_inbound_videos(self, session_id: str, videos: list[str] | None) -> list[str]:
        """Persist inbound video data URLs under the session workspace videos directory."""
        return self.persist_inbound_media(
            session_id,
            videos,
            media_prefix="video",
            directory_name="videos",
            extensions=INBOUND_VIDEO_EXTENSIONS,
        )

    @staticmethod
    def is_media_only_message(
        *,
        text: str | None,
        images: list[str] | None,
        audios: list[str] | None,
        videos: list[str] | None,
    ) -> bool:
        """Return whether a turn only carries media without user instructions."""
        has_media = bool(images or audios or videos)
        return has_media and not (text or "").strip()

    @staticmethod
    def format_saved_media_history_content(
        *,
        image_files: list[str],
        audio_files: list[str],
        video_files: list[str],
    ) -> str:
        """Format saved media paths as readable user-message history content."""
        lines = ["[Media-only message saved to workspace]"]
        if image_files:
            lines.append("Images: " + ", ".join(image_files))
        if audio_files:
            lines.append("Audios: " + ", ".join(audio_files))
        if video_files:
            lines.append("Videos: " + ", ".join(video_files))
        return "\n".join(lines)

    @staticmethod
    def queue_outbound_media(
        media: dict[str, list[str]] | None,
        kind: str,
        payload: str,
    ) -> str | None:
        """Queue one media payload into the active turn's outbound media bucket."""
        if media is None:
            return "Error: outbound media can only be queued while processing a user message."

        key = OUTBOUND_MEDIA_KEYS.get(kind)
        if key is None:
            return f"Error: unsupported outbound media kind: {kind}"

        value = str(payload or "").strip()
        if not value:
            return "Error: outbound media payload cannot be empty."

        media.setdefault(key, []).append(value)
        return None

    @staticmethod
    def queued_outbound_media(media: dict[str, list[str]] | None) -> dict[str, list[str]]:
        """Return a stable outbound media shape for one assistant reply."""
        media = media or {}
        return {key: list(media.get(key) or []) for key in ("images", "voices", "audios", "videos")}

    @staticmethod
    def augment_message_for_media(
        current_message: str,
        user_images: list[str] | None,
        user_audios: list[str] | None,
        user_videos: list[str] | None,
        user_image_files: list[str] | None = None,
        user_audio_files: list[str] | None = None,
        user_video_files: list[str] | None = None,
    ) -> str:
        """Add lightweight prompt hints when the current turn includes media."""
        hints: list[str] = []
        if user_images:
            hints.append(
                f"User attached {len(user_images)} image(s). Use analyze_image or ocr_image only if "
                "the user's text asks for visual understanding or text extraction."
            )
            if user_image_files:
                hints.append(
                    f"Saved inbound image file(s) under the session workspace: {', '.join(user_image_files)}."
                )
        if user_audios:
            hints.append(
                f"User attached {len(user_audios)} audio clip(s). Use transcribe_audio only if "
                "the user's text asks for spoken content."
            )
            if user_audio_files:
                hints.append(
                    f"Saved inbound audio file(s) under the session workspace: {', '.join(user_audio_files)}."
                )
        if user_videos:
            hints.append(
                f"User attached {len(user_videos)} video clip(s). Use analyze_video only if "
                "the user's text asks for video understanding."
            )
            if user_video_files:
                hints.append(
                    f"Saved inbound video file(s) under the session workspace: {', '.join(user_video_files)}."
                )
        if not hints:
            return current_message
        return f"{current_message}\n\n[{ ' '.join(hints) }]"
