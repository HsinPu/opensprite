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

    async def execute_messages(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        allow_tools: bool,
        tool_result_chat_id: str | None = None,
    ) -> str:
        """Execute the prepared messages, including tool calls when enabled."""
        tools = None
        if allow_tools and self.tools.tool_names:
            tools = self.tools.get_definitions()
            logger.info(f"[{log_id}] tools.enabled | names={', '.join(self.tools.tool_names)}")

        tool_results_history: list[str] = []

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
            logger.info(
                f"[{log_id}] llm.response | iter={iteration + 1} model={response.model} raw_len={len(raw_content)} "
                f"visible_len={len(response.content)} tool_calls={len(response.tool_calls or [])} "
                f"preview={self.format_log_preview(response.content)}"
            )
            if raw_content and not response.content:
                logger.warning(
                    f"[{log_id}] llm.sanitized-empty | iter={iteration + 1} raw_preview={self.format_log_preview(raw_content, max_chars=240)}"
                )

            if response.tool_calls:
                if not tools:
                    logger.warning(
                        f"[{log_id}] llm.tool-calls-ignored | iter={iteration + 1} count={len(response.tool_calls)} tools=off"
                    )
                    if not response.content:
                        return self.empty_response_fallback

                    return response.content

                logger.info(f"[{log_id}] llm.tool-calls | iter={iteration + 1} count={len(response.tool_calls)}")

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

                    result = await self.tools.execute(tool_name, tool_args)
                    logger.info(
                        f"[{log_id}] tool.result | name={tool_name} preview={self.format_log_preview(result, max_chars=200)}"
                    )

                    tool_results_history.append(f"{tool_name}: {result[:200]}")
                    chat_messages.append(ChatMessage(
                        role="tool",
                        content=result,
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
                logger.warning(f"[{log_id}] llm.empty-visible-response | using_fallback=true")
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
