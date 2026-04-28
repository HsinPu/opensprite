"""
opensprite/channels/__init__.py - 訊息頻道適配器

匯出各平台的訊息適配器與啟動入口。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .registry import CHANNEL_ADAPTER_FACTORIES, coerce_channel_instances
from .identity import normalize_identifier
from ..utils.log import logger


CHANNEL_FACTORIES = CHANNEL_ADAPTER_FACTORIES


def __getattr__(name: str) -> Any:
    """Lazily expose adapter classes without creating config import cycles."""
    if name == "TelegramAdapter":
        from .telegram import TelegramAdapter

        return TelegramAdapter
    if name == "WebAdapter":
        from .web import WebAdapter

        return WebAdapter
    raise AttributeError(name)


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
    sections = coerce_channel_instances(_dump_channel_config(channels_config))

    for instance_id, channel_config in sections.items():
        if not isinstance(channel_config, dict) or not channel_config.get("enabled"):
            continue

        channel_type = normalize_identifier(str(channel_config.get("type") or instance_id), fallback="")
        factory = CHANNEL_FACTORIES.get(channel_type)
        if factory is None:
            logger.warning("Enabled channel instance '{}' has no registered adapter", instance_id)
            continue

        adapter = factory(mq, instance_id, channel_config)
        tasks.append(asyncio.create_task(adapter.run()))
        logger.info("{} ({}) 啟動中...", channel_config.get("name") or instance_id, instance_id)

    if tasks:
        await asyncio.gather(*tasks)


__all__ = ["TelegramAdapter", "WebAdapter", "CHANNEL_FACTORIES", "start_channels"]
