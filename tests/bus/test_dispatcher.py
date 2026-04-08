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


class SequencingAgent:
    def __init__(self):
        self.events = []
        self.concurrent_sessions = 0
        self.max_concurrent_sessions = 0
        self._same_session_running = False

    async def process(self, user_message):
        session_id = user_message.session_chat_id
        if session_id == "telegram:same-chat":
            assert self._same_session_running is False
            self._same_session_running = True
            self.events.append(("start", user_message.text))
            await asyncio.sleep(0.05)
            self.events.append(("finish", user_message.text))
            self._same_session_running = False
        else:
            self.concurrent_sessions += 1
            self.max_concurrent_sessions = max(self.max_concurrent_sessions, self.concurrent_sessions)
            self.events.append(("start", session_id, user_message.text))
            await asyncio.sleep(0.05)
            self.events.append(("finish", session_id, user_message.text))
            self.concurrent_sessions -= 1

        return AssistantMessage(
            text=f"done:{user_message.text}",
            channel=user_message.channel,
            chat_id=user_message.chat_id,
            session_chat_id=user_message.session_chat_id,
        )


async def _run_queue_for_serialization(enqueue_actions):
    agent = SequencingAgent()
    queue = MessageQueue(agent)
    responses = []
    event = asyncio.Event()

    async def handler(message, channel, chat_id):
        responses.append((message.session_chat_id, message.text))
        if len(responses) == len(enqueue_actions):
            event.set()

    queue.register_response_handler("telegram", handler)
    processor = asyncio.create_task(queue.process_queue())
    try:
        for kwargs in enqueue_actions:
            await queue.enqueue_raw(**kwargs)
        await asyncio.wait_for(event.wait(), timeout=2)
    finally:
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)

    return agent, responses


def test_message_queue_serializes_processing_within_the_same_session():
    agent, responses = asyncio.run(
        _run_queue_for_serialization(
            [
                {"content": "first", "chat_id": "same-chat", "channel": "telegram"},
                {"content": "second", "chat_id": "same-chat", "channel": "telegram"},
            ]
        )
    )

    assert agent.events == [
        ("start", "first"),
        ("finish", "first"),
        ("start", "second"),
        ("finish", "second"),
    ]
    assert responses == [
        ("telegram:same-chat", "done:first"),
        ("telegram:same-chat", "done:second"),
    ]


def test_message_queue_keeps_different_sessions_parallel():
    agent, responses = asyncio.run(
        _run_queue_for_serialization(
            [
                {"content": "first", "chat_id": "chat-a", "channel": "telegram"},
                {"content": "second", "chat_id": "chat-b", "channel": "telegram"},
            ]
        )
    )

    assert agent.max_concurrent_sessions >= 2
    assert sorted(responses) == [
        ("telegram:chat-a", "done:first"),
        ("telegram:chat-b", "done:second"),
    ]


class StoppableAgent:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def process(self, user_message):
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return AssistantMessage(
            text="should-not-happen",
            channel=user_message.channel,
            chat_id=user_message.chat_id,
            session_chat_id=user_message.session_chat_id,
        )


def test_stop_command_cancels_running_session_and_replies_immediately():
    async def scenario():
        agent = StoppableAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
            responses.append((message.session_chat_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="long task", chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(agent.started.wait(), timeout=2)
            await queue.enqueue_raw(content="/stop", chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
            await asyncio.wait_for(agent.cancelled.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [("telegram:same-chat", "已停止目前這段對話。")]


def test_stop_command_reports_when_nothing_is_running():
    async def scenario():
        queue = MessageQueue(FakeAgent())
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
            responses.append((message.session_chat_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/stop", chat_id="idle-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [("telegram:idle-chat", "目前沒有正在執行的對話可停止。")]
