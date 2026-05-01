"""
opensprite/llms/minimax.py - MiniMax LLM 實作

實作 LLMProvider 介面，使用 MiniMax API
官網：https://www.minimax.io/
"""
import asyncio
import random
from typing import Any, Awaitable, Callable

from .base import LLMProvider, LLMResponse, ChatMessage, ToolCall
from .tool_args import parse_tool_arguments
from ..utils.log import logger


def _safe_len(value: Any) -> str:
    try:
        return str(len(value))
    except Exception:
        return "n/a"


def _coerce_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def _preview_text(value: Any, max_chars: int = 240) -> str:
    text = _coerce_content(value).replace("\n", "\\n")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _contains_system_reminder(value: Any) -> bool:
    return "<system-reminder>" in _coerce_content(value)


def _count_system_reminders(value: Any) -> int:
    return _coerce_content(value).count("<system-reminder>")


def _is_minimax_overloaded_error(exc: BaseException) -> bool:
    """MiniMax 在流量過載時回傳 HTTP 529（OpenAI SDK 為 InternalServerError）。"""
    code = getattr(exc, "status_code", None)
    if code == 529:
        return True
    lowered = str(exc).lower()
    if "overloaded_error" in lowered or "high traffic detected" in lowered:
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("type") == "overloaded_error":
            return True
    return False


