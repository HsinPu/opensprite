import asyncio
import base64

from opensprite.media.router import MediaRouter
from opensprite.tools.image import AnalyzeImageTool, OCRImageTool
from opensprite.tools.result_status import classify_tool_result_status


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


def test_analyze_image_tool_reads_saved_session_image(tmp_path):
    provider = FakeImageProvider()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "inbound.jpg"
    image_path.write_bytes(b"jpeg-bytes")
    tool = AnalyzeImageTool(
        MediaRouter(image_provider=provider),
        get_current_images=lambda: None,
        workspace_resolver=lambda: tmp_path,
    )

    result = asyncio.run(tool.execute(instruction="describe the saved photo", image_path="images/inbound.jpg"))

    assert result == "image result"
    expected = "data:image/jpeg;base64," + base64.b64encode(b"jpeg-bytes").decode("utf-8")
    assert provider.calls == [("describe the saved photo", [expected])]


def test_analyze_image_tool_rejects_saved_image_outside_workspace(tmp_path):
    provider = FakeImageProvider()
    workspace = tmp_path / "session"
    workspace.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"jpeg-bytes")
    tool = AnalyzeImageTool(
        MediaRouter(image_provider=provider),
        get_current_images=lambda: None,
        workspace_resolver=lambda: workspace,
    )

    result = asyncio.run(tool.execute(instruction="describe it", image_path="../outside.jpg"))

    status = classify_tool_result_status(result)
    assert status.ok is False
    assert status.error_type == "SavedMediaError"
    assert status.category == "saved_media_not_found"
    assert "saved image '../outside.jpg'" in status.error
    assert provider.calls == []


def test_analyze_image_tool_reports_when_no_images_are_available():
    tool = AnalyzeImageTool(MediaRouter(), get_current_images=lambda: None)

    result = asyncio.run(tool.execute(instruction="describe the screenshot"))

    assert result == MediaRouter.IMAGE_PROVIDER_UNAVAILABLE


def test_ocr_image_tool_uses_default_ocr_instruction():
    provider = FakeImageProvider()
    tool = OCRImageTool(MediaRouter(ocr_provider=provider), get_current_images=lambda: ["img-a"])

    result = asyncio.run(tool.execute())

    assert result == "image result"
    assert provider.calls == [(
        "Extract all visible text from the image as accurately as possible. Preserve line breaks when helpful and do not add commentary unless asked.",
        ["img-a"],
    )]


def test_ocr_image_tool_appends_extra_instruction():
    provider = FakeImageProvider()
    tool = OCRImageTool(MediaRouter(ocr_provider=provider), get_current_images=lambda: ["img-a"])

    asyncio.run(tool.execute(instruction="Focus on the error message only"))

    assert "Additional instruction: Focus on the error message only" in provider.calls[0][0]


def test_ocr_image_tool_reads_saved_session_image(tmp_path):
    provider = FakeImageProvider()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "receipt.png").write_bytes(b"png-bytes")
    tool = OCRImageTool(
        MediaRouter(ocr_provider=provider),
        get_current_images=lambda: None,
        workspace_resolver=lambda: tmp_path,
    )

    result = asyncio.run(tool.execute(image_path="images/receipt.png"))

    assert result == "image result"
    expected = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("utf-8")
    assert provider.calls[0][1] == [expected]


def test_ocr_image_tool_reports_when_no_ocr_provider_is_available():
    tool = OCRImageTool(MediaRouter(image_provider=FakeImageProvider()), get_current_images=lambda: ["img-a"])

    result = asyncio.run(tool.execute())

    assert result == MediaRouter.OCR_PROVIDER_UNAVAILABLE
