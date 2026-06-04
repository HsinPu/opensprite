"""Shared prompt-budget trim reason policy."""

from __future__ import annotations


PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON = "base-exceeds-budget"
PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON = "first-message-exceeds-budget"


def prompt_trim_base_exceeds_budget_reason() -> str:
    return PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON


def prompt_trim_first_message_exceeds_budget_reason() -> str:
    return PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON
