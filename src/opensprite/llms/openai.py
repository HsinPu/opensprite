"""
opensprite/llms/openai.py - OpenAI LLM 實作

實作 LLMProvider 介面，使用 OpenAI API（或相容 API 如 OpenRouter、vLLM）

"""
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


class OpenAILLM(LLMProvider):
    """
    OpenAI LLM 實作
    
    使用 OpenAI API（或相容 API 如 OpenRouter、本地 vLLM）
    """
    
    def __init__(
        self, 
        api_key: str, 
        base_url: str | None = None,
        default_model: str = "gpt-4o-mini"
    ):
        """
        初始化 OpenAI LLM
        
        參數：
            api_key: OpenAI API Key
            base_url: API 端點（可選，例如用 OpenRouter 或本地模型）
            default_model: 預設模型名稱
        """
        from openai import AsyncOpenAI
        
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        
        # 建立客戶端
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        
        self.client = AsyncOpenAI(**kwargs)
    
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
    ) -> LLMResponse:
        """
        呼叫 OpenAI Chat Completions API
        """
        _ = status_callback
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
                # 支援混合內容（文字+圖片）
                if isinstance(m.content, list):
                    # 已經是混合格式，直接傳遞
                    msg = {"role": m.role, "content": m.content}
                else:
                    msg = {"role": m.role, "content": m.content}
                
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                if m.tool_calls:
                    msg["tool_calls"] = m.tool_calls
            api_messages.append(msg)
        
        # API 參數（None = 不帶該欄位，交給服務端預設）
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

        if response_delta_callback is not None and not tools:
            params["stream"] = True
            content_parts: list[str] = []
            model_name = model or self.default_model
            stream = await self.client.chat.completions.create(**params)
            async for chunk in stream:
                model_name = getattr(chunk, "model", model_name)
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta_payload = getattr(choices[0], "delta", None)
                piece = _coerce_content(getattr(delta_payload, "content", "")) if delta_payload is not None else ""
                if not piece:
                    continue
                content_parts.append(piece)
                try:
                    await response_delta_callback(piece)
                except Exception as cb_err:
                    logger.warning("OpenAI response_delta_callback failed; continuing stream: {}", cb_err)
            return LLMResponse(content="".join(content_parts), model=model_name, tool_calls=[])

        # 呼叫 API
        response = await self.client.chat.completions.create(**params)
        choices = getattr(response, "choices", None)
        logger.info(
            "OpenAI response summary: model={}, choices_type={}, choices_len={}",
            getattr(response, "model", None),
            type(choices).__name__,
            _safe_len(choices),
        )

        if not choices:
            logger.warning(
                "OpenAI returned empty choices: response_id={}, model={}, object={}, usage={}",
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
                "OpenAI response parse failed: response_type={}, model={}, choices_type={}, choices_len={}, choices_preview={}",
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
            logger.warning("OpenAI response missing message payload; returning empty response")
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
                    logger.warning("OpenAI tool call missing function payload; skipping")
                    continue
                args = parse_tool_arguments(
                    getattr(function, "arguments", None),
                    provider_name="OpenAI",
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
