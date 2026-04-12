import asyncio

from opensprite.media.router import MediaRouter


class FakeImageProvider:
    def __init__(self):
        self.calls = []

    async def analyze(self, instruction, images, *, model=None, max_tokens=2048):
        self.calls.append((instruction, list(images), model, max_tokens))
        return "analysis"


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
