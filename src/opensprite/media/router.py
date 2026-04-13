"""Routing helpers for media analysis providers."""

from __future__ import annotations

from .base import ImageAnalysisProvider, SpeechToTextProvider, VideoAnalysisProvider


class MediaRouter:
    """Route media analysis calls to configured providers."""

    IMAGE_PROVIDER_UNAVAILABLE = (
        "Error: image analysis is unavailable because no vision provider is configured."
    )
    SPEECH_PROVIDER_UNAVAILABLE = (
        "Error: audio transcription is unavailable because no speech provider is configured."
    )
    VIDEO_PROVIDER_UNAVAILABLE = (
        "Error: video analysis is unavailable because no video provider is configured."
    )
    EMPTY_IMAGE_RESULT = "Error: image analysis provider returned no usable result."
    EMPTY_SPEECH_RESULT = "Error: speech provider returned no transcription text."
    EMPTY_VIDEO_RESULT = "Error: video analysis provider returned no usable result."

    def __init__(
        self,
        *,
        image_provider: ImageAnalysisProvider | None = None,
        speech_provider: SpeechToTextProvider | None = None,
        video_provider: VideoAnalysisProvider | None = None,
    ):
        self.image_provider = image_provider
        self.speech_provider = speech_provider
        self.video_provider = video_provider

    async def analyze_image(
        self,
        instruction: str,
        images: list[str],
        *,
        image_index: int = 0,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """Analyze one image from the current turn."""
        if self.image_provider is None:
            return self.IMAGE_PROVIDER_UNAVAILABLE
        if not images:
            return "Error: no images are available in the current turn."
        if image_index < 0 or image_index >= len(images):
            return f"Error: image_index {image_index} is out of range for {len(images)} image(s)."
        result = await self.image_provider.analyze(
            instruction,
            [images[image_index]],
            model=model,
            max_tokens=max_tokens,
        )
        return result if result.strip() else self.EMPTY_IMAGE_RESULT

    async def transcribe_audio(
        self,
        audios: list[str],
        *,
        audio_index: int = 0,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transcribe one audio clip from the current turn."""
        if self.speech_provider is None:
            return self.SPEECH_PROVIDER_UNAVAILABLE
        if not audios:
            return "Error: no audio is available in the current turn."
        if audio_index < 0 or audio_index >= len(audios):
            return f"Error: audio_index {audio_index} is out of range for {len(audios)} audio clip(s)."
        result = await self.speech_provider.transcribe(
            audios[audio_index],
            model=model,
            language=language,
        )
        return result if result.strip() else self.EMPTY_SPEECH_RESULT

    async def analyze_video(
        self,
        instruction: str,
        videos: list[str],
        *,
        video_index: int = 0,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """Analyze one video clip from the current turn."""
        if self.video_provider is None:
            return self.VIDEO_PROVIDER_UNAVAILABLE
        if not videos:
            return "Error: no videos are available in the current turn."
        if video_index < 0 or video_index >= len(videos):
            return f"Error: video_index {video_index} is out of range for {len(videos)} video clip(s)."
        result = await self.video_provider.analyze(
            instruction,
            videos[video_index],
            model=model,
            max_tokens=max_tokens,
        )
        return result if result.strip() else self.EMPTY_VIDEO_RESULT
