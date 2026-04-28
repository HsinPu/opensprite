"""Channel identity helpers shared by adapters and settings."""

from __future__ import annotations

from dataclasses import dataclass
import re


_ID_RE = re.compile(r"[^a-z0-9_-]+")


def normalize_identifier(value: str | None, *, fallback: str = "default") -> str:
    """Return a stable lowercase identifier for config keys and session namespaces."""
    raw = str(value or "").strip().lower().replace(" ", "_")
    normalized = _ID_RE.sub("_", raw).strip("_")
    return normalized or fallback


def build_session_id(channel_instance_id: str, external_chat_id: str | None) -> str:
    """Build the internal session id from a channel instance and transport chat id."""
    instance_id = normalize_identifier(channel_instance_id, fallback="unknown")
    chat_id = str(external_chat_id or "default").strip() or "default"
    return f"{instance_id}:{chat_id}"


@dataclass(frozen=True)
class ChannelIdentity:
    """Resolved identity for one inbound or outbound channel conversation."""

    channel_instance_id: str
    channel_type: str
    external_chat_id: str | None = None
    external_user_id: str | None = None
    sender_name: str | None = None

    @property
    def session_id(self) -> str:
        return build_session_id(self.channel_instance_id, self.external_chat_id)
