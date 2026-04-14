"""Shared execution loop for agent and subagent message runs."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from ..config import ToolsConfig
from ..llms import ChatMessage, LLMProvider
from ..search.base import SearchStore
from ..tools import ToolRegistry
from ..utils.log import logger


class ToolResultPersistence:
    """Persist tool execution results to storage and optional search indexes."""

    def __init__(
        self,
        *,
        save_message: Callable[[str, str, str, str | None], Awaitable[None]],
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

        await self.save_message(chat_id, "tool", result, tool_name)
        if self.search_store is not None:
            try:
                await self.search_store.index_tool_result(chat_id, tool_name, tool_args, result)
            except Exception as e:
                logger.warning("[{}] Failed to index tool result for search: {}", chat_id, e)


class ExecutionEngine:
    """Run the LLM and tool-calling loop for prepared chat messages."""

    REPEATED_TOOL_ERROR_LIMIT = 2
    TOOL_RESULT_MAX_CHARS = 1200
    EXEC_RESULT_MAX_CHARS = 1200
    EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Previous attempt produced no visible user-facing text. "
        "Please answer again with a direct, displayable reply for the user."
    )
    SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Previous attempt only contained hidden control content and no displayable user-facing text. "
        "Do not output <think> or <thinking> tags. If you need tools, call them. Otherwise answer now in plain visible text for the user."
    )
    TOOL_LOOP_EMPTY_RESPONSE_FALLBACK = (
        "抱歉，我已執行工具，但模型沒有產生可顯示的最終回覆。"
        "請再試一次，或把任務拆成更小步驟。"
    )

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
    ):
        self.provider = provider
        self.tools = tools
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
    def _classify_tool_result(result: str) -> str | None:
        """Classify tool-result errors that should trigger early stopping."""
        if result.startswith("Error: Missing required argument"):
            return result
        return None

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
        """Build a compact tool name list for diagnostics."""
        if not tool_calls:
            return "-"
        names = [getattr(tc, "name", "") or "<unknown>" for tc in tool_calls]
        preview = ", ".join(names[:5])
        if len(names) > 5:
            preview += f", ... (+{len(names) - 5} more)"
        return preview

    async def execute_messages(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        allow_tools: bool,
        tool_result_chat_id: str | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> str:
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

        for iteration in range(self.tools_config.max_tool_iterations):
            logger.info(
                f"[{log_id}] llm.request | iter={iteration + 1} messages={len(chat_messages)} "
                f"tools={'on' if tools else 'off'} tail={self.summarize_messages(chat_messages)}"
            )
            try:
                response = await self.provider.chat(
                    messages=chat_messages,
                    tools=tools,
                )
            except Exception:
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

            if response.tool_calls:
                if not tools:
                    logger.warning(
                        f"[{log_id}] llm.tool-calls-ignored | iter={iteration + 1} count={len(response.tool_calls)} tools=off"
                    )
                    if not response.content:
                        return self.empty_response_fallback

                    return response.content

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
                    tool_args = tc.arguments
                    args_preview = self.format_log_preview(json.dumps(tool_args, ensure_ascii=False), max_chars=200)
                    logger.info(f"[{log_id}] tool.run | id={tc.id} name={tool_name} args={args_preview}")

                    result = await active_tools.execute(tool_name, tool_args)
                    logger.info(
                        f"[{log_id}] tool.result | name={tool_name} preview={self.format_log_preview(result, max_chars=200)}"
                    )

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
                            return (
                                "我重複嘗試呼叫工具，但仍然缺少必要參數而無法繼續。"
                                f"最新錯誤：{result}"
                            )
                    else:
                        repeated_tool_error_key = None
                        repeated_tool_error_count = 0

                    tool_results_history.append(f"{tool_name}: {result[:200]}")
                    context_result = self._summarize_tool_result_for_context(tool_name, result)
                    chat_messages.append(ChatMessage(
                        role="tool",
                        content=context_result,
                        tool_call_id=tc.id,
                    ))

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
                if tool_results_history:
                    logger.warning(
                        f"[{log_id}] llm.tool-loop-empty-final | iter={iteration + 1} "
                        f"tool_history_tail={tool_results_history[-3:]}"
                    )
                    return self.TOOL_LOOP_EMPTY_RESPONSE_FALLBACK
                return self.empty_response_fallback

            return response.content

        logger.warning(f"[{log_id}] llm.max-iterations | limit={self.tools_config.max_tool_iterations}")

        history_msg = ""
        if tool_results_history:
            history_msg = "\n\n我嘗試了以下工具但未能完成任務：\n" + "\n".join(
                f"- {result}" for result in tool_results_history[-5:]
            )

        return (
            f"我嘗試完成你的請求，但超過了最大迭代次數（{self.tools_config.max_tool_iterations}次）。"
            f"請將任務拆分為較小的步驟。{history_msg}"
        )
