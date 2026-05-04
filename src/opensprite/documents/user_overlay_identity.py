"""Stable cross-session user overlay identity helpers."""

from __future__ import annotations

from typing import Any


def resolve_user_overlay_id(
    *,
    channel: str | None,
    sender_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Return a stable overlay identity when the current channel can provide one safely."""
    payload = dict(metadata or {}) if isinstance(metadata, dict) else {}
    channel_name = str(channel or "").strip().lower()
    if channel_name == "web":
        overlay_profile_id = str(payload.get("overlay_profile_id") or payload.get("overlayProfileId") or "").strip()
        return f"web:{overlay_profile_id}" if overlay_profile_id else None

    stable_sender_id = str(sender_id or "").strip()
    if not stable_sender_id:
        return None
    resolved_channel = channel_name or "default"
    return f"{resolved_channel}:{stable_sender_id}"
