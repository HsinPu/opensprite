"""
minibot/llms/openrouter.py - OpenRouter LLM 實作

實作 LLMProvider 介面，使用 OpenRouter API
OpenRouter 可以訪問多種 LLM 模型（OpenAI、Anthropic、Meta 等）
"""

from minibot.llms.base import LLMProvider, LLMResponse, ChatMessage


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
                "HTTP-Referer": "https://github.com/HsinPu/mini-bot",
                "X-Title": "mini-bot"
            }
        )
    
    async def chat(
        self, 
        messages: list[ChatMessage], 
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> LLMResponse:
        """
        呼叫 OpenRouter Chat Completions API
        """
        # 轉換成 OpenAI 格式
        api_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]
        
        # 呼叫 API
        response = await self.client.chat.completions.create(
            model=model or self.default_model,
            messages=api_messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        return LLMResponse(
            content=response.choices[0].message.content,
            model=response.model
        )
    
    def get_default_model(self) -> str:
        return self.default_model
