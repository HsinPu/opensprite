import asyncio
from dataclasses import dataclass

from opensprite.bus.events import RunEvent
from opensprite.bus.dispatcher import MessageQueue
from opensprite.bus.message import AssistantMessage
from opensprite.config.schema import MessagesConfig, ToolsConfig
from opensprite.cron.manager import CronManager
from opensprite.cron.types import CronJob, CronSchedule
from opensprite.llms.base import LLMResponse, ToolCall
from opensprite.storage import MemoryStorage

from tests.agent.agent_test_helpers import make_agent_loop


class FakeAgent:
    def __init__(self, response_channel: str = "unknown"):
        self.response_channel = response_channel
        self.seen_messages = []

    async def process(self, user_message):
        self.seen_messages.append(user_message)
        return AssistantMessage(
            text="pong",
            channel=self.response_channel,
            external_chat_id=user_message.external_chat_id,
            session_id=user_message.session_id,
        )


class ReplyProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        return LLMResponse(content="trace pong", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


@dataclass
class FakeActiveRun:
    run_id: str


class ToolReplyProvider:
    def __init__(self):
        self.responses = [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="dummy", arguments={"value": "abc"})],
            ),
            LLMResponse(content="tool trace pong", model="fake-model"),
        ]

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return "fake-model"


async def _run_queue_once(agent_channel: str, inbound_channel: str):
    agent = FakeAgent(response_channel=agent_channel)
    queue = MessageQueue(agent)
    received = []
    event = asyncio.Event()

    async def telegram_handler(message, channel, external_chat_id):
        received.append(("telegram", channel, external_chat_id, message.text))
        event.set()

    async def slack_handler(message, channel, external_chat_id):
        received.append(("slack", channel, external_chat_id, message.text))
        event.set()

    queue.register_response_handler("telegram", telegram_handler)
    queue.register_response_handler("slack", slack_handler)

    processor = asyncio.create_task(queue.process_queue())
    try:
        await queue.enqueue_raw(content="ping", external_chat_id="chat-1", channel=inbound_channel)
        await asyncio.wait_for(event.wait(), timeout=2)
    finally:
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)

    return received, agent.seen_messages


def test_message_queue_routes_response_to_explicit_channel_handler():
    received, seen_messages = asyncio.run(_run_queue_once(agent_channel="slack", inbound_channel="telegram"))

    assert received == [("slack", "slack", "chat-1", "pong")]
    assert seen_messages[0].session_id == "telegram:chat-1"


def test_message_queue_falls_back_to_inbound_channel_when_response_channel_unknown():
    received, _ = asyncio.run(_run_queue_once(agent_channel="unknown", inbound_channel="telegram"))

    assert received == [("telegram", "telegram", "chat-1", "pong")]


def test_command_detection_ignores_empty_text():
    assert MessageQueue.is_stop_command("") is False
    assert MessageQueue.is_stop_command("   ") is False
    assert MessageQueue.is_reset_command("") is False
    assert MessageQueue.is_cron_command("") is False
    assert MessageQueue.is_task_command("") is False


def test_message_queue_accepts_empty_text_media_message():
    async def scenario():
        agent = FakeAgent(response_channel="telegram")
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="", external_chat_id="media-chat", channel="telegram", images=["img-a"])
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses, agent.seen_messages

    responses, seen_messages = asyncio.run(scenario())

    assert responses == [("telegram:media-chat", "pong")]
    assert seen_messages[0].text == ""
    assert seen_messages[0].images == ["img-a"]