class MiniMaxLLM(LLMProvider):
    """
    MiniMax LLM 實作
    
    使用 MiniMax API（OpenAI 相容）
    API 文件：https://www.minimax.io/docs/api
    """
    
    def __init__(
        self, 
        api_key: str, 
        default_model: str = "MiniMax-M2.5"
    ):
        """
        初始化 MiniMax LLM
        
        參數：
            api_key: MiniMax API Key
            default_model: 預設模型名稱
        """
        from openai import AsyncOpenAI
        
        self.api_key = api_key
        self.default_model = default_model
        
        # MiniMax 使用 OpenAI 相容 API
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.minimax.io/v1"
        )

    async def _chat_completions_create(
        self,
        params: dict[str, Any],
        *,
        status_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> Any:
        """呼叫 chat completions；遇 MiniMax 529（overloaded_error）時採指數退避重試。"""
        from openai import InternalServerError

        max_attempts = 5
        base_delay_sec = 2.0

        for attempt in range(max_attempts):
            try:
                return await self.client.chat.completions.create(**params)
            except InternalServerError as e:
                if not _is_minimax_overloaded_error(e):
                    raise
                if attempt >= max_attempts - 1:
                    logger.error(
                        "MiniMax API 流量過載（529）已重試 {} 次仍失敗；可稍後再試、升級方案或使用高速模型："
                        "https://platform.minimax.io/subscribe/token-plan | {}",
                        max_attempts,
                        e,
                    )
                    raise
                delay = base_delay_sec * (2**attempt) + random.uniform(0, 1.0)
                logger.warning(
                    "MiniMax API 流量過載（529），{:.1f} 秒後重試（第 {}／{} 次）",
                    delay,
                    attempt + 1,
                    max_attempts,
                )
                if status_callback is not None:
                    notice = (
                        "MiniMax 目前流量較高（HTTP 529），"
                        f"約 {delay:.0f} 秒後會自動重試（第 {attempt + 1}／{max_attempts} 次）。"
                        "若經常發生，可考慮升級方案或使用高速模型："
                        "https://platform.minimax.io/subscribe/token-plan"
                    )
                    try:
                        await status_callback(notice)
                    except Exception as cb_err:
                        logger.warning("MiniMax status_callback 失敗（仍會繼續重試）：{}", cb_err)
                await asyncio.sleep(delay)
    
    async def chat(
        self, 
        messages: list[ChatMessage], 
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        status_callback: Callable[[str], Awaitable[None]] | None = None,
        response_delta_callback: Callable[[str], Awaitable[None]] | None = None,
        tool_input_delta_callback: Callable[[str, str, str, int], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
        呼叫 MiniMax Chat Completions API
        """
        _ = response_delta_callback
        _ = tool_input_delta_callback
        # 轉換成 OpenAI 格式
        api_messages = []
        for m in messages:
            if isinstance(m, dict):
                msg = {"role": m.get("role", "?"), "content": m.get("content", "")}
                if m.get("tool_call_id"):
                    msg["tool_call_id"] = m["tool_call_id"]
                if m.get("tool_calls"):
                    msg["tool_calls"] = m["tool_calls"]
            else:
                msg = {"role": m.role, "content": m.content}
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                if m.tool_calls:
                    msg["tool_calls"] = m.tool_calls
            api_messages.append(msg)

        request_reminder_hits: list[str] = []
        for index, msg in enumerate(api_messages, start=1):
            content = msg.get("content", "")
            text = content if isinstance(content, str) else _coerce_content(content)
            if not _contains_system_reminder(text):
                continue
            request_reminder_hits.append(f"{index}:{msg.get('role', '?')}")
            logger.warning(
                "MiniMax request contains system-reminder: index={} role={} len={} preview={}",
                index,
                msg.get("role", "?"),
                len(text),
                _preview_text(text),
            )
        request_reminder_count = sum(_count_system_reminders(msg.get("content", "")) for msg in api_messages)
        if request_reminder_hits:
            logger.warning(
                "MiniMax request system-reminder summary: message_count={} reminder_count={} hits={}",
                len(api_messages),
                request_reminder_count,
                ", ".join(request_reminder_hits),
            )

        params: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": api_messages,
        }
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if top_p is not None:
            params["top_p"] = top_p
        if frequency_penalty is not None:
            params["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            params["presence_penalty"] = presence_penalty

        # 加入 tools 如果有
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        
        # 呼叫 API（含 529 過載重試；可選通知使用者後再退避）
        response = await self._chat_completions_create(params, status_callback=status_callback)
        choices = getattr(response, "choices", None)
        logger.info(
            "MiniMax response summary: model={}, choices_type={}, choices_len={}",
            getattr(response, "model", None),
            type(choices).__name__,
            _safe_len(choices),
        )

        # Debug: log raw MiniMax response for diagnostics
        logger.debug(
            "MiniMax raw response: id={}, model={}, usage={}, finish_reason={}",
            getattr(response, "id", None),
            getattr(response, "model", None),
            getattr(response, "usage", None),
            getattr(getattr(choices[0], "finish_reason", None) if choices else None, "value", None) if choices else None,
        )

        if not choices:
            logger.warning(
                "MiniMax returned empty choices: response_id={}, model={}, object={}, usage={}",
                getattr(response, "id", None),
                getattr(response, "model", None),
                getattr(response, "object", None),
                getattr(response, "usage", None),
            )
            return LLMResponse(
                content="",
                model=getattr(response, "model", model or self.default_model),
                tool_calls=[],
            )

        try:
            message = choices[0].message
        except Exception:
            logger.exception(
                "MiniMax response parse failed: response_type={}, model={}, choices_type={}, choices_len={}, choices_preview={}",
                type(response).__name__,
                getattr(response, "model", None),
                type(choices).__name__,
                _safe_len(choices),
                repr(choices)[:500],
            )
            return LLMResponse(
                content="",
                model=getattr(response, "model", model or self.default_model),
                tool_calls=[],
            )

        # Log raw message content for debugging hidden blocks
        raw_message_content = getattr(message, "content", "")
        logger.info(
            "MiniMax raw message content: len={} preview={}",
            len(raw_message_content) if raw_message_content else 0,
            (raw_message_content[:500] if raw_message_content else "")[:200],
        )
        if _contains_system_reminder(raw_message_content):
            response_reminder_count = _count_system_reminders(raw_message_content)
            logger.warning(
                "MiniMax response contains system-reminder: len={} reminder_count={} tool_calls_count={} preview={}",
                len(raw_message_content),
                response_reminder_count,
                _safe_len(getattr(message, "tool_calls", None)),
                _preview_text(raw_message_content),
            )
            logger.warning(
                "MiniMax system-reminder provenance: request_reminder_count={} response_reminder_count={} source={}",
                request_reminder_count,
                response_reminder_count,
                "model_generated" if request_reminder_count == 0 else "request_echo_or_model_continuation",
            )

        # Log raw tool calls for debugging
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            for tc in raw_tool_calls:
                func = getattr(tc, "function", None)
                raw_arguments = getattr(func, "arguments", None)
                logger.info(
                    "MiniMax raw tool_call: id={}, name={}, arguments_type={}, arguments_preview={}",
                    getattr(tc, "id", None),
                    getattr(func, "name", None),
                    type(raw_arguments).__name__,
                    str(raw_arguments)[:200] if raw_arguments is not None else "None",
                )
                if _contains_system_reminder(raw_arguments):
                    logger.warning(
                        "MiniMax tool_call arguments contain system-reminder: id={} name={} preview={}",
                        getattr(tc, "id", None),
                        getattr(func, "name", None),
                        _preview_text(raw_arguments),
                    )

        if message is None:
            logger.warning("MiniMax response missing message payload; returning empty response")
            return LLMResponse(
                content="",
                model=getattr(response, "model", model or self.default_model),
                tool_calls=[],
            )
        
        # 解析 tool calls
        tool_calls = []
        if getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                function = getattr(tc, "function", None)
                if function is None:
                    logger.warning("MiniMax tool call missing function payload; skipping")
                    continue
                args = parse_tool_arguments(
                    getattr(function, "arguments", None),
                    provider_name="MiniMax",
                    tool_name=getattr(function, "name", "") or "",
                )
                
                tool_calls.append(ToolCall(
                    id=getattr(tc, "id", "") or f"tool_call_{len(tool_calls) + 1}",
                    name=getattr(function, "name", "") or "",
                    arguments=args
                ))
        
        return LLMResponse(
            content=_coerce_content(getattr(message, "content", "")),
            model=getattr(response, "model", model or self.default_model),
            tool_calls=tool_calls
        )
    
    def get_default_model(self) -> str:
        return self.default_model
