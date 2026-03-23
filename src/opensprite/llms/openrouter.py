"""
opensprite/llms/openrouter.py - OpenRouter LLM 實作

實作 LLMProvider 介面，使用 OpenRouter API
OpenRouter 可以訪問多種 LLM 模型（OpenAI、Anthropic、Meta 等）
"""
from typing import Any

from .base import LLMProvider, LLMResponse, ChatMessage, ToolCall


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
        
        message = response.choices[0].message
        
        # 解析 tool calls
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                import json
                try:
                    args = json.loads(tc.function.arguments)
                except:
                    args = {}
                
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args
                ))
        
        return LLMResponse(
            content=message.content or "",
            model=response.model,
            tool_calls=tool_calls
        )
    
    def get_default_model(self) -> str:
        return self.default_model
