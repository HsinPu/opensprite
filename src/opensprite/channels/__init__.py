"""
opensprite/channels/__init__.py - 訊息頻道適配器

匯出各平台的訊息適配器與啟動入口。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .registry import CHANNEL_ADAPTER_FACTORIES, coerce_channel_instances
from .identity import normalize_identifier
from ..utils.log import logger


CHANNEL_FACTORIES = CHANNEL_ADAPTER_FACTORIES
FIXED_RUNTIME_INSTANCES = frozenset({"web"})


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


def _config_fingerprint(channel_config: dict[str, Any]) -> str:
    """Return a stable comparable representation for adapter config."""
    return json.dumps(channel_config, sort_keys=True, default=str)


class ChannelRuntimeManager:
    """Start, stop, and hot-apply channel adapter instances."""

    def __init__(self, mq: Any):
        self.mq = mq
        self.adapters: dict[str, Any] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._fingerprints: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _attach(self) -> None:
        try:
            setattr(self.mq, "channel_manager", self)
        except AttributeError:
            pass
        agent = getattr(self.mq, "agent", None)
        if agent is not None:
            try:
                setattr(agent, "channel_manager", self)
            except AttributeError:
                pass

    def _enabled_instances(self, channels_config: Any, *, include_fixed: bool) -> dict[str, dict[str, Any]]:
        sections = coerce_channel_instances(_dump_channel_config(channels_config))
        enabled: dict[str, dict[str, Any]] = {}
        for instance_id, channel_config in sections.items():
            if not isinstance(channel_config, dict) or not channel_config.get("enabled"):
                continue
            normalized_id = normalize_identifier(instance_id, fallback=instance_id)
            if not include_fixed and normalized_id in FIXED_RUNTIME_INSTANCES:
                continue
            channel_type = normalize_identifier(str(channel_config.get("type") or normalized_id), fallback="")
            if channel_type not in CHANNEL_FACTORIES:
                logger.warning("Enabled channel instance '{}' has no registered adapter", normalized_id)
                continue
            enabled[normalized_id] = dict(channel_config)
        return enabled

    def _managed_running_ids(self, *, include_fixed: bool) -> list[str]:
        if include_fixed:
            return list(self.tasks)
        return [instance_id for instance_id in self.tasks if instance_id not in FIXED_RUNTIME_INSTANCES]

    async def start(self, channels_config: Any) -> dict[str, Any]:
        """Start all enabled configured channels and expose this manager on the queue."""
        self._attach()
        return await self.apply(channels_config, include_fixed=True)

    async def apply(self, channels_config: Any, *, include_fixed: bool = False) -> dict[str, Any]:
        """Hot-apply channel config changes to running adapters."""
        async with self._lock:
            desired = self._enabled_instances(channels_config, include_fixed=include_fixed)
            result: dict[str, Any] = {
                "started": [],
                "stopped": [],
                "restarted": [],
                "unchanged": [],
                "failed": [],
            }

            for instance_id in self._managed_running_ids(include_fixed=include_fixed):
                if instance_id not in desired:
                    await self.stop_instance(instance_id)
                    result["stopped"].append(instance_id)

            for instance_id, channel_config in desired.items():
                fingerprint = _config_fingerprint(channel_config)
                if instance_id in self.tasks:
                    if self._fingerprints.get(instance_id) == fingerprint:
                        result["unchanged"].append(instance_id)
                        continue
                    await self.stop_instance(instance_id)
                    if await self.start_instance(instance_id, channel_config):
                        result["restarted"].append(instance_id)
                    else:
                        result["failed"].append(instance_id)
                    continue

                if await self.start_instance(instance_id, channel_config):
                    result["started"].append(instance_id)
                else:
                    result["failed"].append(instance_id)

            result["running"] = sorted(self.tasks)
            result["ok"] = not result["failed"]
            return result

    async def start_instance(self, instance_id: str, channel_config: dict[str, Any]) -> bool:
        """Start one adapter instance as a background task."""
        channel_type = normalize_identifier(str(channel_config.get("type") or instance_id), fallback="")
        factory = CHANNEL_FACTORIES.get(channel_type)
        if factory is None:
            logger.warning("Enabled channel instance '{}' has no registered adapter", instance_id)
            return False

        adapter = factory(self.mq, instance_id, channel_config)
        task = asyncio.create_task(adapter.run(), name=f"opensprite-channel:{instance_id}")
        self.adapters[instance_id] = adapter
        self.tasks[instance_id] = task
        self._fingerprints[instance_id] = _config_fingerprint(channel_config)
        task.add_done_callback(lambda completed, channel_id=instance_id: self._handle_task_done(channel_id, completed))
        logger.info("{} ({}) 啟動中...", channel_config.get("name") or instance_id, instance_id)

        try:
            wait_until_started = getattr(adapter, "wait_until_started", None)
            if callable(wait_until_started):
                waiter = asyncio.create_task(wait_until_started())
                done, pending = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    for pending_task in pending:
                        pending_task.cancel()
                    await task
                else:
                    await waiter
            else:
                await asyncio.sleep(0)
            if task.done():
                await task
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Channel instance '{}' failed to start: {}", instance_id, exc)
            await self.stop_instance(instance_id)
            return False

    async def stop_instance(self, instance_id: str) -> None:
        """Stop one running adapter instance."""
        task = self.tasks.pop(instance_id, None)
        self.adapters.pop(instance_id, None)
        self._fingerprints.pop(instance_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.info("Stopped channel instance '{}'", instance_id)
        except Exception as exc:
            logger.warning("Channel instance '{}' stopped with error: {}", instance_id, exc)

    async def stop_all(self) -> None:
        """Stop every running adapter instance."""
        for instance_id in list(self.tasks):
            await self.stop_instance(instance_id)

    def _handle_task_done(self, instance_id: str, task: asyncio.Task) -> None:
        if self.tasks.get(instance_id) is task:
            self.tasks.pop(instance_id, None)
            self.adapters.pop(instance_id, None)
            self._fingerprints.pop(instance_id, None)
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.warning("Channel instance '{}' exited with error: {}", instance_id, exc)


async def start_channels(mq, channels_config) -> ChannelRuntimeManager:
    """Start all enabled channels through a hot-reloadable runtime manager."""
    manager = ChannelRuntimeManager(mq)
    await manager.start(channels_config)
    return manager


__all__ = ["TelegramAdapter", "WebAdapter", "CHANNEL_FACTORIES", "ChannelRuntimeManager", "start_channels"]
