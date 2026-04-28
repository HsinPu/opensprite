import asyncio
import base64
from types import SimpleNamespace

from opensprite.bus.message import AssistantMessage
from opensprite.channels.telegram import TelegramAdapter


class FakeFile:
    def __init__(self, payload: bytes):
        self._payload = payload

    async def download_as_bytearray(self):
        return bytearray(self._payload)


class FakeBot:
    def __init__(self):
        self.message_calls = []
        self.photo_calls = []
        self.voice_calls = []
        self.audio_calls = []
        self.video_calls = []

    async def get_file(self, file_id):
        return FakeFile(b"audio-bytes")

    async def send_message(self, chat_id, text, parse_mode=None):
        self.message_calls.append((chat_id, text, parse_mode))

    async def send_photo(self, chat_id, photo):
        self.photo_calls.append((chat_id, photo))

    async def send_voice(self, chat_id, voice):
        self.voice_calls.append((chat_id, voice))

    async def send_audio(self, chat_id, audio):
        self.audio_calls.append((chat_id, audio))

    async def send_video(self, chat_id, video):
        self.video_calls.append((chat_id, video))


def _data_url(mime_type: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def test_telegram_adapter_downloads_voice_message_as_audio_data_url():
    async def scenario():
        adapter = TelegramAdapter("token")
        update = SimpleNamespace(
            update_id=1,
            bot=FakeBot(),
            message=SimpleNamespace(
                text=None,
                caption=None,
                from_user=SimpleNamespace(id=1, username="alice", full_name="Alice"),
                chat=SimpleNamespace(id=123, type="private"),
                message_id=7,
                photo=None,
                voice=SimpleNamespace(file_id="voice-1", mime_type="audio/ogg"),
                audio=None,
            ),
        )
        return await adapter.to_user_message(update)

    user_message = asyncio.run(scenario())

    assert user_message.audios is not None
    assert len(user_message.audios) == 1
    assert user_message.audios[0].startswith("data:audio/ogg;base64,")


def test_telegram_adapter_downloads_video_message_as_video_data_url():
    async def scenario():
        adapter = TelegramAdapter("token")
        update = SimpleNamespace(
            update_id=1,
            bot=FakeBot(),
            message=SimpleNamespace(
                text=None,
                caption=None,
                from_user=SimpleNamespace(id=1, username="alice", full_name="Alice"),
                chat=SimpleNamespace(id=123, type="private"),
                message_id=7,
                photo=None,
                voice=None,
                audio=None,
                video=SimpleNamespace(file_id="video-1", mime_type="video/mp4"),
                video_note=None,
                animation=None,
            ),
        )
        return await adapter.to_user_message(update)

    user_message = asyncio.run(scenario())

    assert user_message.videos is not None
    assert len(user_message.videos) == 1
    assert user_message.videos[0].startswith("data:video/mp4;base64,")


def test_telegram_adapter_uses_explicit_bot_when_update_has_no_bot_attribute():
    async def scenario():
        adapter = TelegramAdapter("token")
        update = SimpleNamespace(
            update_id=1,
            message=SimpleNamespace(
                text=None,
                caption=None,
                from_user=SimpleNamespace(id=1, username="alice", full_name="Alice"),
                chat=SimpleNamespace(id=123, type="private"),
                message_id=7,
                photo=[SimpleNamespace(file_id="photo-small"), SimpleNamespace(file_id="photo-large")],
                voice=None,
                audio=None,
                video=None,
                video_note=None,
                animation=None,
            ),
        )
        return await adapter.to_user_message(update, bot=FakeBot())

    user_message = asyncio.run(scenario())

    assert user_message.images is not None
    assert len(user_message.images) == 1
    assert user_message.images[0].startswith("data:image/jpeg;base64,")


def test_telegram_adapter_skips_media_download_when_bot_is_unavailable():
    async def scenario():
        adapter = TelegramAdapter("token")
        update = SimpleNamespace(
            update_id=1,
            message=SimpleNamespace(
                text=None,
                caption=None,
                from_user=SimpleNamespace(id=1, username="alice", full_name="Alice"),
                chat=SimpleNamespace(id=123, type="private"),
                message_id=7,
                photo=[SimpleNamespace(file_id="photo-large")],
                voice=None,
                audio=None,
                video=None,
                video_note=None,
                animation=None,
            ),
        )
        return await adapter.to_user_message(update)

    user_message = asyncio.run(scenario())

    assert user_message.images is None


def test_telegram_adapter_sends_outbound_media_attachments():
    async def scenario():
        adapter = TelegramAdapter("token")
        bot = FakeBot()
        adapter.app = SimpleNamespace(bot=bot)

        await adapter.send(
            AssistantMessage(
                text="",
                channel="telegram",
                external_chat_id="123",
                session_id="telegram:123",
                images=[_data_url("image/png", b"image-bytes")],
                voices=[_data_url("audio/ogg", b"voice-bytes")],
                audios=[_data_url("audio/mpeg", b"audio-bytes")],
                videos=[_data_url("video/mp4", b"video-bytes")],
            )
        )

        return bot

    bot = asyncio.run(scenario())

    assert bot.message_calls == []
    assert bot.photo_calls[0][0] == "123"
    assert bot.photo_calls[0][1].getvalue() == b"image-bytes"
    assert bot.photo_calls[0][1].name == "image-1.png"
    assert bot.voice_calls[0][1].getvalue() == b"voice-bytes"
    assert bot.voice_calls[0][1].name == "voice-1.ogg"
    assert bot.audio_calls[0][1].getvalue() == b"audio-bytes"
    assert bot.audio_calls[0][1].name == "audio-1.mp3"
    assert bot.video_calls[0][1].getvalue() == b"video-bytes"
    assert bot.video_calls[0][1].name == "video-1.mp4"
