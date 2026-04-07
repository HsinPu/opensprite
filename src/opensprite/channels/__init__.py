"""
opensprite/channels/__init__.py - 訊息頻道適配器

匯出各平台的訊息適配器與啟動入口。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from .telegram import TelegramAdapter
from ..utils.log import logger


ChannelFactory = Callable[[Any, dict[str, Any]], Any]


def _build_telegram_adapter(mq, channel_config: dict[str, Any]) -> TelegramAdapter:
    return TelegramAdapter(
        bot_token=channel_config.get("token", ""),
        mq=mq,
        config=channel_config,
    )


CHANNEL_FACTORIES: dict[str, ChannelFactory] = {
    "telegram": _build_telegram_adapter,
}


def _dump_channel_config(channels_config: Any) -> dict[str, Any]:
    """Normalize the configured channel sections to a plain dict."""
    if channels_config is None:
        return {}
    if hasattr(channels_config, "model_dump"):
        return channels_config.model_dump()
    if isinstance(channels_config, dict):
        return dict(channels_config)
    raise TypeError("channels_config must be a dict or Pydantic model")


async def start_channels(mq, channels_config) -> None:
    """Start all enabled channels through the adapter registry."""
    tasks: list[asyncio.Task] = []
    sections = _dump_channel_config(channels_config)

    for channel_name, channel_config in sections.items():
        if not isinstance(channel_config, dict) or not channel_config.get("enabled"):
            continue

        factory = CHANNEL_FACTORIES.get(channel_name)
        if factory is None:
            logger.warning("Enabled channel '{}' has no registered adapter", channel_name)
            continue

        adapter = factory(mq, channel_config)
        tasks.append(asyncio.create_task(adapter.run()))
        logger.info("{} 啟動中...", channel_name)

    if tasks:
        await asyncio.gather(*tasks)


__all__ = ["TelegramAdapter", "CHANNEL_FACTORIES", "start_channels"]
