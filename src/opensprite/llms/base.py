"""
opensprite/llms/base.py - LLM 介面定義

設計理念：
- Agent 只認得「統一的 LLM 介面」
- 不同 LLM 廠商（OpenAI、Anthropic、DeepSeek、本地 vLLM）都實作這個介面
- 以後要換模型只用改設定，不用改 Agent 程式碼

"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

UNCONFIGURED_LLM_MODEL = "unconfigured"
CHAT_ROLE_SYSTEM = "system"
CHAT_ROLE_USER = "user"
CHAT_ROLE_ASSISTANT = "assistant"
CHAT_ROLE_TOOL = "tool"
CHAT_CONTENT_TYPE_TEXT = "text"
CHAT_CONTENT_TYPE_IMAGE_URL = "image_url"


@dataclass
class ToolCall:
    """Tool call request from LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """
    LLM 回覆的統一格式
    """
    content: str  # 回覆的文字內容
    model: str    # 使用的模型名稱
    tool_calls: list[ToolCall] = field(default_factory=list)  # Tool calls (if any)
    usage: dict[str, Any] = field(default_factory=dict)  # Provider token/cost usage if available
    finish_reason: str | None = None
    reasoning_details: list[dict[str, Any]] | None = None


@dataclass
class ChatMessage:
    """
    單筆對話訊息（給 LLM 用）
    
    支援文字和圖片內容。
    圖片格式：base64 編碼的 data URL
    """
    role: str    # CHAT_ROLE_SYSTEM / CHAT_ROLE_USER / CHAT_ROLE_ASSISTANT / CHAT_ROLE_TOOL
    content: str | list[dict] = ""  # 字串或混合內容（文字+圖片）
    tool_call_id: str | None = None  # For tool results
    tool_calls: list[dict] | None = None  # For assistant messages with tool calls
    reasoning_details: list[dict[str, Any]] | None = None  # For providers that require reasoning passback
    
    @staticmethod
    def create_user_message(text: str, images: list[str] | None = None) -> "ChatMessage":
        """
        建立使用者訊息（支援文字+圖片）
        
        參數：
            text: 文字內容
            images: 圖片清單（base64 data URL）
        
        回傳：
            ChatMessage: 使用者訊息
        """
        if images:
            # 混合內容格式（文字 + 圖片）
            content = [{"type": CHAT_CONTENT_TYPE_TEXT, "text": text}]
            for img in images:
                content.append({"type": CHAT_CONTENT_TYPE_IMAGE_URL, "image_url": {"url": img}})
            return ChatMessage(role=CHAT_ROLE_USER, content=content)
        else:
            return ChatMessage(role=CHAT_ROLE_USER, content=text)


@dataclass
class ToolDefinition:
    """Tool definition for LLM."""
    name: str
    description: str
    parameters: dict[str, Any]


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
        reasoning_delta_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
        發送對話請求到 LLM
        
        參數：
            messages: 對話歷史（ChatMessage 清單）
            tools: Tool 定義清單（可選）
            model: 模型名稱（可選，預設用 Provider 的預設模型）
            temperature: 創意程度（None 表示不帶此參數，由 API／模型預設）
            max_tokens: 最大回覆長度（None 表示不帶此參數，由 API／模型預設）
            top_p: nucleus sampling（None 表示由實作決定是否省略）
            frequency_penalty: 重複用詞懲罰，-2.0～2.0（None 表示由實作決定是否省略）
            presence_penalty: 是否鼓勵新主題／少重複已出現概念，-2.0～2.0（None 表示由實作決定是否省略）
            status_callback: 長時間等待或重試時對使用者顯示的短訊息（可選，由實作決定是否呼叫）
            response_delta_callback: 可選的可見文字增量 callback；provider 支援 streaming 時呼叫
            tool_input_delta_callback: 可選的工具參數增量 callback；provider 支援 streaming tool calls 時呼叫
            reasoning_delta_callback: 可選的 reasoning 增量 callback；只應用於 inspector trace，不應發到外部 channel
        
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

    def recover_after_error(self, error: BaseException) -> bool:
        """Best-effort hook for transient provider recovery before one retry."""
        _ = error
        return False

    def context_request_kwargs(self, *, output_token_reserve: int) -> dict[str, Any]:
        """Provider-required request kwargs derived from centralized context config."""
        _ = output_token_reserve
        return {}


def is_unconfigured_llm(provider: Any, model: str | None) -> bool:
    """Return whether an LLM provider/model pair represents the unconfigured fallback."""
    return provider is None or isinstance(provider, UnconfiguredLLM) or str(model or "").strip().lower() == UNCONFIGURED_LLM_MODEL


class UnconfiguredLLM(LLMProvider):
    """Fallback provider used before the user configures an LLM."""

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
        reasoning_delta_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="", model=self.get_default_model())

    def get_default_model(self) -> str:
        return UNCONFIGURED_LLM_MODEL
