"""Prompt token budgeting, logging, and compact diagnostic helpers."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from ..config import LogConfig
from ..context.builder import ContextBuilder
from ..llms import CHAT_CONTENT_TYPE_IMAGE_URL, CHAT_CONTENT_TYPE_TEXT, LLMProvider
from ..tools import ToolRegistry
from ..utils import (
    count_messages_tokens,
    count_text_tokens,
    sanitize_assistant_visible_text,
    strip_assistant_internal_scaffolding,
)
from ..utils.log_redaction import redact_log_preview
from ..utils.log import logger


PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON = "base-exceeds-budget"
PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON = "first-message-exceeds-budget"
LOG_WHITESPACE_RE = re.compile(r"\s+")


def prompt_trim_base_exceeds_budget_reason() -> str:
    return PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON


def prompt_trim_first_message_exceeds_budget_reason() -> str:
    return PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON


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
            reason = prompt_trim_base_exceeds_budget_reason()
            logger.warning(
                f"[{session_id}] prompt.trim | base_tokens={base_tokens} budget={budget} history_retained=0 reason={reason}"
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
                reason = prompt_trim_first_message_exceeds_budget_reason()
                logger.warning(
                    f"[{session_id}] prompt.trim | base_tokens={base_tokens} first_history_tokens={message_tokens} budget={budget} history_retained=0 reason={reason}"
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


class PromptLoggingService:
    """Handles prompt log files and compact log previews for agent diagnostics."""

    def __init__(self, *, log_config: LogConfig, app_home_getter: Callable[[], Path | None]):
        self.log_config = log_config
        self._app_home_getter = app_home_getter

    @staticmethod
    def sanitize_log_filename(value: str) -> str:
        """Sanitize a string for use in per-prompt log filenames."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
        return cleaned[:80] or "prompt"

    def get_system_prompt_log_path(self, log_id: str) -> Path:
        """Return a unique file path for one full system prompt log entry."""
        logs_root = (self._app_home_getter() or Path.home() / ".opensprite") / "logs" / "system-prompts"
        if ":subagent:" in log_id:
            logs_root = logs_root / "subagents"
        dated_root = logs_root / time.strftime("%Y-%m-%d")
        dated_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%H-%M-%S")
        safe_log_id = self.sanitize_log_filename(log_id)
        filename_base = f"{timestamp}_{safe_log_id}_{time.time_ns()}"
        candidate = dated_root / f"{filename_base}.md"
        counter = 1
        while candidate.exists():
            candidate = dated_root / f"{filename_base}_{counter}.md"
            counter += 1
        return candidate

    def write_full_system_prompt_log(self, log_id: str, content: str) -> None:
        """Write the full system prompt to a dedicated per-prompt log file."""
        try:
            log_path = self.get_system_prompt_log_path(log_id)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            entry = (
                f"[{timestamp}] [{log_id}] prompt.system.begin\n"
                f"{content}\n"
                f"[{timestamp}] [{log_id}] prompt.system.end\n"
            )
            with log_path.open("w", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"[{log_id}] prompt.file.error | error={e}")

    @staticmethod
    def sanitize_response_content(content: str) -> str:
        """Remove provider-internal control blocks from visible replies."""
        return sanitize_assistant_visible_text(content)

    @staticmethod
    def format_log_preview(
        content: str | list[dict[str, Any]] | None,
        max_chars: int = 160,
        *,
        strip_internal: bool = True,
    ) -> str:
        """Build a compact, single-line preview for logs."""
        if isinstance(content, list):
            text_parts: list[str] = []
            image_count = 0
            other_items = 0
            for item in content:
                if not isinstance(item, dict):
                    other_items += 1
                    continue
                item_type = item.get("type")
                if item_type == CHAT_CONTENT_TYPE_TEXT:
                    text_parts.append(str(item.get("text", "")))
                elif item_type == CHAT_CONTENT_TYPE_IMAGE_URL:
                    image_count += 1
                else:
                    other_items += 1

            text = " ".join(part for part in text_parts if part)
            if strip_internal:
                text = strip_assistant_internal_scaffolding(text)
            text = LOG_WHITESPACE_RE.sub(" ", text).strip() or "<multimodal>"
            suffix_parts = []
            if image_count:
                suffix_parts.append(f"images={image_count}")
            if other_items:
                suffix_parts.append(f"items={other_items}")
            if suffix_parts:
                text = f"{text} [{' '.join(suffix_parts)}]"
        else:
            text = str(content or "")
            if strip_internal:
                text = strip_assistant_internal_scaffolding(text)
            text = LOG_WHITESPACE_RE.sub(" ", text).strip()

        if not text:
            return "<empty>"
        text = redact_log_preview(text)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    @staticmethod
    def summarize_messages(messages: list[Any], tail: int = 4) -> str:
        """Build a compact summary of the trailing chat messages for diagnostics."""
        summary = []
        for msg in messages[-tail:]:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content_kind = f"list[{len(content)}]"
            else:
                content_kind = f"str[{len(content or '')}]"
            tool_id = "y" if getattr(msg, "tool_call_id", None) else "n"
            tool_calls = len(getattr(msg, "tool_calls", None) or [])
            summary.append(
                f"{getattr(msg, 'role', '?')}({content_kind},tool_id={tool_id},tool_calls={tool_calls})"
            )
        return ", ".join(summary) if summary else "<empty>"

    @staticmethod
    def extract_available_subagents(system_prompt: str) -> list[str]:
        """Parse the Available Subagents section from a rendered system prompt."""
        in_section = False
        subagents: list[str] = []

        for raw_line in system_prompt.splitlines():
            line = raw_line.strip()
            if not in_section:
                if line in {"# Available Subagents", "## Available Subagents"}:
                    in_section = True
                continue

            if not line:
                continue
            if line == "---" or line.startswith("#"):
                break
            if not line.startswith("- `"):
                continue

            end_tick = line.find("`", 3)
            if end_tick <= 3:
                continue
            subagents.append(line[3:end_tick])

        return subagents

    def log_prepared_messages(self, log_id: str, messages: list[dict[str, Any]]) -> None:
        """Log prepared prompt/messages when prompt logging is enabled."""
        if not self.log_config.log_system_prompt:
            return

        try:
            system_msg = next((m for m in messages if m.get("role") == "system"), None)
            if system_msg:
                system_prompt = str(system_msg.get("content", ""))
                self.write_full_system_prompt_log(log_id, system_prompt)
                max_chars = 240
                if self.log_config.log_system_prompt_lines > 0:
                    max_chars = max(120, self.log_config.log_system_prompt_lines * 120)
                logger.info(
                    f"[{log_id}] prompt.system | {self.format_log_preview(system_prompt, max_chars=max_chars)}"
                )
                if ":subagent:" not in log_id:
                    available_subagents = self.extract_available_subagents(system_prompt)
                    names = ", ".join(available_subagents) if available_subagents else "<none>"
                    logger.info(
                        f"[{log_id}] prompt.subagents | count={len(available_subagents)} names={names}"
                    )

            for index, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                if role == "system":
                    continue
                preview = self.format_log_preview(msg.get("content", ""))
                logger.info(
                    f"[{log_id}] prompt.message[{index}] | role={role} preview={preview}"
                )
        except Exception as e:
            logger.error(f"[{log_id}] prompt.log.error | error={e}")
