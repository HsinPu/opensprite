"""Channel type registry and adapter construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .identity import normalize_identifier


AdapterFactory = Callable[[Any, str, dict[str, Any]], Any]


@dataclass(frozen=True)
class ChannelTypeSpec:
    """Metadata for one supported channel type."""

    type_id: str
    name: str
    description: str
    secret_fields: frozenset[str] = frozenset()
    default_config: dict[str, Any] = field(default_factory=dict)
    requires_token: bool = False


def _build_telegram_adapter(mq: Any, instance_id: str, channel_config: dict[str, Any]) -> Any:
    from .telegram import TelegramAdapter

    return TelegramAdapter(
        bot_token=channel_config.get("token", ""),
        mq=mq,
        config=channel_config,
        channel_instance_id=instance_id,
    )


def _build_web_adapter(mq: Any, instance_id: str, channel_config: dict[str, Any]) -> Any:
    from .web import WebAdapter

    config = dict(channel_config)
    config.setdefault("id", instance_id)
    return WebAdapter(mq=mq, config=config)


CHANNEL_TYPES: dict[str, ChannelTypeSpec] = {
    "telegram": ChannelTypeSpec(
        type_id="telegram",
        name="Telegram",
        description="透過 Telegram bot 收發聊天訊息。",
        secret_fields=frozenset({"token"}),
        requires_token=True,
        default_config={
            "enabled": True,
            "token": "",
            "connect_timeout": 10,
            "read_timeout": 30,
            "write_timeout": 30,
            "pool_timeout": 30,
            "get_updates_connect_timeout": 10,
            "get_updates_read_timeout": 30,
            "get_updates_write_timeout": 30,
            "get_updates_pool_timeout": 30,
            "poll_timeout": 10,
            "bootstrap_retries": 3,
            "drop_pending_updates": False,
        },
    ),
    "web": ChannelTypeSpec(
        type_id="web",
        name="Web",
        description="瀏覽器 WebSocket 頻道與設定介面。",
        default_config={
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8765,
            "path": "/ws",
            "health_path": "/healthz",
            "max_message_size": 1024 * 1024,
            "frontend_auto_build": True,
            "frontend_auto_install": True,
            "frontend_build_timeout": 120,
            "frontend_install_timeout": 300,
        },
    ),
}


CHANNEL_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "telegram": _build_telegram_adapter,
    "web": _build_web_adapter,
}


def get_channel_type(type_id: str) -> ChannelTypeSpec | None:
    return CHANNEL_TYPES.get(normalize_identifier(type_id, fallback=""))


def list_connectable_channel_types() -> list[ChannelTypeSpec]:
    return [CHANNEL_TYPES[type_id] for type_id in ("telegram",)]


def build_channel_adapter(mq: Any, instance_id: str, channel_config: dict[str, Any]) -> Any | None:
    channel_type = normalize_identifier(str(channel_config.get("type") or instance_id), fallback="")
    factory = CHANNEL_ADAPTER_FACTORIES.get(channel_type)
    if factory is None:
        return None
    return factory(mq, normalize_identifier(instance_id, fallback=channel_type), channel_config)


def default_channel_instances() -> dict[str, dict[str, Any]]:
    return {"web": {"type": "web", "name": "Web", **dict(CHANNEL_TYPES["web"].default_config)}}


def default_instance_config(channel_type: str, *, name: str | None = None) -> dict[str, Any]:
    spec = get_channel_type(channel_type)
    if spec is None:
        raise KeyError(channel_type)
    config = {"type": spec.type_id, "name": name or spec.name, **dict(spec.default_config)}
    return config


def coerce_channel_instances(channels_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize channels config into an instance mapping."""
    raw_instances = channels_data.get("instances")
    if isinstance(raw_instances, dict):
        return {str(instance_id): dict(config) for instance_id, config in raw_instances.items() if isinstance(config, dict)}

    instances: dict[str, dict[str, Any]] = {}
    for channel_type, value in channels_data.items():
        if channel_type not in CHANNEL_TYPES or not isinstance(value, dict):
            continue
        instance_id = normalize_identifier(channel_type, fallback=channel_type)
        instances[instance_id] = {"type": channel_type, "name": CHANNEL_TYPES[channel_type].name, **dict(value)}

    if "web" not in instances:
        instances.update(default_channel_instances())
    return instances


def make_unique_instance_id(instances: dict[str, Any], channel_type: str, name: str | None = None) -> str:
    base = normalize_identifier(name, fallback=channel_type)
    if not base.startswith(channel_type):
        base = f"{channel_type}_{base}"
    candidate = base
    index = 2
    while candidate in instances:
        candidate = f"{base}_{index}"
        index += 1
    return candidate
