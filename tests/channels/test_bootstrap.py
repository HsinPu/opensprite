import asyncio

import opensprite.channels as channels_module


class FakeAdapter:
    def __init__(self, name, started):
        self.name = name
        self.started = started

    async def run(self):
        self.started.append(self.name)


def test_start_channels_only_runs_enabled_registered_channels(monkeypatch):
    started = []

    monkeypatch.setattr(
        channels_module,
        "CHANNEL_FACTORIES",
        {
            "telegram": lambda mq, cfg: FakeAdapter("telegram", started),
            "discord": lambda mq, cfg: FakeAdapter("discord", started),
        },
    )

    asyncio.run(
        channels_module.start_channels(
            object(),
            {
                "telegram": {"enabled": True},
                "discord": {"enabled": False},
                "console": {"enabled": True},
            },
        )
    )

    assert started == ["telegram"]
