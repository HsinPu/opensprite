"""Audio input preprocessing for agent turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..bus.message import UserMessage
from .turn_input import PreparedTurnInput


@dataclass(frozen=True)
class AudioInputPreprocessResult:
    """Outcome from optional audio-to-text preprocessing."""

    transcribed: bool = False
    status: str = "skipped"
    audio_files: tuple[str, ...] = ()
    transcript_len: int = 0


class AudioInputPreprocessor:
    """Convert dictated audio into text before the LLM sees the turn."""

    DICTATION_MODES = frozenset({"dictation", "voice"})
    UPLOAD_MODES = frozenset({"upload", "file"})

    def __init__(self, transcribe_audio: Callable[[list[str]], Awaitable[str]]):
        self._transcribe_audio = transcribe_audio

    @staticmethod
    def is_audio_only_message(user_message: UserMessage) -> bool:
        """Return whether a turn only carries audio and no written instruction."""
        return (
            bool(user_message.audios)
            and not bool(user_message.images or user_message.videos)
            and not (user_message.text or "").strip()
        )

    @classmethod
    def should_pretranscribe(cls, user_message: UserMessage) -> bool:
        """Return whether pure audio should be treated as dictated user text."""
        if not cls.is_audio_only_message(user_message):
            return False
        metadata = user_message.metadata if isinstance(user_message.metadata, dict) else {}
        mode = str(metadata.get("audio_input_mode") or "").strip().lower()
        if mode in cls.DICTATION_MODES:
            return True
        if mode in cls.UPLOAD_MODES:
            return False
        audio_kinds = metadata.get("audio_kinds")
        return isinstance(audio_kinds, list) and bool(audio_kinds) and all(kind == "voice" for kind in audio_kinds)

    async def preprocess(
        self,
        user_message: UserMessage,
        turn: PreparedTurnInput,
    ) -> AudioInputPreprocessResult:
        """Turn pure dictated audio into text before task classification and LLM prompting."""
        if not self.should_pretranscribe(user_message):
            return AudioInputPreprocessResult()

        transcript = (await self._transcribe_audio(list(user_message.audios or []))).strip()
        metadata = user_message.metadata
        if transcript.startswith("Error:"):
            metadata["audio_transcription_error"] = transcript
            user_message.text = transcript
            status = "failed"
        else:
            metadata["audio_transcript"] = transcript
            user_message.text = self.format_transcript_message(transcript, turn.audio_files)
            status = "completed"
        user_message.audios = None
        return AudioInputPreprocessResult(
            transcribed=True,
            status=status,
            audio_files=tuple(turn.audio_files),
            transcript_len=len(transcript),
        )

    @staticmethod
    def format_transcript_message(transcript: str, audio_files: list[str]) -> str:
        """Combine dictated text with the saved source path for LLM context."""
        text = transcript.strip()
        if audio_files:
            text = f"{text}\n\n[Uploaded file path(s): {', '.join(audio_files)}]"
        return text

    @staticmethod
    def audio_files_for_llm(user_message: UserMessage, turn: PreparedTurnInput) -> list[str] | None:
        """Hide already-transcribed audio attachments from media tool hints."""
        if "audio_transcript" in user_message.metadata or "audio_transcription_error" in user_message.metadata:
            return None
        return turn.audio_files
