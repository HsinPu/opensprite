"""Helpers for stripping provider-internal assistant scaffolding."""

from __future__ import annotations

import re


THINKING_BLOCK_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>.*?</(?:think|thinking)>",
    re.IGNORECASE | re.DOTALL,
)

SYSTEM_REMINDER_BLOCK_RE = re.compile(
    r"<system-reminder\b[^>]*>.*?</system-reminder>",
    re.IGNORECASE | re.DOTALL,
)


def strip_assistant_internal_scaffolding(text: str) -> str:
    """Remove internal assistant control blocks from visible text."""
    cleaned = THINKING_BLOCK_RE.sub("", text or "")
    return SYSTEM_REMINDER_BLOCK_RE.sub("", cleaned)


def sanitize_assistant_visible_text(text: str) -> str:
    """Return user-visible assistant text after stripping internal blocks."""
    return strip_assistant_internal_scaffolding(text).strip()
