"""Prepare per-turn input data for AgentLoop.process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..bus.message import UserMessage
from ..utils.log import logger
from .media import AgentMediaService


@dataclass(frozen=True)
class PreparedTurnInput:
    """Resolved user turn data used by process orchestration."""

    session_id: str
    channel: str | None
    external_chat_id: str | None
    image_files: list[str]
    audio_files: list[str]
    video_files: list[str]
    user_metadata: dict[str, Any]
    assistant_metadata: dict[str, Any]


class TurnInputPreparer:
    """Resolves turn ids, persists inbound media, and builds message metadata."""

    def __init__(
        self,
        *,
        media_service: AgentMediaService,
        format_log_preview: Callable[..., str],
    ):
        self.media_service = media_service
        self._format_log_preview = format_log_preview

    def prepare(self, user_message: UserMessage) -> PreparedTurnInput:
        """Prepare all process input fields derived directly from the inbound message."""
        session_id = user_message.session_id or user_message.external_chat_id or "default"
        channel = user_message.channel or None

        if ":" not in session_id:
            logger.warning(
                "Received non-namespaced session_id '{}' in Agent.process; this may mix sessions if MessageQueue is bypassed",
                session_id,
            )

        sender = user_message.sender_name or user_message.sender_id or "-"
        logger.info(
            f"[{session_id}] inbound | channel={channel or '-'} sender={sender} images={len(user_message.images or [])} "
            f"text={self._format_log_preview(user_message.text, max_chars=200)}"
        )
        image_files = self.media_service.persist_inbound_images(session_id, user_message.images)
        audio_files = self.media_service.persist_inbound_audios(session_id, user_message.audios)
        video_files = self.media_service.persist_inbound_videos(session_id, user_message.videos)

        user_metadata = {
            **dict(user_message.metadata or {}),
            "channel": channel,
            "external_chat_id": user_message.external_chat_id,
            "sender_id": user_message.sender_id,
            "sender_name": user_message.sender_name,
            "images_count": len(user_message.images or []),
            "image_files": image_files or None,
            "images_dir": "images" if image_files else None,
            "audios_count": len(user_message.audios or []),
            "audio_files": audio_files or None,
            "audios_dir": "audios" if audio_files else None,
            "videos_count": len(user_message.videos or []),
            "video_files": video_files or None,
            "videos_dir": "videos" if video_files else None,
        }
        user_metadata = {key: value for key, value in user_metadata.items() if value is not None}
        assistant_metadata = {
            "channel": channel,
            "external_chat_id": user_message.external_chat_id,
        }
        assistant_metadata = {key: value for key, value in assistant_metadata.items() if value is not None}
        external_chat_id = str(user_message.external_chat_id) if user_message.external_chat_id is not None else None

        return PreparedTurnInput(
            session_id=session_id,
            channel=channel,
            external_chat_id=external_chat_id,
            image_files=image_files,
            audio_files=audio_files,
            video_files=video_files,
            user_metadata=user_metadata,
            assistant_metadata=assistant_metadata,
        )
