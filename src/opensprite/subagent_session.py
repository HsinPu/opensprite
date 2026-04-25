"""Helpers for delegated subagent task sessions."""

from __future__ import annotations

import re
import uuid

from .storage import StoredMessage


SUBAGENT_TASK_ID_PATTERN = r"^task_[A-Za-z0-9_-]{8,64}$"
_TASK_ID_RE = re.compile(SUBAGENT_TASK_ID_PATTERN)


def new_subagent_task_id() -> str:
    """Return a compact id that can be shown to the model/user and reused later."""
    return f"task_{uuid.uuid4().hex[:12]}"


def validate_subagent_task_id(task_id: str) -> str | None:
    """Return an error message when a task id is malformed."""
    value = str(task_id or "").strip()
    if _TASK_ID_RE.fullmatch(value):
        return None
    return "Error: task_id must match pattern task_[A-Za-z0-9_-]{8,64}."


def build_child_subagent_chat_id(parent_chat_id: str, task_id: str) -> str:
    """Build the storage chat id for one child subagent task session."""
    return f"{parent_chat_id}:subagent:{task_id}"


def extract_subagent_prompt_type(messages: list[StoredMessage]) -> str | None:
    """Return the prompt type stored on the first child task message, if available."""
    for message in messages:
        metadata = getattr(message, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            continue
        if metadata.get("kind") != "subagent_task":
            continue
        prompt_type = metadata.get("prompt_type")
        if isinstance(prompt_type, str) and prompt_type.strip():
            return prompt_type.strip()
    return None
