"""
opensprite/llms/minimax.py - MiniMax LLM 實作

實作 LLMProvider 介面，使用 MiniMax API
官網：https://www.minimax.io/
"""
from typing import Any

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
    
    async def chat(
        self, 
        messages: list[ChatMessage], 
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> LLMResponse:
        """
        呼叫 MiniMax Chat Completions API
        """
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
        
        # API 參數
        params = {
            "model": model or self.default_model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        # 加入 tools 如果有
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        
        # 呼叫 API
        response = await self.client.chat.completions.create(**params)
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

        # Log raw message content for debugging hidden blocks
        raw_message_content = getattr(message, "content", "")
        logger.info(
            "MiniMax raw message content: len={} preview={}",
            len(raw_message_content) if raw_message_content else 0,
            (raw_message_content[:500] if raw_message_content else "")[:200],
        )

        # Log raw tool calls for debugging
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            for tc in raw_tool_calls:
                func = getattr(tc, "function", None)
                logger.info(
                    "MiniMax raw tool_call: id={}, name={}, arguments_type={}, arguments_preview={}",
                    getattr(tc, "id", None),
                    getattr(func, "name", None),
                    type(getattr(func, "arguments", None)).__name__,
                    str(getattr(func, "arguments", ""))[:200] if getattr(func, "arguments", None) else "None",
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
