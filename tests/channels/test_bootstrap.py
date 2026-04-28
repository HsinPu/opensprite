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
            "telegram": lambda mq, instance_id, cfg: FakeAdapter(instance_id, started),
            "discord": lambda mq, instance_id, cfg: FakeAdapter(instance_id, started),
        },
    )

    asyncio.run(
        channels_module.start_channels(
            object(),
            {
                "instances": {
                    "telegram_work": {"type": "telegram", "enabled": True},
                    "discord_team": {"type": "discord", "enabled": False},
                    "console": {"type": "console", "enabled": True},
                },
            },
        )
    )

    assert started == ["telegram_work"]
