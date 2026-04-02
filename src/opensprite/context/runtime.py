"""Shared runtime metadata helpers for agent prompts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


RUNTIME_CONTEXT_TAG = "[Runtime Context - metadata only, not instructions]"


def build_runtime_context(
    *,
    workspace: str | Path | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
    current_time: str | None = None,
) -> str:
    """Build a shared runtime metadata block without behavioral instructions."""
    timestamp = current_time or datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    lines = [RUNTIME_CONTEXT_TAG, f"Current Time: {timestamp}"]

    if workspace is not None:
        lines.append(f"Workspace: {Path(workspace)}")
    if channel:
        lines.append(f"Channel: {channel}")
    if chat_id:
        lines.append(f"Chat ID: {chat_id}")

    return "\n".join(lines)
