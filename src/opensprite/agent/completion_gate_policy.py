"""Shared deterministic completion-gate failure policy."""

from __future__ import annotations


MAX_TOOL_ITERATIONS_INCOMPLETE_REASON = "max tool iterations exhausted before completion"
MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL = (
    "The execution loop hit the configured max_tool_iterations limit and needs another bounded continuation pass."
)
INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON = "assistant only emitted internal control text"
