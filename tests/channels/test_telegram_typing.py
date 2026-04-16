import asyncio
from types import SimpleNamespace

from telegram import Update

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
