"""Shared labels for subagent result text."""

from __future__ import annotations


SUBAGENT_TASK_ID_LABEL = "Task ID"
SUBAGENT_PROMPT_TYPE_LABEL = "Subagent"


def subagent_result_line(label: str, value: object) -> str:
    return f"{label}: {value}"


def parse_subagent_result_line(line: str | None, label: str) -> str | None:
    prefix = f"{label}: "
    text = str(line or "")
    if not text.startswith(prefix):
        return None
    return text[len(prefix) :].strip() or None
