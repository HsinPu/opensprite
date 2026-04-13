"""Media analysis providers and routers."""

from .audio import OpenAICompatibleSpeechProvider
from .base import ImageAnalysisProvider, SpeechToTextProvider, VideoAnalysisProvider
from .image import OpenAICompatibleImageProvider
from .video import OpenAICompatibleVideoProvider
from .router import MediaRouter

__all__ = [
    "ImageAnalysisProvider",
    "SpeechToTextProvider",
    "VideoAnalysisProvider",
    "MediaRouter",
    "OpenAICompatibleImageProvider",
    "OpenAICompatibleSpeechProvider",
    "OpenAICompatibleVideoProvider",
]
