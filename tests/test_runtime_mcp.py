import asyncio

from opensprite import runtime


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


class FakeQueue:
    def __init__(self):
        self.calls = []

    async def process_queue(self):
        self.calls.append("process")
        await asyncio.sleep(0)

    async def stop(self):
        self.calls.append("stop")


def test_runtime_run_connects_and_closes_mcp(monkeypatch):
    fake_agent = FakeAgent()
    fake_queue = FakeQueue()
    started_channels = []
    fake_config = FakeConfig()

    async def fake_create_agent(config):
        return fake_agent, fake_queue

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

    assert fake_agent.calls == ["connect", "close"]
    assert sorted(fake_queue.calls) == ["process", "stop"]
    assert started_channels == [(fake_queue, fake_config.channels)]
