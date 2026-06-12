"""Task-local context for one agent turn."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from ..media import AgentMediaService


class TurnContextService:
    """Activates task-local context for one user message turn."""

    def __init__(
        self,
        *,
        current_session_id: ContextVar[str | None],
        current_channel: ContextVar[str | None],
        current_external_chat_id: ContextVar[str | None],
        current_images: ContextVar[list[str] | None],
        current_audios: ContextVar[list[str] | None],
        current_videos: ContextVar[list[str] | None],
        current_outbound_media: ContextVar[dict[str, list[str]] | None],
        current_run_id: ContextVar[str | None],
        current_work_progress: ContextVar[dict[str, Any] | None],
    ):
        self._current_session_id = current_session_id
        self._current_channel = current_channel
        self._current_external_chat_id = current_external_chat_id
        self._current_images = current_images
        self._current_audios = current_audios
        self._current_videos = current_videos
        self._current_outbound_media = current_outbound_media
        self._current_run_id = current_run_id
        self._current_work_progress = current_work_progress

    def current_session_id(self) -> str | None:
        """Return the current task-local session id."""
        return self._current_session_id.get()

    def current_channel(self) -> str | None:
        """Return the current task-local channel."""
        return self._current_channel.get()

    def current_external_chat_id(self) -> str | None:
        """Return the current transport-level chat id."""
        return self._current_external_chat_id.get()

    def current_images(self) -> list[str] | None:
        """Return images attached to the current active turn."""
        return self._current_images.get()

    def current_audios(self) -> list[str] | None:
        """Return audios attached to the current active turn."""
        return self._current_audios.get()

    def current_videos(self) -> list[str] | None:
        """Return videos attached to the current active turn."""
        return self._current_videos.get()

    def current_run_id(self) -> str | None:
        """Return the current task-local run id."""
        return self._current_run_id.get()

    def queue_outbound_media(self, kind: str, payload: str) -> str | None:
        """Queue one media payload to be attached to the current assistant reply."""
        return AgentMediaService.queue_outbound_media(self._current_outbound_media.get(), kind, payload)

    def queued_outbound_media(self) -> dict[str, list[str]]:
        """Return queued outbound media for the current turn."""
        return AgentMediaService.queued_outbound_media(self._current_outbound_media.get())

    def reset_work_progress(self) -> None:
        """Reset per-pass progress signals while keeping turn context active."""
        self._current_work_progress.set(self._default_work_progress())

    def note_file_change(self, path: str) -> None:
        """Record one file-change signal for the active pass."""
        state = self._current_work_progress.get()
        if state is None:
            return
        normalized_path = str(path or "").strip()
        state["file_change_count"] = int(state.get("file_change_count", 0)) + 1
        if normalized_path and normalized_path not in state["touched_paths"]:
            state["touched_paths"].append(normalized_path)

    def snapshot_work_progress(self) -> dict[str, Any]:
        """Return the current per-pass progress signals."""
        state = self._current_work_progress.get() or self._default_work_progress()
        return {
            "file_change_count": int(state.get("file_change_count", 0)),
            "touched_paths": tuple(str(path) for path in state.get("touched_paths", []) if str(path).strip()),
        }

    @staticmethod
    def _default_work_progress() -> dict[str, Any]:
        return {"file_change_count": 0, "touched_paths": []}

    @contextmanager
    def activate(
        self,
        *,
        session_id: str,
        channel: str | None,
        external_chat_id: str | None,
        images: list[str] | None,
        audios: list[str] | None,
        videos: list[str] | None,
        run_id: str,
    ) -> Iterator[None]:
        """Set per-turn context values and reset them in reverse order."""
        token = self._current_session_id.set(session_id)
        channel_token = self._current_channel.set(channel)
        external_chat_id_token = self._current_external_chat_id.set(external_chat_id)
        images_token = self._current_images.set(list(images or []))
        audios_token = self._current_audios.set(list(audios or []))
        videos_token = self._current_videos.set(list(videos or []))
        outbound_media_token = self._current_outbound_media.set(
            {"images": [], "voices": [], "audios": [], "videos": []}
        )
        run_token = self._current_run_id.set(run_id)
        work_progress_token = self._current_work_progress.set(self._default_work_progress())
        try:
            yield
        finally:
            self._current_work_progress.reset(work_progress_token)
            self._current_run_id.reset(run_token)
            self._current_outbound_media.reset(outbound_media_token)
            self._current_videos.reset(videos_token)
            self._current_audios.reset(audios_token)
            self._current_images.reset(images_token)
            self._current_external_chat_id.reset(external_chat_id_token)
            self._current_channel.reset(channel_token)
            self._current_session_id.reset(token)
