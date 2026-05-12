import asyncio
import base64

from opensprite.media.router import MediaRouter
from opensprite.tools.video import AnalyzeVideoTool


class FakeVideoProvider:
    def __init__(self):
        self.calls = []

    async def analyze(self, instruction, video_data_url, *, model=None, max_tokens=2048):
        self.calls.append((instruction, video_data_url))
        return "video result"


def test_analyze_video_tool_uses_current_turn_videos():
    provider = FakeVideoProvider()
    tool = AnalyzeVideoTool(MediaRouter(video_provider=provider), get_current_videos=lambda: ["vid-a"])

    result = asyncio.run(tool.execute(instruction="describe the clip"))

    assert result == "video result"
    assert provider.calls == [("describe the clip", "vid-a")]


def test_analyze_video_tool_reads_saved_session_video(tmp_path):
    provider = FakeVideoProvider()
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "inbound.mp4").write_bytes(b"mp4-bytes")
    tool = AnalyzeVideoTool(
        MediaRouter(video_provider=provider),
        get_current_videos=lambda: None,
        workspace_resolver=lambda: tmp_path,
    )

    result = asyncio.run(tool.execute(instruction="describe the saved clip", video_path="videos/inbound.mp4"))

    assert result == "video result"
    expected = "data:video/mp4;base64," + base64.b64encode(b"mp4-bytes").decode("utf-8")
    assert provider.calls == [("describe the saved clip", expected)]


def test_analyze_video_tool_rejects_saved_video_outside_workspace(tmp_path):
    provider = FakeVideoProvider()
    workspace = tmp_path / "session"
    workspace.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"mp4-bytes")
    tool = AnalyzeVideoTool(
        MediaRouter(video_provider=provider),
        get_current_videos=lambda: None,
        workspace_resolver=lambda: workspace,
    )

    result = asyncio.run(tool.execute(instruction="describe it", video_path="../outside.mp4"))

    assert result == "Error: saved video '../outside.mp4' was not found or is not a supported video file."
    assert provider.calls == []


def test_analyze_video_tool_reports_when_provider_is_unavailable():
    tool = AnalyzeVideoTool(MediaRouter(), get_current_videos=lambda: None)

    result = asyncio.run(tool.execute(instruction="describe the clip"))

    assert result == MediaRouter.VIDEO_PROVIDER_UNAVAILABLE