def test_message_queue_tracks_session_status_during_processing():
    class BlockingAgent(FakeAgent):
        def __init__(self):
            super().__init__(response_channel="telegram")
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def process(self, user_message):
            self.started.set()
            await self.release.wait()
            return await super().process(user_message)

    async def scenario():
        agent = BlockingAgent()
        queue = MessageQueue(agent)
        response_sent = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            response_sent.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="ping", external_chat_id="status-chat", channel="telegram")
            await asyncio.wait_for(agent.started.wait(), timeout=2)
            thinking = queue.session_status.get("telegram:status-chat")
            listed = queue.session_status.list()
            agent.release.set()
            await asyncio.wait_for(response_sent.wait(), timeout=2)
            idle = queue.session_status.get("telegram:status-chat")
            final_list = queue.session_status.list()
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return thinking, listed, idle, final_list

    thinking, listed, idle, final_list = asyncio.run(scenario())

    assert thinking.status == "thinking"
    assert thinking.metadata == {"channel": "telegram", "external_chat_id": "status-chat"}
    assert [item.session_id for item in listed] == ["telegram:status-chat"]
    assert idle.status == "idle"
    assert idle.session_id == "telegram:status-chat"
    assert final_list == []


def test_message_queue_maps_run_events_to_granular_session_status():
    async def scenario():
        queue = MessageQueue(FakeAgent())
        event = RunEvent(
            channel="web",
            external_chat_id="browser-1",
            session_id="web:browser-1",
            run_id="run-1",
            event_type="run_started",
        )
        await queue._set_session_status_from_run_event(event)
        thinking = queue.session_status.get("web:browser-1")

        await queue._set_session_status_from_run_event(
            RunEvent(
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                run_id="run-1",
                event_type="run_part_delta",
                payload={"content_delta": "hi"},
            )
        )
        streaming = queue.session_status.get("web:browser-1")

        await queue._set_session_status_from_run_event(
            RunEvent(
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                run_id="run-1",
                event_type="tool_started",
                payload={"tool_name": "demo"},
            )
        )
        tool_running = queue.session_status.get("web:browser-1")

        await queue._set_session_status_from_run_event(
            RunEvent(
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                run_id="run-1",
                event_type="permission_requested",
                payload={"request_id": "perm-1", "tool_name": "demo"},
            )
        )
        waiting_permission = queue.session_status.get("web:browser-1")

        await queue._set_session_status_from_run_event(
            RunEvent(
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                run_id="run-1",
                event_type="permission_granted",
                payload={"request_id": "perm-1", "tool_name": "demo"},
            )
        )
        resumed = queue.session_status.get("web:browser-1")

        await queue._set_session_status_from_run_event(
            RunEvent(
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                run_id="run-1",
                event_type="work_progress.updated",
                payload={"status": "waiting_user"},
            )
        )
        waiting_user = queue.session_status.get("web:browser-1")

        await queue._set_session_status_from_run_event(
            RunEvent(
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                run_id="run-1",
                event_type="run_finished",
            )
        )
        idle = queue.session_status.get("web:browser-1")
        return thinking, streaming, tool_running, waiting_permission, resumed, waiting_user, idle

    thinking, streaming, tool_running, waiting_permission, resumed, waiting_user, idle = asyncio.run(scenario())

    assert thinking.status == "thinking"
    assert thinking.metadata["run_id"] == "run-1"
    assert streaming.status == "streaming"
    assert tool_running.status == "tool_running"
    assert tool_running.metadata["tool_name"] == "demo"
    assert waiting_permission.status == "waiting_permission"
    assert waiting_permission.metadata["request_id"] == "perm-1"
    assert resumed.status == "thinking"
    assert waiting_user.status == "waiting_user"
    assert idle.status == "idle"


def test_message_queue_cancel_session_requests_active_run_cancel_first():
    class CancellableAgent(FakeAgent):
        def __init__(self):
            super().__init__(response_channel="telegram")
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cancel_calls = []
            self.active_by_session = {}

        def get_active_run(self, session_id):
            return self.active_by_session.get(session_id)

        async def request_run_cancel(self, session_id, run_id, *, channel=None, external_chat_id=None):
            self.cancel_calls.append((session_id, run_id, channel, external_chat_id))
            return True

        async def process(self, user_message):
            self.active_by_session[user_message.session_id] = FakeActiveRun("run-active")
            self.started.set()
            try:
                await self.release.wait()
                return await super().process(user_message)
            finally:
                self.active_by_session.pop(user_message.session_id, None)

    async def scenario():
        agent = CancellableAgent()
        queue = MessageQueue(agent)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="ping", external_chat_id="cancel-chat", channel="telegram")
            await asyncio.wait_for(agent.started.wait(), timeout=2)
            cancelled = await queue.cancel_session("telegram:cancel-chat")
            status = queue.session_status.get("telegram:cancel-chat")
        finally:
            agent.release.set()
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return cancelled, status, agent.cancel_calls

    cancelled, status, cancel_calls = asyncio.run(scenario())

    assert cancelled == 1
    assert status.status == "idle"
    assert cancel_calls == [("telegram:cancel-chat", "run-active", "telegram", "cancel-chat")]


