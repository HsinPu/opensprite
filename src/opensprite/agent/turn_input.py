"""User turn input preparation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..bus.message import UserMessage
from ..media import (
    AgentMediaService,
    INBOUND_AUDIO_EXTENSIONS,
    INBOUND_IMAGE_EXTENSIONS,
    INBOUND_VIDEO_EXTENSIONS,
)
from ..utils.log import logger
from ..utils.url import join_url_path


QUICK_ACTION_METADATA_KEY = "quick_action"
TURN_SOURCE_METADATA_KEY = "source"
CLI_VIA_WEB_TURN_SOURCE = "cli_via_web"
RESUME_FOLLOW_UP_QUICK_ACTION = "resume_follow_up"
RUN_VERIFICATION_QUICK_ACTION = "run_verification"


def metadata_is_cli_via_web(metadata: dict[str, Any]) -> bool:
    return metadata_value_matches(metadata, TURN_SOURCE_METADATA_KEY, CLI_VIA_WEB_TURN_SOURCE)


def metadata_requests_follow_up_resume(metadata: dict[str, Any]) -> bool:
    return metadata_value_matches(metadata, QUICK_ACTION_METADATA_KEY, RESUME_FOLLOW_UP_QUICK_ACTION)


def metadata_requests_direct_verification(metadata: dict[str, Any]) -> bool:
    return metadata_value_matches(metadata, QUICK_ACTION_METADATA_KEY, RUN_VERIFICATION_QUICK_ACTION)


def normalized_policy_text(value: Any) -> str:
    return str(value or "").strip()


def metadata_text(metadata: dict[str, Any], key: str, default: Any = "") -> str:
    return normalized_policy_text(metadata.get(key) or default)


def metadata_value_matches(metadata: dict[str, Any], key: str, expected: str) -> bool:
    return metadata_text(metadata, key) == expected


@dataclass(frozen=True)
class PreparedTurnInput:
    """Resolved user turn data used by process orchestration."""

    session_id: str
    channel: str | None
    external_chat_id: str | None
    image_files: list[str]
    audio_files: list[str]
    video_files: list[str]
    media_events: list[dict[str, Any]]
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
        image_result = self.media_service.persist_inbound_media_with_events(
            session_id,
            user_message.images,
            media_prefix="image",
            directory_name="images",
            extensions=INBOUND_IMAGE_EXTENSIONS,
        )
        audio_result = self.media_service.persist_inbound_media_with_events(
            session_id,
            user_message.audios,
            media_prefix="audio",
            directory_name="audios",
            extensions=INBOUND_AUDIO_EXTENSIONS,
        )
        video_result = self.media_service.persist_inbound_media_with_events(
            session_id,
            user_message.videos,
            media_prefix="video",
            directory_name="videos",
            extensions=INBOUND_VIDEO_EXTENSIONS,
        )
        image_files = image_result.files
        audio_files = audio_result.files
        video_files = video_result.files
        media_events = [*image_result.events, *audio_result.events, *video_result.events]

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
            media_events=media_events,
            user_metadata=user_metadata,
            assistant_metadata=assistant_metadata,
        )


def message_with_runtime_context(message: str, metadata: dict[str, Any] | None) -> str:
    data = dict(metadata or {})
    if not metadata_is_cli_via_web(data):
        return message
    context_lines: list[str] = []
    gateway_url = metadata_text(data, "gateway_url")
    if gateway_url:
        health_url = join_url_path(gateway_url, "/healthz")
        context_lines.append(
            f"OpenSprite CLI is connected to the Web gateway at {gateway_url}; "
            f"use {health_url} for health endpoint checks."
        )
    snapshot = data.get("workspace_snapshot")
    if isinstance(snapshot, dict):
        snapshot_path = metadata_text(snapshot, "path")
        snapshot_source = metadata_text(snapshot, "source")
        if snapshot_path:
            context_lines.append(
                f"The requested workspace snapshot is available inside this session at `{snapshot_path}/`."
            )
        if snapshot_source:
            context_lines.append(f"The snapshot came from local path `{snapshot_source}`.")
        context_lines.append("Snapshot copies omit VCS internals such as `.git`.")
    if not context_lines:
        return message
    return f"{message}\n\n[Runtime context]\n" + "\n".join(f"- {line}" for line in context_lines)
