import asyncio
from types import SimpleNamespace

from telegram import Update
from telegram.ext import filters

from opensprite.bus.message import AssistantMessage
from opensprite.channels.telegram import TelegramAdapter


class FakeBot:
    def __init__(self):
        self.typing_calls = []
        self.message_calls = []

    async def send_chat_action(self, chat_id, action):
        self.typing_calls.append((chat_id, action))

    async def send_message(self, chat_id, text, parse_mode=None):
        self.message_calls.append((chat_id, text, parse_mode))


def test_typing_indicator_starts_and_stops_on_response():
    async def scenario():
        adapter = TelegramAdapter("token", config={"typing_action_interval": 1})
        adapter.app = SimpleNamespace(bot=FakeBot())

        adapter._start_typing_indicator("telegram:user-a", "user-a")
        await asyncio.sleep(0.05)
        assert adapter.app.bot.typing_calls != []
        assert "telegram:user-a" in adapter._typing_tasks

        await adapter._on_response(
            AssistantMessage(
                text="done",
                channel="telegram",
                chat_id="user-a",
                session_chat_id="telegram:user-a",
            ),
            "telegram",
            "user-a",
        )

        return adapter

    adapter = asyncio.run(scenario())

    assert "telegram:user-a" not in adapter._typing_tasks
    assert adapter.app.bot.message_calls == [("user-a", "done", "HTML")]


def test_typing_indicator_keeps_running_after_interim_tool_progress():
    """Interim outbound (e.g. delegate / read_skill notice) must not stop typing before the final reply."""

    async def scenario():
        adapter = TelegramAdapter("token", config={"typing_action_interval": 1})
        adapter.app = SimpleNamespace(bot=FakeBot())

        adapter._start_typing_indicator("telegram:user-a", "user-a")
        await asyncio.sleep(0.05)
        assert "telegram:user-a" in adapter._typing_tasks

        await adapter._on_response(
            AssistantMessage(
                text="正在委派子代理（writer）…",
                channel="telegram",
                chat_id="user-a",
                session_chat_id="telegram:user-a",
                metadata={"interim": True, "kind": "tool_progress", "tool_name": "delegate"},
            ),
            "telegram",
            "user-a",
        )

        assert "telegram:user-a" in adapter._typing_tasks

        await adapter._on_response(
            AssistantMessage(
                text="final",
                channel="telegram",
                chat_id="user-a",
                session_chat_id="telegram:user-a",
            ),
            "telegram",
            "user-a",
        )

        return adapter

    adapter = asyncio.run(scenario())

    assert "telegram:user-a" not in adapter._typing_tasks
    assert adapter.app.bot.message_calls[-1] == ("user-a", "final", "HTML")


def test_typing_indicator_stops_on_error():
    async def scenario():
        adapter = TelegramAdapter("token", config={"typing_action_interval": 1})
        adapter.app = SimpleNamespace(bot=FakeBot())
        adapter._start_typing_indicator("telegram:user-a", "user-a")
        await asyncio.sleep(0.05)
        await adapter._on_error("telegram:user-a", "boom")
        return adapter

    adapter = asyncio.run(scenario())

    assert "telegram:user-a" not in adapter._typing_tasks


def test_to_user_message_sets_session_chat_id_for_typing():
    async def scenario():
        adapter = TelegramAdapter("token")
        update = Update.de_json(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": 1710000000,
                    "chat": {"id": 12345, "type": "private"},
                    "from": {"id": 67890, "is_bot": False, "first_name": "Test"},
                    "text": "hello",
                },
            },
            bot=None,
        )
        return await adapter.to_user_message(update)

    user_message = asyncio.run(scenario())

    assert user_message.chat_id == "12345"
    assert user_message.session_chat_id == "telegram:12345"


def test_supported_message_filters_include_media_updates():
    message_filter = TelegramAdapter._supported_message_filters()

    voice_update = Update.de_json(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": 1710000000,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 67890, "is_bot": False, "first_name": "Test"},
                "voice": {"file_id": "voice-1", "file_unique_id": "voice-u-1", "duration": 1},
            },
        },
        bot=None,
    )
    video_update = Update.de_json(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "date": 1710000001,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 67890, "is_bot": False, "first_name": "Test"},
                "video": {
                    "file_id": "video-1",
                    "file_unique_id": "video-u-1",
                    "width": 16,
                    "height": 16,
                    "duration": 1,
                },
            },
        },
        bot=None,
    )
    command_update = Update.de_json(
        {
            "update_id": 3,
            "message": {
                "message_id": 12,
                "date": 1710000002,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 67890, "is_bot": False, "first_name": "Test"},
                "text": "/start",
                "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            },
        },
        bot=None,
    )

    assert bool(message_filter.check_update(voice_update))
    assert bool(message_filter.check_update(video_update))
    assert not message_filter.check_update(command_update)
    assert filters.VOICE.check_update(voice_update)
