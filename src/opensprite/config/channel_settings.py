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
CHANNEL_ORDER = ("web", "telegram", "console")
CHANNEL_NAMES = {
    "web": "Web",
    "telegram": "Telegram",
    "console": "Console",
}
CHANNEL_DESCRIPTIONS = {
    "web": "Browser WebSocket channel and settings UI.",
    "telegram": "Telegram bot channel for chat messages.",
    "console": "Legacy console config; no runtime adapter is registered.",
}
SECRET_FIELDS = frozenset({"token"})


def _ordered_channel_ids(channels: dict[str, Any]) -> list[str]:
    ordered = [name for name in CHANNEL_ORDER if name in channels]
    ordered.extend(sorted(name for name in channels if name not in CHANNEL_ORDER))
    return ordered


def _channel_name(channel_id: str) -> str:
    return CHANNEL_NAMES.get(channel_id, channel_id.replace("_", " ").replace("-", " ").title())


def _sanitize_settings(channel: dict[str, Any]) -> dict[str, Any]:
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
        "settings": _sanitize_settings(channel),
    }


def _coerce_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ChannelSettingsValidationError(f"{field} must be a boolean")


def _coerce_int(value: Any, *, field: str, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ChannelSettingsValidationError(f"{field} must be an integer") from exc
    if minimum is not None and number < minimum:
        raise ChannelSettingsValidationError(f"{field} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ChannelSettingsValidationError(f"{field} must be at most {maximum}")
    return number


def _coerce_text(value: Any, *, field: str, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not allow_empty and not text:
        raise ChannelSettingsValidationError(f"{field} cannot be empty")
    return text


def _coerce_path(value: Any, *, field: str) -> str:
    text = _coerce_text(value, field=field, allow_empty=False)
    return text if text.startswith("/") else f"/{text}"


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
        """Return configured channels without leaking secrets."""
        main_data, channels = self._load_state()
        return {
            "channels": [
                _serialize_channel(channel_id, channels[channel_id])
                for channel_id in _ordered_channel_ids(channels)
                if isinstance(channels.get(channel_id), dict)
            ],
            "restart_required": False,
            "config_path": str(self.config_path),
            "channels_file": str(Config.get_channels_file_path(self.config_path, main_data)),
        }

    def update_channel(self, channel_id: str, *, enabled: bool | None, settings: dict[str, Any]) -> dict[str, Any]:
        """Update one channel and persist it."""
        main_data, channels = self._load_state()
        channel = channels.get(channel_id)
        if not isinstance(channel, dict):
            raise ChannelSettingsNotFound(f"Unknown channel: {channel_id}")

        if enabled is not None:
            channel["enabled"] = _coerce_bool(enabled, field="enabled")
        if not isinstance(settings, dict):
            raise ChannelSettingsValidationError("settings must be an object")

        if channel_id == "web":
            self._update_web_settings(channel, settings)
        elif channel_id == "telegram":
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

    def _update_web_settings(self, channel: dict[str, Any], settings: dict[str, Any]) -> None:
        if "host" in settings:
            channel["host"] = _coerce_text(settings["host"], field="host", allow_empty=False)
        if "port" in settings:
            channel["port"] = _coerce_int(settings["port"], field="port", minimum=1, maximum=65535)
        if "path" in settings:
            channel["path"] = _coerce_path(settings["path"], field="path")
        if "health_path" in settings:
            channel["health_path"] = _coerce_path(settings["health_path"], field="health_path")
        if "max_message_size" in settings:
            channel["max_message_size"] = _coerce_int(
                settings["max_message_size"],
                field="max_message_size",
                minimum=1024,
            )

    def _update_telegram_settings(self, channel: dict[str, Any], settings: dict[str, Any]) -> None:
        if "token" in settings:
            channel["token"] = _coerce_text(settings["token"], field="token")
        if "drop_pending_updates" in settings:
            channel["drop_pending_updates"] = _coerce_bool(
                settings["drop_pending_updates"],
                field="drop_pending_updates",
            )
        if "poll_timeout" in settings:
            channel["poll_timeout"] = _coerce_int(settings["poll_timeout"], field="poll_timeout", minimum=1)
        if "bootstrap_retries" in settings:
            channel["bootstrap_retries"] = _coerce_int(
                settings["bootstrap_retries"],
                field="bootstrap_retries",
                minimum=0,
            )
