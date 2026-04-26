"""Shared execution loop for agent and subagent message runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import DocumentLlmConfig, ToolsConfig
from ..llms import ChatMessage, LLMProvider
from ..search.base import SearchStore
from ..tools import ToolRegistry
from ..utils import count_messages_tokens, count_text_tokens
from ..utils.log import logger


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
    message_tokens: int | None = None
    tool_schema_tokens: int | None = None
    fallback_reason: str | None = None
    error: str | None = None


@dataclass
class ExecutionResult:
    """Outcome of one execute_messages run (visible reply plus tool-use telemetry)."""

    content: str
    executed_tool_calls: int = 0
    used_configure_skill: bool = False
    had_tool_error: bool = False
    context_compactions: int = 0
    context_compaction_events: list[ContextCompactionEvent] = field(default_factory=list)


@dataclass
class _LlmCompactionAttempt:
    """Internal result for one optional LLM compaction attempt."""

    messages: list[ChatMessage] | None = None
    fallback_reason: str | None = None
    error: str | None = None


@dataclass
class _ProactiveCompactionResult:
    """Internal result for one proactive compaction build."""

    messages: list[ChatMessage]
    estimated_tokens: int
    compacted_tokens: int
    threshold_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    strategy: str
    fallback_reason: str | None = None
    error: str | None = None


class ToolResultPersistence:
    """Persist tool execution results to storage and optional search indexes."""

    def __init__(
        self,
        *,
        save_message: Callable[[str, str, str, str | None, dict[str, Any] | None], Awaitable[None]],
        search_store: SearchStore | None = None,
    ):
        self.save_message = save_message
        self.search_store = search_store

    async def persist(
        self,
        *,
        chat_id: str | None,
        tool_name: str,
        tool_args: dict[str, Any],
        result: str,
    ) -> None:
        """Persist a single tool result when a target chat is available."""
        if chat_id is None:
            return

        await self.save_message(
            chat_id,
            "tool",
            result,
            tool_name,
            {"tool_args": dict(tool_args or {})},
        )
        if self.search_store is not None:
            try:
                await self.search_store.index_tool_result(chat_id, tool_name, tool_args, result)
            except Exception as e:
                logger.warning("[{}] Failed to index tool result for search: {}", chat_id, e)


class ExecutionEngine:
    """Run the LLM and tool-calling loop for prepared chat messages."""

    _MAIN_SYSTEM_REFRESH_TOOLS = frozenset({"configure_skill", "configure_subagent"})
    _MAIN_SYSTEM_REFRESH_ACTIONS = frozenset({"add", "upsert", "remove"})

    REPEATED_TOOL_ERROR_LIMIT = 2
    TOOL_RESULT_MAX_CHARS = 1200
    EXEC_RESULT_MAX_CHARS = 1200
    EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Previous attempt produced no visible user-facing text. "
        "Please answer again with a direct, displayable reply for the user."
    )
    SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Previous attempt only contained hidden or non-displayable content. "
        "Do not output <think> or hidden reasoning. If tools are needed, call them. Otherwise answer now in plain visible text for the user."
    )
    CONTEXT_COMPACTION_RETRY_LIMIT = 1
    PROACTIVE_CONTEXT_COMPACTION_LIMIT = 1
    COMPACTED_MESSAGE_MAX_CHARS = 900
    COMPACTED_LATEST_USER_MAX_CHARS = 1600
    COMPACTED_TRANSCRIPT_MAX_CHARS = 10_000
    LLM_COMPACTION_TRANSCRIPT_MAX_CHARS = 30_000
    LLM_COMPACTION_MESSAGE_MAX_CHARS = 1800
    CONTEXT_OVERFLOW_MARKERS = (
        "context length",
        "context_length_exceeded",
        "context window",
        "maximum context",
        "maximum context length",
        "maximum token",
        "too many tokens",
        "token limit",
        "tokens exceed",
        "input is too long",
        "prompt is too long",
        "reduce the length",
    )
    CONTINUATION_AFTER_COMPACTION_MESSAGE = (
        "Continue from the compacted conversation state above. "
        "Do not ask the user to repeat information that is present in the compacted state. "
        "If more tool work is needed, continue using tools; otherwise provide the final answer."
    )
    CONTEXT_OVERFLOW_STATUS_MESSAGE = "上下文已接近上限，正在壓縮目前任務並繼續…"
    PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE = "上下文接近上限，正在壓縮目前任務並繼續…"
    LLM_COMPACTION_SYSTEM_PROMPT = """You are a context compaction engine for an autonomous assistant.
Compress the provided conversation state into a concise, factual Markdown state snapshot.
Do not solve the user's task. Do not ask questions. Do not invent facts.
Preserve all information needed to continue work without asking the user to repeat details.

