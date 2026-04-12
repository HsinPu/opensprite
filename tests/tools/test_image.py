import asyncio

from opensprite.media.router import MediaRouter
from opensprite.tools.image import AnalyzeImageTool


class FakeImageProvider:
    def __init__(self):
        self.calls = []

    async def analyze(self, instruction, images, *, model=None, max_tokens=2048):
        self.calls.append((instruction, list(images)))
        return "image result"


def test_analyze_image_tool_uses_current_turn_images():
    provider = FakeImageProvider()
    tool = AnalyzeImageTool(MediaRouter(image_provider=provider), get_current_images=lambda: ["img-a"]) 

    result = asyncio.run(tool.execute(instruction="describe the screenshot"))

    assert result == "image result"
    assert provider.calls == [("describe the screenshot", ["img-a"])]


def test_analyze_image_tool_reports_when_no_images_are_available():
    tool = AnalyzeImageTool(MediaRouter(), get_current_images=lambda: None)

    result = asyncio.run(tool.execute(instruction="describe the screenshot"))

    assert result == MediaRouter.IMAGE_PROVIDER_UNAVAILABLE
