import asyncio
import base64

from opensprite.media.router import MediaRouter
from opensprite.tools.audio import TranscribeAudioTool


class FakeSpeechProvider:
    def __init__(self):
        self.calls = []

    async def transcribe(self, audio_data_url, *, model=None, language=None):
        self.calls.append((audio_data_url, model, language))
        return "hello world"


def test_transcribe_audio_tool_uses_current_turn_audio():
    provider = FakeSpeechProvider()
    tool = TranscribeAudioTool(MediaRouter(speech_provider=provider), get_current_audios=lambda: ["aud-a"]) 

    result = asyncio.run(tool.execute(language="en"))

    assert result == "hello world"
    assert provider.calls == [("aud-a", None, "en")]


def test_transcribe_audio_tool_reads_saved_session_audio(tmp_path):
    provider = FakeSpeechProvider()
    audio_dir = tmp_path / "audios"
    audio_dir.mkdir()
    (audio_dir / "inbound.ogg").write_bytes(b"ogg-bytes")
    tool = TranscribeAudioTool(
        MediaRouter(speech_provider=provider),
        get_current_audios=lambda: None,
        workspace_resolver=lambda: tmp_path,
    )

    result = asyncio.run(tool.execute(audio_path="audios/inbound.ogg", language="zh"))

    assert result == "hello world"
    expected = "data:audio/ogg;base64," + base64.b64encode(b"ogg-bytes").decode("utf-8")
    assert provider.calls == [(expected, None, "zh")]


def test_transcribe_audio_tool_rejects_saved_audio_outside_workspace(tmp_path):
    provider = FakeSpeechProvider()
    workspace = tmp_path / "session"
    workspace.mkdir()
    outside = tmp_path / "outside.ogg"
    outside.write_bytes(b"ogg-bytes")
    tool = TranscribeAudioTool(
        MediaRouter(speech_provider=provider),
        get_current_audios=lambda: None,
        workspace_resolver=lambda: workspace,
    )

    result = asyncio.run(tool.execute(audio_path="../outside.ogg"))

    assert result == "Error: saved audio '../outside.ogg' was not found or is not a supported audio file."
    assert provider.calls == []


def test_transcribe_audio_tool_reports_when_provider_is_unavailable():
    tool = TranscribeAudioTool(MediaRouter(), get_current_audios=lambda: None)

    result = asyncio.run(tool.execute())

    assert result == MediaRouter.SPEECH_PROVIDER_UNAVAILABLE
