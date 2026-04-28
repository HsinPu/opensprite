"""Shared channel settings helpers for Web settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .provider_settings import load_json_dict, write_json_dict
from .schema import Config


class ChannelSettingsError(Exception):
    """Base error for channel settings operations."""


class ChannelSettingsValidationError(ChannelSettingsError):
    """Raised when a request is malformed."""


class ChannelSettingsNotFound(ChannelSettingsError):
    """Raised when a channel cannot be found."""


REGISTERED_CHANNELS = frozenset({"telegram", "web"})
FIXED_CHANNELS = frozenset({"web", "console"})
CHANNEL_ORDER = ("telegram",)
CONNECTABLE_CHANNELS = frozenset({"telegram"})
CHANNEL_NAMES = {
    "web": "Web",
    "telegram": "Telegram",
    "console": "Console",
}
CHANNEL_DESCRIPTIONS = {
    "web": "瀏覽器 WebSocket 頻道與設定介面。",
    "telegram": "透過 Telegram bot 收發聊天訊息。",
    "console": "舊版 console 設定；目前沒有註冊 runtime adapter。",
}
SECRET_FIELDS = frozenset({"token"})


def _channel_name(channel_id: str) -> str:
    return CHANNEL_NAMES.get(channel_id, channel_id.replace("_", " ").replace("-", " ").title())


def _sanitize_settings(channel_id: str, channel: dict[str, Any]) -> dict[str, Any]:
    if channel_id == "telegram":
        return {}
    return {
        key: value
        for key, value in channel.items()
        if key != "enabled" and key not in SECRET_FIELDS
    }


def _serialize_channel(channel_id: str, channel: dict[str, Any]) -> dict[str, Any]:
    token = str(channel.get("token", "") or "")
    return {
        "id": channel_id,
        "name": _channel_name(channel_id),
        "description": CHANNEL_DESCRIPTIONS.get(channel_id, "Custom channel configuration."),
        "enabled": bool(channel.get("enabled")),
        "registered": channel_id in REGISTERED_CHANNELS,
        "token_configured": bool(token.strip()),
        "settings": _sanitize_settings(channel_id, channel),
    }


def _empty_channel(channel_id: str) -> dict[str, Any]:
    return {"enabled": False, "token": ""} if channel_id == "telegram" else {"enabled": False}


def _coerce_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ChannelSettingsValidationError(f"{field} must be a boolean")


def _coerce_text(value: Any, *, field: str, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not allow_empty and not text:
        raise ChannelSettingsValidationError(f"{field} cannot be empty")
    return text


class ChannelSettingsService:
    """Read and mutate channel settings on disk."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).expanduser().resolve()

    def _load_main_data(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise ChannelSettingsNotFound(f"Config file not found: {self.config_path}")
        return load_json_dict(self.config_path)

    def _load_state(self) -> tuple[dict[str, Any], dict[str, Any]]:
        main_data = self._load_main_data()
        loaded = Config.from_json(self.config_path)
        return main_data, loaded.channels.model_dump()

    def _persist_channels_state(self, main_data: dict[str, Any], channels: dict[str, Any]) -> None:
        main_data.pop("channels", None)
        main_data.setdefault("channels_file", "channels.json")
        write_json_dict(self.config_path, main_data)
        Config.ensure_channels_file(self.config_path, main_data)
        Config.write_channels_file(self.config_path, channels, main_data)

    def list_channels(self) -> dict[str, Any]:
        """Return connected and available channels without leaking secrets."""
        main_data, channels = self._load_state()
        connected: list[dict[str, Any]] = []
        available: list[dict[str, Any]] = []
        for channel_id in CHANNEL_ORDER:
            channel = channels.get(channel_id)
            if not isinstance(channel, dict):
                channel = _empty_channel(channel_id)
            serialized = _serialize_channel(channel_id, channel)
            if serialized["token_configured"]:
                connected.append(serialized)
            else:
                available.append(serialized)

        return {
            "connected": connected,
            "available": available,
            "channels": connected + available,
            "restart_required": False,
            "config_path": str(self.config_path),
            "channels_file": str(Config.get_channels_file_path(self.config_path, main_data)),
        }

    def connect_channel(self, channel_id: str, *, token: str | None) -> dict[str, Any]:
        """Connect or update one channel credential."""
        if channel_id not in CONNECTABLE_CHANNELS:
            raise ChannelSettingsNotFound(f"Unknown connectable channel: {channel_id}")

        main_data, channels = self._load_state()
        channel = channels.get(channel_id)
        if not isinstance(channel, dict):
            channel = _empty_channel(channel_id)
            channels[channel_id] = channel

        normalized_token = _coerce_text(token, field="token")
        if normalized_token:
            channel["token"] = normalized_token
        elif not str(channel.get("token", "") or "").strip():
            raise ChannelSettingsValidationError("token is required when connecting a new channel")
        channel["enabled"] = True

        self._persist_channels_state(main_data, channels)
        return {
            "ok": True,
            "channel": _serialize_channel(channel_id, channel),
            "restart_required": True,
        }

    def disconnect_channel(self, channel_id: str) -> dict[str, Any]:
        """Disconnect one channel by disabling it and clearing its credential."""
        if channel_id not in CONNECTABLE_CHANNELS:
            raise ChannelSettingsNotFound(f"Unknown connectable channel: {channel_id}")

        main_data, channels = self._load_state()
        channel = channels.get(channel_id)
        if not isinstance(channel, dict) or not str(channel.get("token", "") or "").strip():
            raise ChannelSettingsNotFound(f"Channel is not connected: {channel_id}")

        channel["enabled"] = False
        channel["token"] = ""
        self._persist_channels_state(main_data, channels)
        return {"ok": True, "channel_id": channel_id, "restart_required": True}

    def update_channel(self, channel_id: str, *, enabled: bool | None, settings: dict[str, Any]) -> dict[str, Any]:
        """Update one channel and persist it."""
        if channel_id in FIXED_CHANNELS:
            raise ChannelSettingsValidationError(f"{_channel_name(channel_id)} channel cannot be edited from settings")

        main_data, channels = self._load_state()
        channel = channels.get(channel_id)
        if not isinstance(channel, dict):
            raise ChannelSettingsNotFound(f"Unknown channel: {channel_id}")

        if enabled is not None:
            channel["enabled"] = _coerce_bool(enabled, field="enabled")
        if not isinstance(settings, dict):
            raise ChannelSettingsValidationError("settings must be an object")

        if channel_id == "telegram":
            self._update_telegram_settings(channel, settings)
        elif channel_id == "console":
            pass
        elif settings:
            channel.update({key: value for key, value in settings.items() if key not in SECRET_FIELDS})

        self._persist_channels_state(main_data, channels)
        return {
            "ok": True,
            "channel": _serialize_channel(channel_id, channel),
            "restart_required": True,
        }

    def _update_telegram_settings(self, channel: dict[str, Any], settings: dict[str, Any]) -> None:
        unsupported = set(settings) - {"token"}
        if unsupported:
            raise ChannelSettingsValidationError("Only token can be edited for Telegram")
        if "token" in settings:
            channel["token"] = _coerce_text(settings["token"], field="token")
