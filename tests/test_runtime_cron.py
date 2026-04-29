import asyncio

from opensprite import runtime
from opensprite.cron.types import CronSchedule


class FakeConfig:
    def __init__(self):
        self.log = object()
        self.channels = object()
        self.is_llm_configured = True


class FakeAgent:
    def __init__(self):
        self.calls = []

    async def connect_mcp(self):
        self.calls.append("connect")

    async def close_mcp(self):
        self.calls.append("close")

    async def close_background_maintenance(self):
        self.calls.append("close-maintenance")

    async def close_background_skill_reviews(self):
        self.calls.append("close-skill-review")

    async def close_background_processes(self):
        self.calls.append("close-processes")


class FakeQueue:
    def __init__(self):
        self.calls = []

    async def process_queue(self):
        self.calls.append("process")
        await asyncio.sleep(0)

    async def stop(self):
        self.calls.append("stop")


class FakeCronManager:
    def __init__(self):
        self.calls = []

    async def start(self):
        self.calls.append("start")

    async def stop(self):
        self.calls.append("stop")


def test_runtime_run_starts_and_stops_cron_manager(monkeypatch):
    fake_agent = FakeAgent()
    fake_queue = FakeQueue()
    fake_cron = FakeCronManager()
    fake_config = FakeConfig()

    async def fake_create_agent(config):
        return fake_agent, fake_queue, fake_cron

    async def fake_start_channels(mq, channels_config):
        return None

    class FakeEvent:
        async def wait(self):
            await asyncio.sleep(0)
            raise KeyboardInterrupt

    monkeypatch.setattr(runtime.Config, "load", classmethod(lambda cls, path=None: fake_config))
    monkeypatch.setattr(runtime, "create_agent", fake_create_agent)
    monkeypatch.setattr("opensprite.utils.log.setup_log", lambda config=None, console=True: None)
    monkeypatch.setattr("opensprite.channels.start_channels", fake_start_channels)
    monkeypatch.setattr(runtime.asyncio, "Event", FakeEvent)

    asyncio.run(runtime.run())

    assert fake_cron.calls == ["start", "stop"]


def test_runtime_cron_jobs_are_enqueued_through_message_queue(tmp_path):
    class CronAgent:
        def __init__(self):
            self.tool_workspace = tmp_path / "workspace"
            self.process_calls = 0

        async def process(self, user_message):
            self.process_calls += 1
            raise AssertionError("cron runtime should enqueue instead of calling agent.process directly")

    class CronQueue:
        def __init__(self):
            self.messages = []

        async def enqueue(self, user_message):
            self.messages.append(user_message)

    async def scenario():
        agent = CronAgent()
        queue = CronQueue()
        manager = runtime.create_cron_manager(object(), agent, queue)
        service = await manager.get_or_create_service("telegram:same-chat")
        job = service.add_job(
            name="quoted-command",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="/cron help should reach the agent",
            deliver=False,
            channel="telegram",
            external_chat_id="same-chat",
        )
        await service.run_job(job.id)
        await manager.stop()
        return agent, queue, job.id

    agent, queue, job_id = asyncio.run(scenario())

    assert agent.process_calls == 0
    assert len(queue.messages) == 1
    message = queue.messages[0]
    assert message.text == "/cron help should reach the agent"
    assert message.channel == "telegram"
    assert message.external_chat_id == "same-chat"
    assert message.session_id == "telegram:same-chat"
    assert message.sender_id == "system:cron"
    assert message.metadata == {
        "source": "cron",
        "job_id": job_id,
        "_bypass_commands": True,
        "_suppress_outbound": True,
    }