def test_message_queue_persists_run_trace_for_telegram_message(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = make_agent_loop(tmp_path, provider=ReplyProvider(), storage=storage)
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, channel, external_chat_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="trace ping", external_chat_id="trace-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        session_id = "telegram:trace-chat"
        runs = await storage.get_runs(session_id)
        assert len(runs) == 1
        run = runs[0]
        events = await storage.get_run_events(session_id, run.run_id)
        parts = await storage.get_run_parts(session_id, run.run_id)
        return responses, run, events, parts

    responses, run, events, parts = asyncio.run(scenario())

    assert responses == [("telegram:trace-chat", "telegram", "trace-chat", "trace pong")]
    assert run.session_id == "telegram:trace-chat"
    assert run.status == "completed"
    assert run.metadata["channel"] == "telegram"
    assert run.metadata["external_chat_id"] == "trace-chat"
    event_types = [event.event_type for event in events]
    assert "run_started" in event_types
    assert "task_intent.detected" in event_types
    assert "run_finished" in event_types
    assert events[0].payload["status"] == "running"
    assert events[-1].payload["status"] == "completed"
    assert len(parts) == 1
    assert parts[0].part_type == "assistant_message"
    assert parts[0].content == "trace pong"


def test_message_queue_persists_tool_trace_for_telegram_message(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = make_agent_loop(tmp_path, provider=ToolReplyProvider(), storage=storage)
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, channel, external_chat_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="use a tool", external_chat_id="tool-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        session_id = "telegram:tool-chat"
        runs = await storage.get_runs(session_id)
        assert len(runs) == 1
        run = runs[0]
        events = await storage.get_run_events(session_id, run.run_id)
        parts = await storage.get_run_parts(session_id, run.run_id)
        return responses, run, events, parts

    responses, run, events, parts = asyncio.run(scenario())

    assert responses == [("telegram:tool-chat", "telegram", "tool-chat", "tool trace pong")]
    assert run.status == "completed"
    assert run.metadata["executed_tool_calls"] == 1
    event_types = [event.event_type for event in events]
    assert "tool_started" in event_types
    assert "tool_result" in event_types
    assert "run_finished" in event_types
    tool_events = [event for event in events if event.event_type in {"tool_started", "tool_result"}]
    assert [event.payload["tool_name"] for event in tool_events] == ["dummy", "dummy"]
    assert [part.part_type for part in parts] == ["tool_call", "tool_result", "assistant_message"]
    assert [part.tool_name for part in parts[:2]] == ["dummy", "dummy"]
    assert parts[0].metadata["tool_call_id"] == "tc1"
    assert parts[0].metadata["state"] == "running"
    assert parts[1].metadata["tool_call_id"] == "tc1"
    assert parts[1].metadata["ok"] is True
    assert parts[1].content == "ok"
    assert parts[2].content == "tool trace pong"


def test_message_queue_can_bypass_immediate_commands_for_internal_messages():
    async def scenario():
        agent = FakeAgent(response_channel="telegram")
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(
                content="/cron help",
                external_chat_id="same-chat",
                channel="telegram",
                metadata={"_bypass_commands": True, "source": "cron"},
            )
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses, agent.seen_messages

    responses, seen_messages = asyncio.run(scenario())

    assert responses == [("telegram:same-chat", "pong")]
    assert seen_messages[0].text == "/cron help"
    assert seen_messages[0].metadata == {"source": "cron"}


def test_message_queue_can_suppress_final_outbound_for_internal_messages():
    class EventAgent(FakeAgent):
        def __init__(self):
            super().__init__(response_channel="telegram")
            self.done = asyncio.Event()

        async def process(self, user_message):
            response = await super().process(user_message)
            self.done.set()
            return response

    async def scenario():
        agent = EventAgent()
        queue = MessageQueue(agent)
        responses = []

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(
                content="quiet cron",
                external_chat_id="same-chat",
                channel="telegram",
                metadata={"_suppress_outbound": True, "source": "cron"},
            )
            await asyncio.wait_for(agent.done.wait(), timeout=2)
            await asyncio.sleep(0.05)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses, agent.seen_messages

    responses, seen_messages = asyncio.run(scenario())

    assert responses == []
    assert seen_messages[0].text == "quiet cron"
    assert seen_messages[0].metadata == {"source": "cron"}


def test_message_queue_processor_exits_cleanly_when_cancelled_while_idle():
    async def scenario():
        queue = MessageQueue(FakeAgent())
        processor = asyncio.create_task(queue.process_queue())
        await asyncio.sleep(0)

        processor.cancel()
        await asyncio.wait_for(processor, timeout=2)

    asyncio.run(scenario())


class SequencingAgent:
    def __init__(self):
        self.events = []
        self.concurrent_sessions = 0
        self.max_concurrent_sessions = 0
        self._same_session_running = False

    async def process(self, user_message):
        session_id = user_message.session_id
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
            external_chat_id=user_message.external_chat_id,
            session_id=user_message.session_id,
        )


async def _run_queue_for_serialization(enqueue_actions):
    agent = SequencingAgent()
    queue = MessageQueue(agent)
    responses = []
    event = asyncio.Event()

    async def handler(message, channel, external_chat_id):
        responses.append((message.session_id, message.text))
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
                {"content": "first", "external_chat_id": "same-chat", "channel": "telegram"},
                {"content": "second", "external_chat_id": "same-chat", "channel": "telegram"},
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
                {"content": "first", "external_chat_id": "chat-a", "channel": "telegram"},
                {"content": "second", "external_chat_id": "chat-b", "channel": "telegram"},
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
            external_chat_id=user_message.external_chat_id,
            session_id=user_message.session_id,
        )