Output exactly these sections when applicable:
# Compacted Task State
## Current Goal
## Latest User Instruction
## Important Context And Constraints
## Completed Work
## Pending Work
## Relevant Files, Tools, Or IDs
## Recent Tool Results
## Open Questions Or Blockers
"""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        tools: ToolRegistry,
        tools_config: ToolsConfig | None = None,
        search_store: SearchStore | None = None,
        empty_response_fallback: str,
        save_message: Callable[[str, str, str, str | None], Awaitable[None]],
        format_log_preview: Callable[..., str],
        summarize_messages: Callable[..., str],
        sanitize_response_content: Callable[[str], str],
        chat_temperature: float,
        chat_max_tokens: int,
        chat_top_p: float | None,
        chat_frequency_penalty: float | None,
        chat_presence_penalty: float | None,
        pass_decoding_params: bool,
        context_compaction_enabled: bool = False,
        context_compaction_token_budget: int = 0,
        context_compaction_threshold_ratio: float = 0.9,
        context_compaction_min_messages: int = 8,
        context_compaction_strategy: str = "deterministic",
        context_compaction_llm: DocumentLlmConfig | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.chat_temperature = chat_temperature
        self.chat_max_tokens = chat_max_tokens
        self.chat_top_p = chat_top_p
        self.chat_frequency_penalty = chat_frequency_penalty
        self.chat_presence_penalty = chat_presence_penalty
        self.pass_decoding_params = pass_decoding_params
        self.context_compaction_enabled = context_compaction_enabled
        self.context_compaction_token_budget = max(0, context_compaction_token_budget)
        self.context_compaction_threshold_ratio = context_compaction_threshold_ratio
        self.context_compaction_min_messages = max(1, context_compaction_min_messages)
        self.context_compaction_strategy = context_compaction_strategy
        self.context_compaction_llm = context_compaction_llm
        self.tools_config = tools_config or ToolsConfig()
        self.search_store = search_store
        self.empty_response_fallback = empty_response_fallback
        self.format_log_preview = format_log_preview
        self.summarize_messages = summarize_messages
        self.sanitize_response_content = sanitize_response_content
        self.tool_result_persistence = ToolResultPersistence(
            save_message=save_message,
            search_store=search_store,
        )

    @staticmethod
    def _should_refresh_main_system_after_tool(tool_name: str, tool_args: dict[str, Any]) -> bool:
        """Skill/subagent definitions on disk may change; optional mid-loop system rebuild."""
        if tool_name not in ExecutionEngine._MAIN_SYSTEM_REFRESH_TOOLS:
            return False
        action = tool_args.get("action")
        return action in ExecutionEngine._MAIN_SYSTEM_REFRESH_ACTIONS

    @staticmethod
    def _tool_result_ok_for_system_refresh(result: str) -> bool:
        return not str(result).lstrip().startswith("Error:")

    @staticmethod
    def _classify_tool_result(result: str) -> str | None:
        """Classify tool-result errors that should trigger early stopping."""
        if result.startswith("Error: Invalid arguments for "):
            return result
        return None

    @staticmethod
    def _tool_result_looks_like_failure(result: str) -> bool:
        lowered = result.lower()
        return (
            result.startswith("Error:")
            or "timed out" in lowered
            or " failed" in lowered
            or lowered.startswith("(mcp tool call failed")
            or lowered.startswith("(mcp tool call timed out")
        )

    @classmethod
    def _summarize_tool_result_for_context(cls, tool_name: str, result: str) -> str:
        """Shrink verbose tool output before feeding it back into the LLM loop."""
        text = result.strip()
        if tool_name == "exec":
            return cls._summarize_exec_result_for_context(text)
        if len(text) <= cls.TOOL_RESULT_MAX_CHARS:
            return text

        head_limit = cls.TOOL_RESULT_MAX_CHARS // 2
        tail_limit = cls.TOOL_RESULT_MAX_CHARS - head_limit
        head = text[:head_limit].rstrip()
        tail = text[-tail_limit:].lstrip()
        return (
            f"[tool:{tool_name}] Output truncated for context. Full result was persisted separately "
            f"({len(text)} chars total).\n"
            f"--- BEGIN HEAD ---\n{head}\n"
            f"--- MIDDLE TRUNCATED ---\n"
            f"--- END TAIL ---\n{tail}"
        )

    @classmethod
    def _summarize_exec_result_for_context(cls, text: str) -> str:
        """Prefer error markers and the latest lines for shell command output."""
        if len(text) <= cls.EXEC_RESULT_MAX_CHARS:
            return text

        lines = [line for line in text.splitlines() if line.strip()]
        first_lines = lines[:6]
        stderr_lines = [line for line in lines if "[stderr]" in line][:4]
        timeout_lines = [line for line in lines if "timed out" in line.lower()][:2]
        tail_lines = lines[-8:]

        summary_parts: list[str] = [
            f"[tool:exec] Output truncated for context. Full result was persisted separately ({len(text)} chars total)."
        ]
        if timeout_lines:
            summary_parts.extend(["Timeout/Error summary:", *timeout_lines])
        elif text.startswith("Error:"):
            summary_parts.extend(["Error summary:", lines[0]])

        if stderr_lines:
            summary_parts.extend(["stderr highlights:", *stderr_lines])

        if first_lines:
            summary_parts.extend(["output start:", *first_lines])

        if tail_lines:
            summary_parts.extend(["output tail:", *tail_lines])

        summarized = "\n".join(summary_parts)
        if len(summarized) <= cls.EXEC_RESULT_MAX_CHARS:
            return summarized

        return summarized[: cls.EXEC_RESULT_MAX_CHARS].rstrip() + "\n... (exec context summary truncated)"

    @staticmethod
    def _summarize_tool_names(tool_calls: list[Any] | None) -> str:
        """Build a compact tool-name list for diagnostics."""
        if not tool_calls:
            return "-"
        names = [getattr(tc, "name", "") or "<unknown>" for tc in tool_calls]
        preview = ", ".join(names[:5])
        if len(names) > 5:
            preview += f", ... (+{len(names) - 5} more)"
        return preview

    @classmethod
    def _looks_like_context_overflow(cls, exc: Exception) -> bool:
        """Return whether an LLM exception appears to be caused by context size."""
        text = f"{type(exc).__name__}: {str(exc)}".lower()
        return any(marker in text for marker in cls.CONTEXT_OVERFLOW_MARKERS)

    def _get_token_model(self) -> str | None:
        """Best-effort model name lookup for local token estimates."""
        get_default_model = getattr(self.provider, "get_default_model", None)
        if not callable(get_default_model):
            return None
        try:
            return str(get_default_model() or "") or None
        except Exception:
            return None

    @staticmethod
    def _estimate_tool_schema_tokens(tools: list[dict[str, Any]] | None, *, model: str | None) -> int:
        if not tools:
            return 0
        try:
            tool_schema_text = json.dumps(tools, ensure_ascii=False, sort_keys=True)
        except Exception:
            tool_schema_text = str(tools)
        return count_text_tokens(tool_schema_text, model=model)

    def _estimate_request_tokens(
        self,
        chat_messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[int, int, int]:
        model = self._get_token_model()
        message_tokens = count_messages_tokens(chat_messages, model=model)
        tool_schema_tokens = self._estimate_tool_schema_tokens(tools, model=model)
        return message_tokens + tool_schema_tokens, message_tokens, tool_schema_tokens

    async def _build_proactive_compaction(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        tool_results_history: list[str],
    ) -> _ProactiveCompactionResult | None:
        """Return compacted messages when the next request is nearing the configured budget."""
        if not self.context_compaction_enabled:
            return None
        if self.context_compaction_token_budget <= 0 or self.context_compaction_threshold_ratio <= 0:
            return None
        if len(chat_messages) < self.context_compaction_min_messages:
            return None

        threshold_tokens = max(1, int(self.context_compaction_token_budget * self.context_compaction_threshold_ratio))
        estimated_tokens, message_tokens, tool_schema_tokens = self._estimate_request_tokens(chat_messages, tools)
        if estimated_tokens < threshold_tokens:
            return None

        llm_fallback_reason: str | None = None
        llm_fallback_error: str | None = None
        if self.context_compaction_strategy == "llm":
            llm_attempt = await self._compact_messages_with_llm(
                log_id,
                chat_messages,
                tool_results_history=tool_results_history,
            )
            compacted_messages = llm_attempt.messages
            if compacted_messages is not None:
                compacted_tokens, _, _ = self._estimate_request_tokens(compacted_messages, tools)
                if compacted_tokens < estimated_tokens:
                    return _ProactiveCompactionResult(
                        messages=compacted_messages,
                        estimated_tokens=estimated_tokens,
                        compacted_tokens=compacted_tokens,
                        threshold_tokens=threshold_tokens,
                        message_tokens=message_tokens,
                        tool_schema_tokens=tool_schema_tokens,
                        strategy="llm",
                    )
                llm_fallback_reason = "llm_too_large"
                logger.warning(
                    f"[{log_id}] llm.context-compact.llm-too-large | "
                    f"estimated_tokens={estimated_tokens} compacted_tokens={compacted_tokens} fallback=deterministic"
                )
            else:
                llm_fallback_reason = llm_attempt.fallback_reason
                llm_fallback_error = llm_attempt.error

        compacted_messages = self._compact_messages_for_continuation(
            chat_messages,
            tool_results_history=tool_results_history,
            reason=(
                "The in-turn context was compacted automatically before the LLM request because "
                "it was approaching the configured context budget."
            ),
        )
        if compacted_messages is None:
            return None

        compacted_tokens, _, _ = self._estimate_request_tokens(compacted_messages, tools)
        if compacted_tokens >= estimated_tokens:
            return None

        return _ProactiveCompactionResult(
            messages=compacted_messages,
            estimated_tokens=estimated_tokens,
            compacted_tokens=compacted_tokens,
            threshold_tokens=threshold_tokens,
            message_tokens=message_tokens,
            tool_schema_tokens=tool_schema_tokens,
            strategy="deterministic",
            fallback_reason=llm_fallback_reason,
            error=llm_fallback_error,
        )

    @classmethod
    def _split_leading_system_messages(
        cls,
        chat_messages: list[ChatMessage],
    ) -> tuple[list[ChatMessage], list[ChatMessage]]:
        leading_system: list[ChatMessage] = []
        body_start = 0
        for message in chat_messages:
            if getattr(message, "role", None) != "system":
                break
            leading_system.append(ChatMessage(role="system", content=getattr(message, "content", "")))
            body_start += 1
        return leading_system, chat_messages[body_start:]

    @classmethod
    def _latest_user_text(cls, messages: list[ChatMessage], *, max_chars: int) -> str:
        latest_user = next((message for message in reversed(messages) if getattr(message, "role", None) == "user"), None)
        if latest_user is None:
            return ""
        return cls._truncate_text(
            cls._message_content_to_text(getattr(latest_user, "content", "")),
            max_chars,
        )

    def _build_llm_compaction_prompt(
        self,
        chat_messages: list[ChatMessage],
        *,
        tool_results_history: list[str],
    ) -> list[ChatMessage] | None:
        _, body = self._split_leading_system_messages(chat_messages)
        if not body:
            return None

        latest_user_text = self._latest_user_text(body, max_chars=self.COMPACTED_LATEST_USER_MAX_CHARS)
        transcript = self._build_compacted_transcript(
            body,
            max_chars=self.LLM_COMPACTION_TRANSCRIPT_MAX_CHARS,
            message_max_chars=self.LLM_COMPACTION_MESSAGE_MAX_CHARS,
        )
        sections = [
            "# Conversation State To Compact",
            "Use this state to produce a continuation snapshot for the assistant.",
        ]
        if latest_user_text:
            sections.extend(["", "## Latest User Instruction", latest_user_text])
        sections.extend(["", "## Transcript", transcript or "(no transcript details)"])
        if tool_results_history:
            sections.extend([
                "",
                "## Recent Tool Results",
                "\n".join(f"- {item}" for item in tool_results_history[-12:]),
            ])
        return [
            ChatMessage(role="system", content=self.LLM_COMPACTION_SYSTEM_PROMPT),
            ChatMessage(role="user", content="\n".join(sections)),
        ]

    async def _compact_messages_with_llm(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        tool_results_history: list[str],
    ) -> _LlmCompactionAttempt:
        compaction_llm = self.context_compaction_llm
        if compaction_llm is None:
            return _LlmCompactionAttempt(fallback_reason="llm_config_missing")

        leading_system, body = self._split_leading_system_messages(chat_messages)
        if not body:
            return _LlmCompactionAttempt(fallback_reason="no_body")

        compaction_messages = self._build_llm_compaction_prompt(
            chat_messages,
            tool_results_history=tool_results_history,
        )
        if compaction_messages is None:
            return _LlmCompactionAttempt(fallback_reason="no_prompt")

        try:
            response = await self.provider.chat(
                messages=compaction_messages,
                tools=None,
                status_callback=None,
                **compaction_llm.decoding_kwargs(),
            )
        except Exception as exc:
            error_preview = self.format_log_preview(str(exc), max_chars=240)
            logger.warning(
                f"[{log_id}] llm.context-compact.llm-error | fallback=deterministic "
                f"error={error_preview}"
            )
            return _LlmCompactionAttempt(fallback_reason="llm_error", error=error_preview)

        summary = self.sanitize_response_content(response.content or "").strip()
        if not summary:
            logger.warning(f"[{log_id}] llm.context-compact.llm-empty | fallback=deterministic")
            return _LlmCompactionAttempt(fallback_reason="llm_empty")

        summary_sections = [
            "# Compacted Conversation State",
            "The in-turn context was compacted by an LLM before the next request because it was approaching the configured context budget.",
            "Continue the same task from this state; do not restart or ask the user to repeat details already summarized here.",
            "",
            summary,
        ]
        return _LlmCompactionAttempt(
            messages=[
                *leading_system,
                ChatMessage(role="system", content="\n".join(summary_sections)),
                ChatMessage(role="user", content=self.CONTINUATION_AFTER_COMPACTION_MESSAGE),
            ]
        )

    @staticmethod
    def _message_content_to_text(content: Any) -> str:
        """Render ChatMessage content into compact text for deterministic summaries."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    parts.append(str(item))
                    continue
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(str(item.get("text", "")))
                elif item_type == "image_url":
                    parts.append("[image attachment omitted during compaction]")
                else:
                    try:
                        parts.append(json.dumps(item, ensure_ascii=False))
                    except Exception:
                        parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content or "")

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """Truncate text while retaining both the beginning and the end."""
        value = str(text or "").strip()
        if len(value) <= max_chars:
            return value
        tail_chars = max(120, max_chars // 3)
        head_chars = max_chars - tail_chars
        return (
            value[:head_chars].rstrip()
            + f"\n... (truncated {len(value) - max_chars} chars during compaction) ...\n"
            + value[-tail_chars:].lstrip()
        )

    @classmethod
    def _format_tool_calls_for_compaction(cls, tool_calls: list[dict[str, Any]] | None) -> str:
        if not tool_calls:
            return ""

        formatted: list[str] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                formatted.append(cls._truncate_text(str(call), 240))
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = function.get("name") or call.get("name") or "<unknown>"
            args = function.get("arguments") or call.get("arguments") or "{}"
            formatted.append(f"{name}({cls._truncate_text(str(args), 240)})")
        return "; ".join(formatted)

    @classmethod
    def _build_compacted_transcript(
        cls,
        messages: list[ChatMessage],
        *,
        max_chars: int,
        message_max_chars: int | None = None,
    ) -> str:
        lines: list[str] = []
        used_chars = 0
        current_message_max_chars = message_max_chars or cls.COMPACTED_MESSAGE_MAX_CHARS
        for index, message in enumerate(messages, start=1):
            role = getattr(message, "role", "?")
            content = cls._truncate_text(
                cls._message_content_to_text(getattr(message, "content", "")),
                current_message_max_chars,
            )
            tool_call_summary = cls._format_tool_calls_for_compaction(getattr(message, "tool_calls", None))
            tool_call_id = getattr(message, "tool_call_id", None)
            parts = [f"{index}. role={role}"]
            if tool_call_id:
                parts.append(f"tool_call_id={tool_call_id}")
            header = " ".join(parts)
            rendered = header
            if content:
                rendered += f"\n{content}"
            if tool_call_summary:
                rendered += f"\ntool_calls: {tool_call_summary}"

            if lines and used_chars + len(rendered) + 2 > max_chars:
                lines.append("... (older compacted transcript entries omitted due to size) ...")
                break
            lines.append(rendered)
            used_chars += len(rendered) + 2
        return "\n\n".join(lines)

    @classmethod
    def _compact_messages_for_continuation(
        cls,
        chat_messages: list[ChatMessage],
        *,
        tool_results_history: list[str],
        reason: str | None = None,
    ) -> list[ChatMessage] | None:
        """Create a smaller message list that can retry the same turn after overflow."""
        if not chat_messages:
            return None

        leading_system: list[ChatMessage] = []
        body_start = 0
        for message in chat_messages:
            if getattr(message, "role", None) != "system":
                break
            leading_system.append(ChatMessage(role="system", content=getattr(message, "content", "")))
            body_start += 1

        body = chat_messages[body_start:]
        if not body:
            return None

        latest_user = next((message for message in reversed(body) if getattr(message, "role", None) == "user"), None)
        latest_user_text = ""
        if latest_user is not None:
            latest_user_text = cls._truncate_text(
                cls._message_content_to_text(getattr(latest_user, "content", "")),
                cls.COMPACTED_LATEST_USER_MAX_CHARS,
            )

        transcript = cls._build_compacted_transcript(
            body,
            max_chars=cls.COMPACTED_TRANSCRIPT_MAX_CHARS,
        )
        summary_sections = [
            "# Compacted Conversation State",
            reason
            or "The previous in-turn context was compacted automatically after the LLM reported a context-window error.",
            "Continue the same task from this state; do not restart or ask the user to repeat details already summarized here.",
        ]
        if latest_user_text:
            summary_sections.extend(["", "## Latest User Instruction", latest_user_text])
        summary_sections.extend(["", "## Compacted Transcript", transcript or "(no transcript details)"])
        if tool_results_history:
            summary_sections.extend([
                "",
                "## Recent Tool Results",
                "\n".join(f"- {item}" for item in tool_results_history[-8:]),
            ])

        return [
            *leading_system,
            ChatMessage(role="system", content="\n".join(summary_sections)),
            ChatMessage(role="user", content=cls.CONTINUATION_AFTER_COMPACTION_MESSAGE),
        ]


    async def execute_messages(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        allow_tools: bool,
        tool_result_chat_id: str | None = None,
        tool_registry: ToolRegistry | None = None,
        on_tool_before_execute: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_tool_after_execute: Callable[[str, dict[str, Any], str], Awaitable[None]] | None = None,
        on_llm_status: Callable[[str], Awaitable[None]] | None = None,
        refresh_system_prompt: Callable[[], str] | None = None,
        max_tool_iterations: int | None = None,
    ) -> ExecutionResult:
        """Execute the prepared messages, including tool calls when enabled."""
        active_tools = tool_registry or self.tools
        tools = None
        if allow_tools and active_tools.tool_names:
            tools = active_tools.get_definitions()
            logger.info(f"[{log_id}] tools.enabled | names={', '.join(active_tools.tool_names)}")

        tool_results_history: list[str] = []
        empty_response_retried = False
        repeated_tool_error_key: tuple[str, str] | None = None
        repeated_tool_error_count = 0
        executed_tool_calls = 0
        used_configure_skill = False
        had_tool_error = False
        context_compactions = 0
        context_compaction_events: list[ContextCompactionEvent] = []
        proactive_context_compactions = 0
        overflow_context_compactions = 0
        iteration_limit = (
            max_tool_iterations if max_tool_iterations is not None else self.tools_config.max_tool_iterations
        )

        for iteration in range(iteration_limit):
            if proactive_context_compactions < self.PROACTIVE_CONTEXT_COMPACTION_LIMIT:
                proactive_compaction = await self._build_proactive_compaction(
                    log_id,
                    chat_messages,
                    tools=tools,
                    tool_results_history=tool_results_history,
                )
                if proactive_compaction is not None:
                    proactive_context_compactions += 1
                    context_compactions += 1
                    before_count = len(chat_messages)
                    chat_messages[:] = proactive_compaction.messages
                    context_compaction_events.append(
                        ContextCompactionEvent(
                            trigger="proactive",
                            strategy=proactive_compaction.strategy,
                            outcome="fallback" if proactive_compaction.fallback_reason else "compacted",
                            iteration=iteration + 1,
                            messages_before=before_count,
                            messages_after=len(chat_messages),
                            estimated_tokens=proactive_compaction.estimated_tokens,
                            compacted_tokens=proactive_compaction.compacted_tokens,
                            threshold_tokens=proactive_compaction.threshold_tokens,
                            budget_tokens=self.context_compaction_token_budget,
                            message_tokens=proactive_compaction.message_tokens,
                            tool_schema_tokens=proactive_compaction.tool_schema_tokens,
                            fallback_reason=proactive_compaction.fallback_reason,
                            error=proactive_compaction.error,
                        )
                    )
                    logger.warning(
                        f"[{log_id}] llm.context-proactive-compact | iter={iteration + 1} "
                        f"compaction={proactive_context_compactions}/{self.PROACTIVE_CONTEXT_COMPACTION_LIMIT} "
                        f"strategy={proactive_compaction.strategy} outcome={context_compaction_events[-1].outcome} "
                        f"estimated_tokens={proactive_compaction.estimated_tokens} compacted_tokens={proactive_compaction.compacted_tokens} "
                        f"threshold={proactive_compaction.threshold_tokens} budget={self.context_compaction_token_budget} "
                        f"message_tokens={proactive_compaction.message_tokens} tool_schema_tokens={proactive_compaction.tool_schema_tokens} "
                        f"messages_before={before_count} messages_after={len(chat_messages)}"
                    )
                    if on_llm_status is not None:
                        try:
                            await on_llm_status(self.PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE)
                        except Exception:
                            logger.exception(f"[{log_id}] llm.context-proactive.status-hook.error")

            logger.info(
                f"[{log_id}] llm.request | iter={iteration + 1} messages={len(chat_messages)} "
                f"tools={'on' if tools else 'off'} tail={self.summarize_messages(chat_messages)}"
            )
            while True:
                try:
                    if self.pass_decoding_params:
                        dec_temp = self.chat_temperature
                        dec_max = self.chat_max_tokens
                        dec_top_p = self.chat_top_p
                        dec_freq = self.chat_frequency_penalty
                        dec_pres = self.chat_presence_penalty
                    else:
                        dec_temp = dec_max = dec_top_p = dec_freq = dec_pres = None
                    response = await self.provider.chat(
                        messages=chat_messages,
                        tools=tools,
                        temperature=dec_temp,
                        max_tokens=dec_max,
                        top_p=dec_top_p,
                        frequency_penalty=dec_freq,
                        presence_penalty=dec_pres,
                        status_callback=on_llm_status,
                    )
                    break
                except Exception as exc:
                    if (
                        overflow_context_compactions < self.CONTEXT_COMPACTION_RETRY_LIMIT
                        and self._looks_like_context_overflow(exc)
                    ):
                        compacted_messages = self._compact_messages_for_continuation(
                            chat_messages,
                            tool_results_history=tool_results_history,
                        )
                        if compacted_messages is not None:
                            overflow_context_compactions += 1
                            context_compactions += 1
                            before_count = len(chat_messages)
                            estimated_tokens, message_tokens, tool_schema_tokens = self._estimate_request_tokens(
                                chat_messages,
                                tools,
                            )
                            compacted_tokens, _, _ = self._estimate_request_tokens(compacted_messages, tools)
                            error_preview = self.format_log_preview(str(exc), max_chars=240)
                            chat_messages[:] = compacted_messages
                            context_compaction_events.append(
                                ContextCompactionEvent(
                                    trigger="overflow",
                                    strategy="deterministic",
                                    outcome="compacted",
                                    iteration=iteration + 1,
                                    messages_before=before_count,
                                    messages_after=len(chat_messages),
                                    estimated_tokens=estimated_tokens,
                                    compacted_tokens=compacted_tokens,
                                    budget_tokens=self.context_compaction_token_budget,
                                    message_tokens=message_tokens,
                                    tool_schema_tokens=tool_schema_tokens,
                                    error=error_preview,
                                )
                            )
                            logger.warning(
                                f"[{log_id}] llm.context-overflow | iter={iteration + 1} "
                                f"compaction={overflow_context_compactions}/{self.CONTEXT_COMPACTION_RETRY_LIMIT} "
                                f"messages_before={before_count} messages_after={len(chat_messages)} retrying=true "
                                f"error={error_preview}"
                            )
                            if on_llm_status is not None:
                                try:
                                    await on_llm_status(self.CONTEXT_OVERFLOW_STATUS_MESSAGE)
                                except Exception:
                                    logger.exception(f"[{log_id}] llm.context-overflow.status-hook.error")
                            continue

                    logger.exception(
                        f"[{log_id}] llm.error | iter={iteration + 1} messages={len(chat_messages)} "
                        f"tools={'on' if tools else 'off'} tail={self.summarize_messages(chat_messages)}"
                    )
                    raise

            raw_content = response.content or ""
            response.content = self.sanitize_response_content(raw_content)
            sanitized_became_empty = bool(raw_content.strip() and not response.content)
            tool_calls_count = len(response.tool_calls or [])
            logger.info(
                f"[{log_id}] llm.response | iter={iteration + 1} model={response.model} raw_len={len(raw_content)} "
                f"visible_len={len(response.content)} tool_calls={tool_calls_count} "
                f"preview={self.format_log_preview(response.content)}"
            )
            if sanitized_became_empty:
                logger.warning(
                    f"[{log_id}] llm.sanitized-empty | iter={iteration + 1} raw_len={len(raw_content)} raw_non_ws={len(raw_content.strip())} "
                    f"tool_calls={tool_calls_count} tools={self._summarize_tool_names(response.tool_calls)} "
                    f"raw_preview={self.format_log_preview(raw_content, max_chars=240)}"
                )
                logger.warning(
                    f"[{log_id}] llm.raw-hidden-blocks | iter={iteration + 1} raw_content={raw_content[:500]}"
                )
                if "<system-reminder>" in raw_content:
                    logger.warning(
                        f"[{log_id}] llm.system-reminder-hidden | iter={iteration + 1} raw_len={len(raw_content)} "
                        f"visible_len={len(response.content)} tool_calls={tool_calls_count} "
                        f"tools={self._summarize_tool_names(response.tool_calls)}"
                    )
                    logger.warning(
                        f"[{log_id}] llm.system-reminder-hidden-state | iter={iteration + 1} "
                        f"phase={'tool-call' if tool_calls_count else 'final-response'} "
                        f"tool_history_count={len(tool_results_history)} sanitized_from_nonempty=true"
                    )

            if response.tool_calls:
                if not tools:
                    logger.warning(
                        f"[{log_id}] llm.tool-calls-ignored | iter={iteration + 1} count={len(response.tool_calls)} tools=off"
                    )
                    if not response.content:
                        return ExecutionResult(
                            content=self.empty_response_fallback,
                            executed_tool_calls=executed_tool_calls,
                            used_configure_skill=used_configure_skill,
                            had_tool_error=had_tool_error,
                            context_compactions=context_compactions,
                            context_compaction_events=context_compaction_events,
                        )

                    return ExecutionResult(
                        content=response.content,
                        executed_tool_calls=executed_tool_calls,
                        used_configure_skill=used_configure_skill,
                        had_tool_error=had_tool_error,
                        context_compactions=context_compactions,
                        context_compaction_events=context_compaction_events,
                    )

                logger.info(
                    f"[{log_id}] llm.tool-calls | iter={iteration + 1} count={len(response.tool_calls)} "
                    f"tools={self._summarize_tool_names(response.tool_calls)} visible_len={len(response.content)}"
                )

                tool_calls_api = []
                for tc in response.tool_calls:
                    tool_calls_api.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    })

                chat_messages.append(ChatMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=tool_calls_api,
                ))

                for tc in response.tool_calls:
                    tool_name = tc.name
                    tool_args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    args_preview = self.format_log_preview(json.dumps(tool_args, ensure_ascii=False), max_chars=200)
                    logger.info(f"[{log_id}] tool.run | id={tc.id} name={tool_name} args={args_preview}")

                    async def _notify_tool_before_execute(name: str, args: dict[str, Any]) -> None:
                        if on_tool_before_execute is None:
                            return
                        try:
                            await on_tool_before_execute(name, args)
                        except Exception:
                            logger.exception(
                                f"[{log_id}] tool.progress-hook.error | name={name}"
                            )

                    result = await active_tools.execute(
                        tool_name,
                        tool_args,
                        on_before_execute=_notify_tool_before_execute,
                    )
                    executed_tool_calls += 1
                    if self._tool_result_looks_like_failure(result):
                        had_tool_error = True
                    if tool_name == "configure_skill" and tool_args.get("action") in ("add", "upsert"):
                        used_configure_skill = True
                    logger.info(
                        f"[{log_id}] tool.result | name={tool_name} preview={self.format_log_preview(result, max_chars=200)}"
                    )
                    if on_tool_after_execute is not None:
                        try:
                            await on_tool_after_execute(tool_name, tool_args, result)
                        except Exception:
                            logger.exception(
                                f"[{log_id}] tool.result-hook.error | name={tool_name}"
                            )
                    result_for_context = self._summarize_tool_result_for_context(tool_name, result)

                    repeated_error_marker = self._classify_tool_result(result)
                    if repeated_error_marker is not None:
                        current_error_key = (tool_name, repeated_error_marker)
                        if repeated_tool_error_key == current_error_key:
                            repeated_tool_error_count += 1
                        else:
                            repeated_tool_error_key = current_error_key
                            repeated_tool_error_count = 1

                        if repeated_tool_error_count >= self.REPEATED_TOOL_ERROR_LIMIT:
                            logger.warning(
                                f"[{log_id}] tool.repeated-error | name={tool_name} count={repeated_tool_error_count} stopping_early=true"
                            )
                            return ExecutionResult(
                                content=(
                                    "我重複嘗試呼叫工具，但工具參數仍然無效而無法繼續。"
                                    f"最新錯誤：{result}"
                                ),
                                executed_tool_calls=executed_tool_calls,
                                used_configure_skill=used_configure_skill,
                                had_tool_error=had_tool_error,
                                context_compactions=context_compactions,
                                context_compaction_events=context_compaction_events,
                            )
                    else:
                        repeated_tool_error_key = None
                        repeated_tool_error_count = 0

                    tool_results_history.append(f"{tool_name}: {result_for_context[:200]}")
                    chat_messages.append(ChatMessage(
                        role="tool",
                        content=result_for_context,
                        tool_call_id=tc.id,
                    ))

                    if (
                        refresh_system_prompt is not None
                        and self._should_refresh_main_system_after_tool(tool_name, tool_args)
                        and self._tool_result_ok_for_system_refresh(result)
                    ):
                        try:
                            new_system = refresh_system_prompt()
                            if chat_messages and chat_messages[0].role == "system":
                                chat_messages[0].content = new_system
                                if allow_tools and active_tools.tool_names:
                                    tools = active_tools.get_definitions()
                                logger.info(
                                    f"[{log_id}] prompt.refresh | after_tool={tool_name} "
                                    "system_rebuilt=true tools_refreshed=true"
                                )
                        except Exception:
                            logger.exception(f"[{log_id}] prompt.refresh.error | after_tool={tool_name}")

                    await self.tool_result_persistence.persist(
                        chat_id=tool_result_chat_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        result=result,
                    )

                continue

            if not response.content:
                if not empty_response_retried:
                    empty_response_retried = True
                    logger.warning(
                        f"[{log_id}] llm.empty-visible-response | iter={iteration + 1} retrying_once=true "
                        f"sanitized_from_nonempty={'true' if sanitized_became_empty else 'false'} "
                        f"tool_history_count={len(tool_results_history)}"
                    )
                    chat_messages.append(
                        ChatMessage(
                            role="system",
                            content=(
                                self.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE
                                if sanitized_became_empty
                                else self.EMPTY_RESPONSE_RETRY_MESSAGE
                            ),
                        )
                    )
                    continue

                logger.warning(
                    f"[{log_id}] llm.empty-visible-response | iter={iteration + 1} using_fallback=true "
                    f"sanitized_from_nonempty={'true' if sanitized_became_empty else 'false'} "
                    f"tool_history_count={len(tool_results_history)}"
                )
                return ExecutionResult(
                    content=self.empty_response_fallback,
                    executed_tool_calls=executed_tool_calls,
                    used_configure_skill=used_configure_skill,
                    had_tool_error=had_tool_error,
                    context_compactions=context_compactions,
                    context_compaction_events=context_compaction_events,
                )

            return ExecutionResult(
                content=response.content,
                executed_tool_calls=executed_tool_calls,
                used_configure_skill=used_configure_skill,
                had_tool_error=had_tool_error,
                context_compactions=context_compactions,
                context_compaction_events=context_compaction_events,
            )

        logger.warning(f"[{log_id}] llm.max-iterations | limit={iteration_limit}")

        history_msg = ""
        if tool_results_history:
            history_msg = "\n\n我嘗試了以下工具但未能完成任務：\n" + "\n".join(
                f"- {result}" for result in tool_results_history[-5:]
            )

        return ExecutionResult(
            content=(
                f"我嘗試完成你的請求，但超過了最大迭代次數（{iteration_limit}次）。"
                f"請將任務拆分為較小的步驟。{history_msg}"
            ),
            executed_tool_calls=executed_tool_calls,
            used_configure_skill=used_configure_skill,
            had_tool_error=had_tool_error,
            context_compactions=context_compactions,
            context_compaction_events=context_compaction_events,
        )
