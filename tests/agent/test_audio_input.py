import asyncio

from opensprite.agent.audio_input import AudioInputPreprocessor
from opensprite.agent.turn_input import PreparedTurnInput
from opensprite.bus.message import UserMessage


def _turn(audio_files=None):
    return PreparedTurnInput(
        session_id="telegram:room-1",
        channel="telegram",
        external_chat_id="room-1",
        image_files=[],
        audio_files=list(audio_files or []),
        video_files=[],
        media_events=[],
        user_metadata={},
        assistant_metadata={},
    )


def test_audio_input_preprocessor_treats_voice_kind_as_dictation():
    assert AudioInputPreprocessor.should_pretranscribe(
        UserMessage(text="", audios=["aud"], metadata={"audio_kinds": ["voice"]})
    )


def test_audio_input_preprocessor_treats_audio_kind_as_upload():
    assert not AudioInputPreprocessor.should_pretranscribe(
        UserMessage(text="", audios=["aud"], metadata={"audio_kinds": ["audio"]})
    )


def test_audio_input_preprocessor_supports_channel_neutral_dictation_mode():
    assert AudioInputPreprocessor.should_pretranscribe(
        UserMessage(text="", audios=["aud"], metadata={"audio_input_mode": "dictation"})
    )


def test_audio_input_preprocessor_supports_channel_neutral_upload_mode():
    assert not AudioInputPreprocessor.should_pretranscribe(
        UserMessage(text="", audios=["aud"], metadata={"audio_input_mode": "upload", "audio_kinds": ["voice"]})
    )


def test_audio_input_preprocessor_replaces_audio_with_transcript_text():
    async def transcribe(audios):
        assert audios == ["aud"]
        return "請幫我查明天行程"

    message = UserMessage(text="", audios=["aud"], metadata={"audio_input_mode": "dictation"})
    result = asyncio.run(AudioInputPreprocessor(transcribe).preprocess(message, _turn(["audios/inbound-1.ogg"])))

    assert result.transcribed is True
    assert result.status == "completed"
    assert message.audios is None
    assert message.metadata["audio_transcript"] == "請幫我查明天行程"
    assert message.text == "請幫我查明天行程\n\n[Uploaded file path(s): audios/inbound-1.ogg]"
    assert AudioInputPreprocessor.audio_files_for_llm(message, _turn(["audios/inbound-1.ogg"])) is None
