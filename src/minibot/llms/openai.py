"""
minibot/llms/openai.py - OpenAI LLM 實作

實作 LLMProvider 介面，使用 OpenAI API（或相容 API 如 OpenRouter、vLLM）

"""

from minibot.llms.base import LLMProvider, LLMResponse, ChatMessage


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
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> LLMResponse:
        """
        呼叫 OpenAI Chat Completions API
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