def test_stop_command_cancels_running_session_and_replies_immediately():
    async def scenario():
        agent = StoppableAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="long task", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(agent.started.wait(), timeout=2)
            await queue.enqueue_raw(content="/stop", external_chat_id="same-chat", channel="telegram")
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

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/stop", external_chat_id="idle-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [("telegram:idle-chat", "目前沒有正在執行的對話可停止。")]


def test_stop_command_uses_configured_idle_message():
    async def scenario():
        agent = FakeAgent()
        agent.messages = MessagesConfig(**{"queue": {"stop_idle": "目前沒有可停止的任務。"}})
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/stop", external_chat_id="idle-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [("telegram:idle-chat", "目前沒有可停止的任務。")]


def test_reset_command_clears_session_history_and_replies_immediately():
    class ResettableAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.reset_calls = []

        async def reset_history(self, session_id):
            self.reset_calls.append(session_id)

    async def scenario():
        agent = ResettableAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/reset", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses, agent.reset_calls

    responses, reset_calls = asyncio.run(scenario())

    assert reset_calls == ["telegram:same-chat"]
    assert responses == [("telegram:same-chat", "已重置目前這段對話。")]


def test_reset_command_cancels_running_session_before_clearing_history():
    class ResettableStoppableAgent(StoppableAgent):
        def __init__(self):
            super().__init__()
            self.reset_calls = []

        async def reset_history(self, session_id):
            self.reset_calls.append(session_id)
            assert self.cancelled.is_set() is True

    async def scenario():
        agent = ResettableStoppableAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="long task", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(agent.started.wait(), timeout=2)
            await queue.enqueue_raw(content="/reset", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
            await asyncio.wait_for(agent.cancelled.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses, agent.reset_calls

    responses, reset_calls = asyncio.run(scenario())

    assert reset_calls == ["telegram:same-chat"]
    assert responses == [("telegram:same-chat", "已重置目前這段對話。 進行中的任務也已停止。")]


def test_cron_command_lists_jobs_for_current_session(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    async def on_job(session_id: str, job: CronJob):
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        service = await agent.cron_manager.get_or_create_service("telegram:same-chat")
        service.add_job(
            name="weather-check",
            schedule=CronSchedule(kind="every", every_ms=300_000),
            message="Check weather",
            deliver=True,
            channel="telegram",
            external_chat_id="same-chat",
        )

        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/cron list", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses

    responses = asyncio.run(scenario())

    assert len(responses) == 1
    assert responses[0][0] == "telegram:same-chat"
    assert "Scheduled jobs:" in responses[0][1]
    assert "weather-check" in responses[0][1]


def test_cron_help_uses_configured_messages():
    async def scenario():
        agent = FakeAgent()
        agent.messages = MessagesConfig(**{"cron": {"help_text": "自訂排程說明"}})
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/cron help", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [("telegram:same-chat", "自訂排程說明")]


def test_cron_command_adds_interval_job_for_current_session(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    async def on_job(session_id: str, job: CronJob):
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(
                content='/cron add every 300 "Check weather and report back"',
                external_chat_id="same-chat",
                channel="telegram",
            )
            await asyncio.wait_for(event.wait(), timeout=2)
            service = await agent.cron_manager.get_or_create_service("telegram:same-chat")
            jobs = service.list_jobs(include_disabled=True)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses, jobs

    responses, jobs = asyncio.run(scenario())

    assert len(responses) == 1
    assert "Created job 'Check weather and report back'" in responses[0][1]
    assert len(jobs) == 1
    assert jobs[0].schedule.kind == "every"
    assert jobs[0].schedule.every_ms == 300_000
    assert jobs[0].payload.message == "Check weather and report back"


def test_cron_command_uses_configured_default_timezone_for_cron_expression(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None
            self.tools_config = ToolsConfig(**{"cron": {"default_timezone": "Asia/Taipei"}})

    async def on_job(session_id: str, job: CronJob):
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(
                content='/cron add cron "0 9 * * *" "Daily report"',
                external_chat_id="same-chat",
                channel="telegram",
            )
            await asyncio.wait_for(event.wait(), timeout=2)
            service = await agent.cron_manager.get_or_create_service("telegram:same-chat")
            jobs = service.list_jobs(include_disabled=True)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses, jobs

    responses, jobs = asyncio.run(scenario())

    assert "Created job 'Daily report'" in responses[0][1]
    assert jobs[0].schedule.kind == "cron"
    assert jobs[0].schedule.tz == "Asia/Taipei"


def test_cron_command_adds_one_time_job_without_delivery_when_requested(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    async def on_job(session_id: str, job: CronJob):
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(
                content='/cron add at 2026-04-10T09:00:00 --no-deliver "Remind me later"',
                external_chat_id="same-chat",
                channel="telegram",
            )
            await asyncio.wait_for(event.wait(), timeout=2)
            service = await agent.cron_manager.get_or_create_service("telegram:same-chat")
            jobs = service.list_jobs(include_disabled=True)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses, jobs

    responses, jobs = asyncio.run(scenario())

    assert len(responses) == 1
    assert "Created job 'Remind me later'" in responses[0][1]
    assert len(jobs) == 1
    assert jobs[0].schedule.kind == "at"
    assert jobs[0].payload.deliver is False
    assert jobs[0].delete_after_run is True


def test_cron_command_removes_job_for_current_session(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    async def on_job(session_id: str, job: CronJob):
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        service = await agent.cron_manager.get_or_create_service("telegram:same-chat")
        job = service.add_job(
            name="cleanup",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="Cleanup",
            deliver=True,
            channel="telegram",
            external_chat_id="same-chat",
        )

        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content=f"/cron remove {job.id}", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
            remaining = service.list_jobs(include_disabled=True)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses, remaining, job.id

    responses, remaining, job_id = asyncio.run(scenario())

    assert responses == [("telegram:same-chat", f"Removed job {job_id}")]
    assert remaining == []


def test_cron_command_can_pause_and_enable_job_for_current_session(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    async def on_job(session_id: str, job: CronJob):
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        service = await agent.cron_manager.get_or_create_service("telegram:same-chat")
        job = service.add_job(
            name="cleanup",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="Cleanup",
            deliver=True,
            channel="telegram",
            external_chat_id="same-chat",
        )

        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            if len(responses) == 2:
                event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content=f"/cron pause {job.id}", external_chat_id="same-chat", channel="telegram")
            await queue.enqueue_raw(content=f"/cron enable {job.id}", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
            refreshed = service.get_job(job.id)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses, refreshed

    responses, refreshed = asyncio.run(scenario())

    assert responses == [
        ("telegram:same-chat", f"Paused job {refreshed.id}"),
        ("telegram:same-chat", f"Enabled job {refreshed.id}"),
    ]
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.state.next_run_at_ms is not None


def test_cron_command_can_run_job_for_current_session(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    executions = []

    async def on_job(session_id: str, job: CronJob):
        executions.append((session_id, job.id))
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        service = await agent.cron_manager.get_or_create_service("telegram:same-chat")
        job = service.add_job(
            name="cleanup",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="Cleanup",
            deliver=True,
            channel="telegram",
            external_chat_id="same-chat",
        )

        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content=f"/cron run {job.id}", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses, job.id

    responses, job_id = asyncio.run(scenario())

    assert responses == [("telegram:same-chat", f"Ran job {job_id}")]
    assert executions == [("telegram:same-chat", job_id)]


def test_cron_command_help_is_immediate():
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    async def scenario():
        queue = MessageQueue(CronAgent())
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/cron help", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert len(responses) == 1
    assert "/cron list" in responses[0][1]
    assert "/cron pause <job_id>" in responses[0][1]
    assert "/cron enable <job_id>" in responses[0][1]
    assert "/cron run <job_id>" in responses[0][1]
    assert "/cron remove <job_id>" in responses[0][1]


def test_cron_command_reports_invalid_add_usage(tmp_path):
    class CronAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cron_manager = None

    async def on_job(session_id: str, job: CronJob):
        return "ok"

    async def scenario():
        agent = CronAgent()
        agent.cron_manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/cron add every nope broken", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)
            await agent.cron_manager.stop()

        return responses

    responses = asyncio.run(scenario())

    assert len(responses) == 1
    assert "Error: every requires an integer number of seconds" in responses[0][1]


def test_task_set_command_replies_immediately_without_running_agent_loop():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.current_task = None

        async def show_active_task(self, session_id):
            return self.current_task

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            self.current_task = f"# Active Task\n\n- Status: active\n- Goal: {task_text}"
            return self.current_task

        async def mark_active_task_status(self, session_id, status):
            if self.current_task is None:
                return None
            self.current_task = f"# Active Task\n\n- Status: {status}\n- Goal: existing"
            return self.current_task

        async def reset_active_task(self, session_id):
            self.current_task = None

        async def activate_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def block_active_task(self, session_id, reason):
            return self.current_task

        async def wait_on_active_task(self, session_id, question):
            return self.current_task

        async def set_active_task_current_step(self, session_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, session_id, step_text):
            return self.current_task

        async def advance_active_task(self, session_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task set Refactor the agent carefully", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses, agent.seen_messages

    responses, seen_messages = asyncio.run(scenario())

    assert responses == [
        ("telegram:same-chat", "已設定目前任務。\n\n# Active Task\n\n- Status: active\n- Goal: Refactor the agent carefully")
    ]
    assert seen_messages == []


def test_task_show_and_done_commands_use_current_task_state_immediately():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"

        async def show_active_task(self, session_id):
            return "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"

        async def show_active_task_full(self, session_id):
            return self.current_task

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            self.current_task = f"# Active Task\n\n- Status: active\n- Goal: {task_text}"
            return self.current_task

        async def mark_active_task_status(self, session_id, status):
            if self.current_task is None:
                return None
            self.current_task = f"# Active Task\n\n- Status: {status}\n- Goal: Keep the agent on task"
            return self.current_task

        async def reset_active_task(self, session_id):
            self.current_task = None

        async def activate_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def block_active_task(self, session_id, reason):
            return self.current_task

        async def wait_on_active_task(self, session_id, question):
            return self.current_task

        async def set_active_task_current_step(self, session_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, session_id, step_text):
            return self.current_task

        async def advance_active_task(self, session_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            if len(responses) == 2:
                event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task show", external_chat_id="same-chat", channel="telegram")
            await queue.enqueue_raw(content="/task done", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [
        ("telegram:same-chat", "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"),
        ("telegram:same-chat", "已將目前任務標記為完成。\n\n# Active Task\n\n- Status: done\n- Goal: Keep the agent on task"),
    ]


def test_task_show_full_returns_full_task_block():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: inspect\n- Next step: verify"

        async def show_active_task(self, session_id):
            return "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"

        async def show_active_task_full(self, session_id):
            return self.current_task

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, session_id, status):
            return self.current_task

        async def reset_active_task(self, session_id):
            self.current_task = None

        async def activate_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def block_active_task(self, session_id, reason):
            return self.current_task

        async def wait_on_active_task(self, session_id, question):
            return self.current_task

        async def set_active_task_current_step(self, session_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, session_id, step_text):
            return self.current_task

        async def advance_active_task(self, session_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task show full", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [
        (
            "telegram:same-chat",
            "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: inspect\n- Next step: verify",
        )
    ]


def test_task_show_reports_no_active_task_when_empty():
    class TaskAgent(FakeAgent):
        async def show_active_task(self, session_id):
            return None

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            return None

        async def mark_active_task_status(self, session_id, status):
            return None

        async def reset_active_task(self, session_id):
            return None

        async def activate_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def block_active_task(self, session_id, reason):
            return None

        async def wait_on_active_task(self, session_id, question):
            return None

        async def set_active_task_current_step(self, session_id, step_text):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def set_active_task_next_step(self, session_id, step_text):
            return None

        async def advance_active_task(self, session_id):
            return None

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task show", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [("telegram:same-chat", "目前沒有進行中的任務。")]


def test_task_history_returns_recent_task_events_immediately():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.history = "# Active Task History\n\n- [2026-04-24 10:00:00] set (user)\n  - status: active"

        async def show_active_task(self, session_id):
            return None

        async def show_active_task_history(self, session_id, *, limit=10):
            return self.history

        async def set_active_task_from_text(self, session_id, task_text):
            return None

        async def mark_active_task_status(self, session_id, status):
            return None

        async def reset_active_task(self, session_id):
            return None

        async def activate_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def block_active_task(self, session_id, reason):
            return None

        async def wait_on_active_task(self, session_id, question):
            return None

        async def set_active_task_current_step(self, session_id, step_text):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def set_active_task_next_step(self, session_id, step_text):
            return None

        async def advance_active_task(self, session_id):
            return None

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task history", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [
        ("telegram:same-chat", "# Active Task History\n\n- [2026-04-24 10:00:00] set (user)\n  - status: active")
    ]


def test_task_history_respects_optional_limit_argument():
    class TaskAgent(FakeAgent):
        async def show_active_task(self, session_id):
            return None

        async def show_active_task_history(self, session_id, *, limit=10):
            return f"# Active Task History\n\n- limit: {limit}"

        async def set_active_task_from_text(self, session_id, task_text):
            return None

        async def mark_active_task_status(self, session_id, status):
            return None

        async def reset_active_task(self, session_id):
            return None

        async def activate_active_task(self, session_id):
            return None

        async def reopen_active_task(self, session_id):
            return None

        async def block_active_task(self, session_id, reason):
            return None

        async def wait_on_active_task(self, session_id, question):
            return None

        async def set_active_task_current_step(self, session_id, step_text):
            return None

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return None

        async def set_active_task_next_step(self, session_id, step_text):
            return None

        async def advance_active_task(self, session_id):
            return None

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task history 1", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [("telegram:same-chat", "# Active Task History\n\n- limit: 1")]


def test_task_block_command_marks_task_blocked_immediately():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"

        async def show_active_task(self, session_id):
            return self.current_task

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, session_id, status):
            return self.current_task

        async def reset_active_task(self, session_id):
            self.current_task = None

        async def activate_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def block_active_task(self, session_id, reason):
            self.current_task = f"# Active Task\n\n- Status: blocked\n- Goal: Keep the agent on task\n- Open questions:\n  - {reason}"
            return self.current_task

        async def wait_on_active_task(self, session_id, question):
            return self.current_task

        async def set_active_task_current_step(self, session_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, session_id, step_text):
            return self.current_task

        async def advance_active_task(self, session_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task block waiting for test environment", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [
        (
            "telegram:same-chat",
            "已將目前任務標記為阻塞。\n\n# Active Task\n\n- Status: blocked\n- Goal: Keep the agent on task\n- Open questions:\n  - waiting for test environment",
        )
    ]


def test_task_next_without_argument_advances_existing_next_step():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: inspect\n- Next step: verify"

        async def show_active_task(self, session_id):
            return self.current_task

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, session_id, status):
            return self.current_task

        async def reset_active_task(self, session_id):
            self.current_task = None

        async def activate_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def block_active_task(self, session_id, reason):
            return self.current_task

        async def wait_on_active_task(self, session_id, question):
            return self.current_task

        async def set_active_task_current_step(self, session_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, session_id, step_text):
            self.current_task = f"# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: inspect\n- Next step: {step_text}"
            return self.current_task

        async def advance_active_task(self, session_id):
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: verify\n- Next step: not set"
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task next", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [
        (
            "telegram:same-chat",
            "已將下一步提升為目前步驟。\n\n# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: verify\n- Next step: not set",
        )
    ]


def test_task_complete_marks_current_step_complete_immediately():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: verify\n- Next step: not set"

        async def show_active_task(self, session_id):
            return self.current_task

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, session_id, status):
            return self.current_task

        async def reset_active_task(self, session_id):
            self.current_task = None

        async def activate_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            return self.current_task

        async def block_active_task(self, session_id, reason):
            return self.current_task

        async def wait_on_active_task(self, session_id, question):
            return self.current_task

        async def set_active_task_current_step(self, session_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            self.current_task = "# Active Task\n\n- Status: done\n- Goal: Keep the agent on task\n- Current step: not set\n- Next step: not set"
            return self.current_task

        async def set_active_task_next_step(self, session_id, step_text):
            return self.current_task

        async def advance_active_task(self, session_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task complete", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [
        (
            "telegram:same-chat",
            "已完成目前步驟。\n\n# Active Task\n\n- Status: done\n- Goal: Keep the agent on task\n- Current step: not set\n- Next step: not set",
        )
    ]


def test_task_reopen_reactivates_terminal_task():
    class TaskAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.current_task = "# Active Task\n\n- Status: done\n- Goal: Keep the agent on task"

        async def show_active_task(self, session_id):
            return self.current_task

        async def show_active_task_history(self, session_id):
            return None

        async def set_active_task_from_text(self, session_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, session_id, status):
            return self.current_task

        async def reset_active_task(self, session_id):
            self.current_task = None

        async def activate_active_task(self, session_id):
            return self.current_task

        async def reopen_active_task(self, session_id):
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"
            return self.current_task

        async def block_active_task(self, session_id, reason):
            return self.current_task

        async def wait_on_active_task(self, session_id, question):
            return self.current_task

        async def set_active_task_current_step(self, session_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, session_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, session_id, step_text):
            return self.current_task

        async def advance_active_task(self, session_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, external_chat_id):
            responses.append((message.session_id, message.text))
            event.set()

        queue.register_response_handler("telegram", handler)
        processor = asyncio.create_task(queue.process_queue())
        try:
            await queue.enqueue_raw(content="/task reopen", external_chat_id="same-chat", channel="telegram")
            await asyncio.wait_for(event.wait(), timeout=2)
        finally:
            await queue.stop()
            await asyncio.wait_for(processor, timeout=2)

        return responses

    responses = asyncio.run(scenario())

    assert responses == [
        (
            "telegram:same-chat",
            "已重新開啟目前任務。\n\n# Active Task\n\n- Status: active\n- Goal: Keep the agent on task",
        )
    ]
