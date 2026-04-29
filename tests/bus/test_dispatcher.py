import asyncio

from opensprite.bus.dispatcher import MessageQueue
from opensprite.bus.message import AssistantMessage
from opensprite.config.schema import MessagesConfig
from opensprite.cron.manager import CronManager
from opensprite.cron.types import CronJob, CronSchedule


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

        async def handler(message, channel, chat_id):
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


def test_message_queue_can_bypass_immediate_commands_for_internal_messages():
    async def scenario():
        agent = FakeAgent(response_channel="telegram")
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

    async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def reset_history(self, chat_id):
            self.reset_calls.append(chat_id)

    async def scenario():
        agent = ResettableAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def reset_history(self, chat_id):
            self.reset_calls.append(chat_id)
            assert self.cancelled.is_set() is True

    async def scenario():
        agent = ResettableStoppableAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return self.current_task

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            self.current_task = f"# Active Task\n\n- Status: active\n- Goal: {task_text}"
            return self.current_task

        async def mark_active_task_status(self, chat_id, status):
            if self.current_task is None:
                return None
            self.current_task = f"# Active Task\n\n- Status: {status}\n- Goal: existing"
            return self.current_task

        async def reset_active_task(self, chat_id):
            self.current_task = None

        async def activate_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def block_active_task(self, chat_id, reason):
            return self.current_task

        async def wait_on_active_task(self, chat_id, question):
            return self.current_task

        async def set_active_task_current_step(self, chat_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, chat_id, step_text):
            return self.current_task

        async def advance_active_task(self, chat_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"

        async def show_active_task_full(self, chat_id):
            return self.current_task

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            self.current_task = f"# Active Task\n\n- Status: active\n- Goal: {task_text}"
            return self.current_task

        async def mark_active_task_status(self, chat_id, status):
            if self.current_task is None:
                return None
            self.current_task = f"# Active Task\n\n- Status: {status}\n- Goal: Keep the agent on task"
            return self.current_task

        async def reset_active_task(self, chat_id):
            self.current_task = None

        async def activate_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def block_active_task(self, chat_id, reason):
            return self.current_task

        async def wait_on_active_task(self, chat_id, question):
            return self.current_task

        async def set_active_task_current_step(self, chat_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, chat_id, step_text):
            return self.current_task

        async def advance_active_task(self, chat_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"

        async def show_active_task_full(self, chat_id):
            return self.current_task

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, chat_id, status):
            return self.current_task

        async def reset_active_task(self, chat_id):
            self.current_task = None

        async def activate_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def block_active_task(self, chat_id, reason):
            return self.current_task

        async def wait_on_active_task(self, chat_id, question):
            return self.current_task

        async def set_active_task_current_step(self, chat_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, chat_id, step_text):
            return self.current_task

        async def advance_active_task(self, chat_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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
        async def show_active_task(self, chat_id):
            return None

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            return None

        async def mark_active_task_status(self, chat_id, status):
            return None

        async def reset_active_task(self, chat_id):
            return None

        async def activate_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def block_active_task(self, chat_id, reason):
            return None

        async def wait_on_active_task(self, chat_id, question):
            return None

        async def set_active_task_current_step(self, chat_id, step_text):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def set_active_task_next_step(self, chat_id, step_text):
            return None

        async def advance_active_task(self, chat_id):
            return None

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return None

        async def show_active_task_history(self, chat_id, *, limit=10):
            return self.history

        async def set_active_task_from_text(self, chat_id, task_text):
            return None

        async def mark_active_task_status(self, chat_id, status):
            return None

        async def reset_active_task(self, chat_id):
            return None

        async def activate_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def block_active_task(self, chat_id, reason):
            return None

        async def wait_on_active_task(self, chat_id, question):
            return None

        async def set_active_task_current_step(self, chat_id, step_text):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def set_active_task_next_step(self, chat_id, step_text):
            return None

        async def advance_active_task(self, chat_id):
            return None

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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
        async def show_active_task(self, chat_id):
            return None

        async def show_active_task_history(self, chat_id, *, limit=10):
            return f"# Active Task History\n\n- limit: {limit}"

        async def set_active_task_from_text(self, chat_id, task_text):
            return None

        async def mark_active_task_status(self, chat_id, status):
            return None

        async def reset_active_task(self, chat_id):
            return None

        async def activate_active_task(self, chat_id):
            return None

        async def reopen_active_task(self, chat_id):
            return None

        async def block_active_task(self, chat_id, reason):
            return None

        async def wait_on_active_task(self, chat_id, question):
            return None

        async def set_active_task_current_step(self, chat_id, step_text):
            return None

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return None

        async def set_active_task_next_step(self, chat_id, step_text):
            return None

        async def advance_active_task(self, chat_id):
            return None

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return self.current_task

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, chat_id, status):
            return self.current_task

        async def reset_active_task(self, chat_id):
            self.current_task = None

        async def activate_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def block_active_task(self, chat_id, reason):
            self.current_task = f"# Active Task\n\n- Status: blocked\n- Goal: Keep the agent on task\n- Open questions:\n  - {reason}"
            return self.current_task

        async def wait_on_active_task(self, chat_id, question):
            return self.current_task

        async def set_active_task_current_step(self, chat_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, chat_id, step_text):
            return self.current_task

        async def advance_active_task(self, chat_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return self.current_task

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, chat_id, status):
            return self.current_task

        async def reset_active_task(self, chat_id):
            self.current_task = None

        async def activate_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def block_active_task(self, chat_id, reason):
            return self.current_task

        async def wait_on_active_task(self, chat_id, question):
            return self.current_task

        async def set_active_task_current_step(self, chat_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, chat_id, step_text):
            self.current_task = f"# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: inspect\n- Next step: {step_text}"
            return self.current_task

        async def advance_active_task(self, chat_id):
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task\n- Current step: verify\n- Next step: not set"
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return self.current_task

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, chat_id, status):
            return self.current_task

        async def reset_active_task(self, chat_id):
            self.current_task = None

        async def activate_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            return self.current_task

        async def block_active_task(self, chat_id, reason):
            return self.current_task

        async def wait_on_active_task(self, chat_id, question):
            return self.current_task

        async def set_active_task_current_step(self, chat_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            self.current_task = "# Active Task\n\n- Status: done\n- Goal: Keep the agent on task\n- Current step: not set\n- Next step: not set"
            return self.current_task

        async def set_active_task_next_step(self, chat_id, step_text):
            return self.current_task

        async def advance_active_task(self, chat_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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

        async def show_active_task(self, chat_id):
            return self.current_task

        async def show_active_task_history(self, chat_id):
            return None

        async def set_active_task_from_text(self, chat_id, task_text):
            return self.current_task

        async def mark_active_task_status(self, chat_id, status):
            return self.current_task

        async def reset_active_task(self, chat_id):
            self.current_task = None

        async def activate_active_task(self, chat_id):
            return self.current_task

        async def reopen_active_task(self, chat_id):
            self.current_task = "# Active Task\n\n- Status: active\n- Goal: Keep the agent on task"
            return self.current_task

        async def block_active_task(self, chat_id, reason):
            return self.current_task

        async def wait_on_active_task(self, chat_id, question):
            return self.current_task

        async def set_active_task_current_step(self, chat_id, step_text):
            return self.current_task

        async def complete_active_task_step(self, chat_id, next_step_override=None):
            return self.current_task

        async def set_active_task_next_step(self, chat_id, step_text):
            return self.current_task

        async def advance_active_task(self, chat_id):
            return self.current_task

    async def scenario():
        agent = TaskAgent()
        queue = MessageQueue(agent)
        responses = []
        event = asyncio.Event()

        async def handler(message, channel, chat_id):
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
