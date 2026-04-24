import asyncio

from opensprite import runtime


class FakeConfig:
    def __init__(self, *, llm_configured: bool = True):
        self.log = object()
        self.channels = object()
        self.is_llm_configured = llm_configured


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


def test_runtime_run_connects_and_closes_mcp(monkeypatch):
    fake_agent = FakeAgent()
    fake_queue = FakeQueue()
    fake_cron = FakeCronManager()
    started_channels = []
    fake_config = FakeConfig()

    async def fake_create_agent(config):
        return fake_agent, fake_queue, fake_cron

    async def fake_start_channels(mq, channels_config):
        started_channels.append((mq, channels_config))

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

    assert fake_agent.calls == ["connect", "close-maintenance", "close-skill-review", "close"]
    assert fake_cron.calls == ["start", "stop"]
    assert sorted(fake_queue.calls) == ["process", "stop"]
    assert started_channels == [(fake_queue, fake_config.channels)]


def test_runtime_run_still_starts_when_llm_not_configured(monkeypatch):
    fake_agent = FakeAgent()
    fake_queue = FakeQueue()
    fake_cron = FakeCronManager()
    fake_config = FakeConfig(llm_configured=False)

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

    assert fake_agent.calls == ["connect", "close-maintenance", "close-skill-review", "close"]
    assert fake_cron.calls == ["start", "stop"]
    assert sorted(fake_queue.calls) == ["process", "stop"]
