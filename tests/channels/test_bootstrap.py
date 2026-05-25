import asyncio

import opensprite.channels as channels_module
from opensprite.channels.registry import default_channel_instances


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
                    "unknown_local": {"type": "unknown", "enabled": True},
                },
            },
        )
    )

    assert started == ["telegram_work"]


def test_web_channel_defaults_expose_auth_token():
    instances = default_channel_instances()

    assert instances["web"]["auth_token"] == ""
