"""
minibot/llms/base.py - LLM 介面定義

設計理念：
- Agent 只認得「統一的 LLM 介面」
- 不同 LLM 廠商（OpenAI、Anthropic、DeepSeek、本地 vLLM）都實作這個介面
- 以後要換模型只用改設定，不用改 Agent 程式碼

"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """
    LLM 回覆的統一格式
    """
    content: str  # 回覆的文字內容
    model: str    # 使用的模型名稱


@dataclass
class ChatMessage:
    """
    單筆對話訊息（給 LLM 用）
    """
    role: str    # "system", "user", "assistant"
    content: str # 訊息內容


class LLMProvider(ABC):
    """
    LLM Provider 的抽象基底類別
    
    每個 LLM 廠商（OpenAI、Anthropic、DeepSeek、本地 vLLM 等）
    都應該實作這個類別。
    
    抽象方法：
        - chat(): 發送對話請求取得回覆
        - get_default_model(): 取得預設模型名稱
    """
    
    @abstractmethod
    async def chat(
        self, 
        messages: list[ChatMessage], 
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> LLMResponse:
        """
        發送對話請求到 LLM
        
        參數：
            messages: 對話歷史（ChatMessage 清單）
            model: 模型名稱（可選，預設用 Provider 的預設模型）
            temperature: 創意程度
            max_tokens: 最大回覆長度
        
        回傳：
            LLMResponse: 包含回覆內容和使用的模型
        """
        pass
    
    @abstractmethod
    def get_default_model(self) -> str:
        """
        取得此 Provider 的預設模型名稱
        
        回傳：
            str: 模型名稱
        """
        pass
