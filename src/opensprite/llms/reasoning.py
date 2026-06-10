"""Shared LLM reasoning-mode helpers."""

from __future__ import annotations

from typing import Any


VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
REASONING_EFFORT_OPTIONS = ("", "none", *VALID_REASONING_EFFORTS)
DEFAULT_REASONING_EFFORT = "medium"


def normalize_reasoning_effort(effort: str | None) -> str:
    """Return a supported reasoning effort value, or empty for default provider behavior."""
    normalized = str(effort or "").strip().lower()
    return normalized if normalized in REASONING_EFFORT_OPTIONS else ""


def is_valid_reasoning_effort(effort: str | None) -> bool:
    """Return whether a reasoning effort value is accepted by config/settings APIs."""
    normalized = str(effort or "").strip().lower()
    return normalized in REASONING_EFFORT_OPTIONS


def reasoning_config_from_effort(effort: str | None) -> dict[str, Any] | None:
    """Convert a stored reasoning effort into the common reasoning config shape."""
    normalized = normalize_reasoning_effort(effort)
    if not normalized:
        return None
    if normalized == "none":
        return {"enabled": False}
    return {"enabled": True, "effort": normalized}


def reasoning_config_or_default(effort: str | None) -> dict[str, Any]:
    """Return an explicit reasoning config, defaulting to enabled reasoning."""
    return reasoning_config_from_effort(effort) or {"enabled": True}


def reasoning_effort_from_config(
    config: dict[str, Any] | None,
    *,
    default: str = DEFAULT_REASONING_EFFORT,
    allow_none: bool = True,
) -> str | None:
    """Return the provider effort string represented by a common reasoning config."""
    if not isinstance(config, dict):
        return None
    if config.get("enabled") is False:
        return "none" if allow_none else None
    configured = normalize_reasoning_effort(str(config.get("effort") or ""))
    if configured and configured != "none":
        return configured
    return normalize_reasoning_effort(default) or DEFAULT_REASONING_EFFORT
