"""Prompt token budget and history trimming helpers."""

from __future__ import annotations

import json
from typing import Any, Callable

from ..context.builder import ContextBuilder
from ..llms import LLMProvider
from ..tools import ToolRegistry
from ..utils import count_messages_tokens, count_text_tokens
from ..utils.log import logger


class PromptBudgetService:
    """Estimates prompt token usage and trims history to fit the configured budget."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        provider: LLMProvider,
        tools: ToolRegistry,
        history_token_budget_getter: Callable[[], int],
        context_window_tokens_getter: Callable[[], int | None],
        output_token_reserve_getter: Callable[[], int],
    ):
        self.context_builder = context_builder
        self.provider = provider
        self.tools = tools
        self._history_token_budget_getter = history_token_budget_getter
        self._context_window_tokens_getter = context_window_tokens_getter
        self._output_token_reserve_getter = output_token_reserve_getter

    def effective_context_token_budget(self) -> int:
        """Return the prompt token budget after applying model window and output reserve."""
        history_budget = max(0, self._history_token_budget_getter())
        context_window_tokens = self._context_window_tokens_getter()
        if context_window_tokens is None:
            return history_budget

        output_reserve = max(0, self._output_token_reserve_getter())
        model_input_budget = max(1, context_window_tokens - output_reserve)
        if history_budget <= 0:
            return model_input_budget
        return min(history_budget, model_input_budget)

    def estimate_tool_schema_tokens(self, *, allow_tools: bool, tool_registry: ToolRegistry | None = None) -> int:
        """Estimate token cost of tool schemas sent with the request."""
        if not allow_tools:
            return 0

        active_tools = tool_registry or self.tools
        if not active_tools.tool_names:
            return 0

        try:
            tool_schema_text = json.dumps(active_tools.get_definitions(), ensure_ascii=False, sort_keys=True)
        except Exception:
            return 0

        return count_text_tokens(tool_schema_text, model=self.provider.get_default_model())

    def trim_history_to_token_budget(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        channel: str | None,
        session_id: str,
        tool_schema_tokens: int = 0,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Trim oldest history messages when the prompt would exceed the history token budget."""
        budget = self.effective_context_token_budget()
        model = self.provider.get_default_model()
        base_messages = self.context_builder.build_messages(
            history=[],
            current_message=current_message,
            current_images=None,
            channel=channel,
            session_id=session_id,
        )
        base_tokens = count_messages_tokens(base_messages, model=model) + tool_schema_tokens
        if budget <= 0 or not history:
            history_tokens = count_messages_tokens(history, model=model) if history else 0
            return history, base_tokens, history_tokens, base_tokens + history_tokens

        if base_tokens >= budget:
            logger.warning(
                f"[{session_id}] prompt.trim | base_tokens={base_tokens} budget={budget} history_retained=0 reason=base-exceeds-budget"
            )
            return [], base_tokens, 0, base_tokens

        trimmed_reversed: list[dict[str, Any]] = []
        running_tokens = base_tokens
        retained_history_tokens = 0
        for message in reversed(history):
            message_tokens = count_messages_tokens([message], model=model)
            if trimmed_reversed and running_tokens + message_tokens > budget:
                break
            if not trimmed_reversed and running_tokens + message_tokens > budget:
                logger.warning(
                    f"[{session_id}] prompt.trim | base_tokens={base_tokens} first_history_tokens={message_tokens} budget={budget} history_retained=0 reason=first-message-exceeds-budget"
                )
                return [], base_tokens, 0, base_tokens
            trimmed_reversed.append(message)
            running_tokens += message_tokens
            retained_history_tokens += message_tokens

        trimmed_history = list(reversed(trimmed_reversed))
        if len(trimmed_history) != len(history):
            logger.info(
                f"[{session_id}] prompt.trim | budget={budget} base_tokens={base_tokens} history_before={len(history)} history_after={len(trimmed_history)} estimated_tokens={running_tokens}"
            )
        return trimmed_history, base_tokens, retained_history_tokens, running_tokens
