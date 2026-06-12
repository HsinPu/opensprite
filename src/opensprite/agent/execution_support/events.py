"""Execution telemetry event models and stop-reason helpers."""

from __future__ import annotations

from dataclasses import dataclass


LLM_STEP_COMPLETED_STATUS = "completed"
LLM_STEP_ERROR_STATUS = "error"
COMPACTED_CONVERSATION_STATE_HEADING = "# Compacted Conversation State"
COMPACTED_TASK_STATE_HEADING = "# Compacted Task State"
COMPACTION_HANDOFF_HEADINGS = (
    COMPACTED_CONVERSATION_STATE_HEADING,
    COMPACTED_TASK_STATE_HEADING,
)
LLM_COMPACTION_TOO_LARGE_REASON = "llm_too_large"
LLM_COMPACTION_CONFIG_MISSING_REASON = "llm_config_missing"
LLM_COMPACTION_NO_BODY_REASON = "no_body"
LLM_COMPACTION_NO_PROMPT_REASON = "no_prompt"
LLM_COMPACTION_ERROR_REASON = "llm_error"
LLM_COMPACTION_EMPTY_REASON = "llm_empty"
MAX_TOOL_ITERATIONS_STOP_REASON = "max_tool_iterations"


def is_max_tool_iterations_stop_reason(stop_reason: str | None) -> bool:
    return str(stop_reason or "").strip() == MAX_TOOL_ITERATIONS_STOP_REASON


def contains_compaction_handoff(content: str | None) -> bool:
    text = str(content or "")
    return any(heading in text for heading in COMPACTION_HANDOFF_HEADINGS)


def format_repeated_invalid_tool_call_content(template: str | None, result: str) -> str:
    cleaned_template = str(template or "").strip()
    cleaned_result = str(result or "").strip()
    if not cleaned_template:
        return cleaned_result
    try:
        return cleaned_template.format(result=result)
    except (KeyError, IndexError, ValueError):
        return f"{cleaned_template}\n\n{result}"


@dataclass
class ContextCompactionEvent:
    """Structured telemetry for one context compaction decision."""

    trigger: str
    strategy: str
    outcome: str
    iteration: int
    messages_before: int
    messages_after: int
    estimated_tokens: int | None = None
    compacted_tokens: int | None = None
    threshold_tokens: int | None = None
    budget_tokens: int | None = None
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    message_tokens: int | None = None
    tool_schema_tokens: int | None = None
    fallback_reason: str | None = None
    error: str | None = None


@dataclass
class LlmStepEvent:
    """Structured telemetry for one LLM request attempt."""

    iteration: int
    attempt: int
    status: str
    provider: str | None
    model: str | None
    duration_ms: int
    estimated_input_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    tools_enabled: bool = False
    tool_count: int = 0
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    finish_reason: str | None = None
    tool_calls: int = 0
    error: str | None = None
    retryable: bool = False
    retry_after_ms: int | None = None
    next_retry_at: float | None = None
