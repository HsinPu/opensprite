"""
opensprite/llms/openrouter.py - OpenRouter LLM 實作

實作 LLMProvider 介面，使用 OpenRouter API
OpenRouter 可以訪問多種 LLM 模型（OpenAI、Anthropic、Meta 等）
"""
from typing import Any

from .base import LLMProvider, LLMResponse, ChatMessage, ToolCall
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


class OpenRouterLLM(LLMProvider):
    """
    OpenRouter LLM 實作
    
    使用 OpenRouter API，可以訪問多種 LLM 模型
    官網：https://openrouter.ai/
    """
    
    def __init__(
        self, 
        api_key: str, 
        default_model: str = "openai/gpt-4o-mini"
    ):
        """
        初始化 OpenRouter LLM
        
        參數：
            api_key: OpenRouter API Key
            default_model: 預設模型名稱
                常用模型：
                - openai/gpt-4o-mini
                - openai/gpt-4o
                - anthropic/claude-3.5-sonnet
                - meta-llama/llama-3.1-70b-instruct
                - google/gemma-2-27b-instruct
        """
        from openai import AsyncOpenAI
        
        self.api_key = api_key
        self.default_model = default_model
        
        # 建立客戶端
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            # OpenRouter 需要這些 headers
            default_headers={
                "HTTP-Referer": "https://github.com/HsinPu/opensprite",
                "X-Title": "OpenSprite"
            }
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
        呼叫 OpenRouter Chat Completions API
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
            "OpenRouter response summary: model={}, choices_type={}, choices_len={}",
            getattr(response, "model", None),
            type(choices).__name__,
            _safe_len(choices),
        )

        try:
            message = response.choices[0].message
        except Exception:
            logger.exception(
                "OpenRouter response parse failed: response_type={}, model={}, choices_type={}, choices_len={}, choices_preview={}",
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
            logger.warning("OpenRouter response missing message payload; returning empty response")
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
                    logger.warning("OpenRouter tool call missing function payload; skipping")
                    continue
                import json
                try:
                    raw_args = getattr(function, "arguments", None)
                    if isinstance(raw_args, str):
                        args = json.loads(raw_args) if raw_args.strip() else {}
                    elif isinstance(raw_args, dict):
                        args = raw_args
                    else:
                        args = {}
                except:
                    args = {}
                
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
