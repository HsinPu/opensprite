"""Shared channel instance settings helpers for Web settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..channels.identity import normalize_identifier
from ..channels.registry import (
    CHANNEL_ADAPTER_FACTORIES,
    CHANNEL_TYPES,
    coerce_channel_instances,
    default_instance_config,
    list_connectable_channel_types,
    make_unique_instance_id,
)
from .provider_settings import load_json_dict, write_json_dict
from .schema import Config


class ChannelSettingsError(Exception):
    """Base error for channel settings operations."""


class ChannelSettingsValidationError(ChannelSettingsError):
    """Raised when a request is malformed."""


class ChannelSettingsNotFound(ChannelSettingsError):
    """Raised when a channel cannot be found."""


FIXED_CHANNEL_INSTANCES = frozenset({"web", "console"})


def _coerce_text(value: Any, *, field: str, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not allow_empty and not text:
        raise ChannelSettingsValidationError(f"{field} cannot be empty")
    return text


def _channel_type(channel: dict[str, Any], fallback: str) -> str:
    return normalize_identifier(str(channel.get("type") or fallback), fallback=fallback)


def _has_required_secret(channel_type: str, channel: dict[str, Any]) -> bool:
    spec = CHANNEL_TYPES.get(channel_type)
    if spec is None or not spec.requires_token:
        return True
    return bool(str(channel.get("token", "") or "").strip())


def _sanitize_settings(channel_type: str, channel: dict[str, Any]) -> dict[str, Any]:
    if channel_type == "telegram":
        return {}
    spec = CHANNEL_TYPES.get(channel_type)
    secret_fields = spec.secret_fields if spec is not None else frozenset({"token"})
    hidden = set(secret_fields) | {"type", "name", "enabled"}
    return {key: value for key, value in channel.items() if key not in hidden}


def _serialize_instance(instance_id: str, channel: dict[str, Any]) -> dict[str, Any]:
    channel_type = _channel_type(channel, instance_id)
    spec = CHANNEL_TYPES.get(channel_type)
    token_configured = bool(str(channel.get("token", "") or "").strip())
    return {
        "id": instance_id,
        "instance_id": instance_id,
        "type": channel_type,
        "name": str(channel.get("name") or (spec.name if spec else instance_id)),
        "description": spec.description if spec else "Custom channel configuration.",
        "enabled": bool(channel.get("enabled")),
        "registered": channel_type in CHANNEL_ADAPTER_FACTORIES,
        "token_configured": token_configured,
        "settings": _sanitize_settings(channel_type, channel),
    }


def _serialize_type(channel_type: str) -> dict[str, Any]:
    spec = CHANNEL_TYPES[channel_type]
    return {
        "id": spec.type_id,
        "type": spec.type_id,
        "name": spec.name,
        "description": spec.description,
        "requires_token": spec.requires_token,
    }


class ChannelSettingsService:
    """Read and mutate channel instance settings on disk."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).expanduser().resolve()

    def _load_main_data(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise ChannelSettingsNotFound(f"Config file not found: {self.config_path}")
        return load_json_dict(self.config_path)

    def _load_state(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        main_data = self._load_main_data()
        loaded = Config.from_json(self.config_path)
        return main_data, coerce_channel_instances(loaded.channels.model_dump())

    def _persist_instances_state(self, main_data: dict[str, Any], instances: dict[str, dict[str, Any]]) -> None:
        main_data.pop("channels", None)
        main_data.setdefault("channels_file", "channels.json")
        write_json_dict(self.config_path, main_data)
        Config.ensure_channels_file(self.config_path, main_data)
        Config.write_channels_file(self.config_path, {"instances": instances}, main_data)

    def list_channels(self) -> dict[str, Any]:
        """Return connected instances and available channel types without leaking secrets."""
        main_data, instances = self._load_state()
        connected = [
            _serialize_instance(instance_id, channel)
            for instance_id, channel in sorted(instances.items())
            if instance_id not in FIXED_CHANNEL_INSTANCES and _has_required_secret(_channel_type(channel, instance_id), channel)
        ]
        available = [_serialize_type(spec.type_id) for spec in list_connectable_channel_types()]
        return {
            "connected": connected,
            "available": available,
            "channels": connected,
            "restart_required": False,
            "config_path": str(self.config_path),
            "channels_file": str(Config.get_channels_file_path(self.config_path, main_data)),
        }

    def create_channel(self, channel_type: str, *, name: str | None, token: str | None) -> dict[str, Any]:
        """Create and connect one channel instance."""
        normalized_type = normalize_identifier(channel_type, fallback="")
        if normalized_type not in CHANNEL_TYPES or normalized_type not in {spec.type_id for spec in list_connectable_channel_types()}:
            raise ChannelSettingsNotFound(f"Unknown connectable channel type: {channel_type}")

        normalized_name = _coerce_text(name, field="name") or CHANNEL_TYPES[normalized_type].name
        normalized_token = _coerce_text(token, field="token")
        if CHANNEL_TYPES[normalized_type].requires_token and not normalized_token:
            raise ChannelSettingsValidationError("token is required when connecting a new channel")

        main_data, instances = self._load_state()
        instance_id = make_unique_instance_id(instances, normalized_type, normalized_name)
        channel = default_instance_config(normalized_type, name=normalized_name)
        if normalized_token:
            channel["token"] = normalized_token
        channel["enabled"] = True
        instances[instance_id] = channel
        self._persist_instances_state(main_data, instances)
        return {"ok": True, "channel": _serialize_instance(instance_id, channel), "restart_required": True}

    def connect_channel(self, channel_id: str, *, token: str | None, name: str | None = None) -> dict[str, Any]:
        """Connect an existing instance or create a new instance for a channel type."""
        main_data, instances = self._load_state()
        if channel_id in instances:
            if channel_id in FIXED_CHANNEL_INSTANCES:
                raise ChannelSettingsValidationError(f"{channel_id} channel cannot be edited from settings")
            channel = instances[channel_id]
            normalized_token = _coerce_text(token, field="token")
            if normalized_token:
                channel["token"] = normalized_token
            elif not _has_required_secret(_channel_type(channel, channel_id), channel):
                raise ChannelSettingsValidationError("token is required when connecting a new channel")
            if name is not None:
                channel["name"] = _coerce_text(name, field="name", allow_empty=False)
            channel["enabled"] = True
            self._persist_instances_state(main_data, instances)
            return {"ok": True, "channel": _serialize_instance(channel_id, channel), "restart_required": True}

        return self.create_channel(channel_id, name=name, token=token)

    def disconnect_channel(self, instance_id: str) -> dict[str, Any]:
        """Disconnect one channel instance by removing it from user-managed instances."""
        main_data, instances = self._load_state()
        if instance_id in FIXED_CHANNEL_INSTANCES:
            raise ChannelSettingsValidationError(f"{instance_id} channel cannot be disconnected from settings")
        channel = instances.get(instance_id)
        if not isinstance(channel, dict):
            raise ChannelSettingsNotFound(f"Channel is not connected: {instance_id}")

        instances.pop(instance_id, None)
        self._persist_instances_state(main_data, instances)
        return {"ok": True, "channel_id": instance_id, "instance_id": instance_id, "restart_required": True}

    def update_channel(self, instance_id: str, *, enabled: bool | None, settings: dict[str, Any]) -> dict[str, Any]:
        """Update one existing channel instance."""
        if instance_id in FIXED_CHANNEL_INSTANCES:
            raise ChannelSettingsValidationError(f"{instance_id} channel cannot be edited from settings")
        if not isinstance(settings, dict):
            raise ChannelSettingsValidationError("settings must be an object")

        main_data, instances = self._load_state()
        channel = instances.get(instance_id)
        if not isinstance(channel, dict):
            raise ChannelSettingsNotFound(f"Unknown channel instance: {instance_id}")

        unsupported = set(settings) - {"name", "token"}
        if unsupported:
            raise ChannelSettingsValidationError("Only name and token can be edited for a channel instance")
        if "name" in settings:
            channel["name"] = _coerce_text(settings["name"], field="name", allow_empty=False)
        if "token" in settings:
            channel["token"] = _coerce_text(settings["token"], field="token")
        if enabled is not None:
            channel["enabled"] = bool(enabled)

        self._persist_instances_state(main_data, instances)
        return {"ok": True, "channel": _serialize_instance(instance_id, channel), "restart_required": True}
