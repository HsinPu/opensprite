import asyncio

from opensprite.bus.dispatcher import MessageQueue
from opensprite.bus.message import AssistantMessage


class FakeAgent:
    def __init__(self, response_channel: str = "unknown"):
        self.response_channel = response_channel
        self.seen_messages = []

    async def process(self, user_message):
        self.seen_messages.append(user_message)
        return AssistantMessage(
            text="pong",
            channel=self.response_channel,
            chat_id=user_message.chat_id,
            session_chat_id=user_message.session_chat_id,
        )


async def _run_queue_once(agent_channel: str, inbound_channel: str):
    agent = FakeAgent(response_channel=agent_channel)
    queue = MessageQueue(agent)
    received = []
    event = asyncio.Event()

    async def telegram_handler(message, channel, chat_id):
        received.append(("telegram", channel, chat_id, message.text))
        event.set()

    async def slack_handler(message, channel, chat_id):
        received.append(("slack", channel, chat_id, message.text))
        event.set()

    queue.register_response_handler("telegram", telegram_handler)
    queue.register_response_handler("slack", slack_handler)

    processor = asyncio.create_task(queue.process_queue())
    try:
        await queue.enqueue_raw(content="ping", chat_id="chat-1", channel=inbound_channel)
        await asyncio.wait_for(event.wait(), timeout=2)
    finally:
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)

    return received, agent.seen_messages


def test_message_queue_routes_response_to_explicit_channel_handler():
    received, seen_messages = asyncio.run(_run_queue_once(agent_channel="slack", inbound_channel="telegram"))

    assert received == [("slack", "slack", "chat-1", "pong")]
    assert seen_messages[0].session_chat_id == "telegram:chat-1"


def test_message_queue_falls_back_to_inbound_channel_when_response_channel_unknown():
    received, _ = asyncio.run(_run_queue_once(agent_channel="unknown", inbound_channel="telegram"))

    assert received == [("telegram", "telegram", "chat-1", "pong")]
