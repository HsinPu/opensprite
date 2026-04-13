import asyncio

from opensprite.media.router import MediaRouter


class FakeImageProvider:
    def __init__(self):
        self.calls = []

    async def analyze(self, instruction, images, *, model=None, max_tokens=2048):
        self.calls.append((instruction, list(images), model, max_tokens))
        return "analysis"


class FakeVideoProvider:
    def __init__(self):
        self.calls = []

    async def analyze(self, instruction, video_data_url, *, model=None, max_tokens=2048):
        self.calls.append((instruction, video_data_url, model, max_tokens))
        return "video analysis"


class EmptySpeechProvider:
    async def transcribe(self, audio_data_url, *, model=None, language=None):
        return ""


class EmptyImageProvider:
    async def analyze(self, instruction, images, *, model=None, max_tokens=2048):
        return ""


class EmptyVideoProvider:
    async def analyze(self, instruction, video_data_url, *, model=None, max_tokens=2048):
        return ""


def test_media_router_uses_image_provider_for_selected_image():
    provider = FakeImageProvider()
    router = MediaRouter(image_provider=provider)

    result = asyncio.run(
        router.analyze_image("describe it", ["img-a", "img-b"], image_index=1)
    )

    assert result == "analysis"
    assert provider.calls == [("describe it", ["img-b"], None, 2048)]


def test_media_router_reports_when_provider_is_unavailable():
    router = MediaRouter()

    result = asyncio.run(router.analyze_image("describe it", ["img-a"]))

    assert result == MediaRouter.IMAGE_PROVIDER_UNAVAILABLE


def test_media_router_uses_video_provider_for_selected_video():
    provider = FakeVideoProvider()
    router = MediaRouter(video_provider=provider)

    result = asyncio.run(
        router.analyze_video("describe the clip", ["vid-a", "vid-b"], video_index=1)
    )

    assert result == "video analysis"
    assert provider.calls == [("describe the clip", "vid-b", None, 2048)]


def test_media_router_reports_empty_image_provider_result():
    router = MediaRouter(image_provider=EmptyImageProvider())

    result = asyncio.run(router.analyze_image("describe it", ["img-a"]))

    assert result == MediaRouter.EMPTY_IMAGE_RESULT


def test_media_router_reports_empty_speech_provider_result():
    router = MediaRouter(speech_provider=EmptySpeechProvider())

    result = asyncio.run(router.transcribe_audio(["aud-a"]))

    assert result == MediaRouter.EMPTY_SPEECH_RESULT


def test_media_router_reports_empty_video_provider_result():
    router = MediaRouter(video_provider=EmptyVideoProvider())

    result = asyncio.run(router.analyze_video("describe the clip", ["vid-a"]))

    assert result == MediaRouter.EMPTY_VIDEO_RESULT
