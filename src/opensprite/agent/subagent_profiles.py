"""Shared subagent tool-profile metadata helpers."""

from __future__ import annotations

from typing import Any


TOOL_PROFILE_METADATA_FIELD = "tool_profile"
TOOL_PROFILE_NAMES = frozenset({"read-only", "research", "implementation", "testing"})


def normalize_metadata_value(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def allowed_tool_profile_names() -> list[str]:
    """Return supported frontmatter tool profile names."""
    return sorted(TOOL_PROFILE_NAMES)


def validate_tool_profile_name(tool_profile: Any) -> str | None:
    """Return an error when a tool_profile value is not supported."""
    normalized = normalize_metadata_value(tool_profile)
    if normalized in TOOL_PROFILE_NAMES:
        return None
    allowed = ", ".join(allowed_tool_profile_names())
    return f"Error: tool_profile must be one of: {allowed}."
