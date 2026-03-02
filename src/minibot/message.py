"""
minibot/message.py - 統一訊息格式

設計理念：
- Agent 只認得「統一的訊息格式」
- 不同訊息來源（Telegram、Discord、Console）都轉換成這個格式
- 這樣 Agent 不需要知道各平台的通知差異

"""

from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Any


@dataclass
class UserMessage:
    """
    統一的「使用者訊息」格式
    
    所有頻道（telegram、discord、console）收到使用者訊息後，
    都應該轉換成這個格式傳給 Agent。
    
    屬性：
        text: 訊息文字內容
        sender: 發送者是誰（例如 user_id 或 username）
        chat_id: 聊天室 ID（例如 group 或 channel）
        raw: （可選）原始訊息物件，讓某些工具需要知道來源時使用
    """
    text: str
    sender: str | None = None
    chat_id: str | None = None
    raw: Any = None  # 原始訊息物件，可選


@dataclass
class AssistantMessage:
    """
    統一的「助理回覆」格式
    
    Agent 回覆後，要發送回各頻道時，會轉換成這個格式。
    
    屬性：
        text: 回覆文字
        chat_id: 要發送到哪個聊天室
        raw: （可選）可放入平台特定的回覆物件
    """
    text: str
    chat_id: str | None = None
    raw: Any = None


class MessageAdapter(ABC):
    """
    訊息轉接器（Adapter）的抽象基底類別
    
    每個訊息來源（telegram、discord、console）都應該實作這個類別。
    這樣 Agent 只需要跟這個介面互動，不需要知道各平台的差异。
    
    抽象方法：
        - to_user_message(): 把平台原始訊息轉成 UserMessage
        - from_assistant_message(): 把 Agent 的回覆轉成平台格式並發送
    """
    
    @abstractmethod
    def to_user_message(self, raw_message: Any) -> UserMessage:
        """
        把平台收到的原始訊息轉換成統一的 UserMessage
        
        參數：
            raw_message: 平台原始的訊息物件（例如 telegram 的 Update）
        
        回傳：
            UserMessage: 統一格式的訊息
        """
        pass
    
    @abstractmethod
    async def send(self, message: AssistantMessage) -> None:
        """
        把助理的回覆發送到平台
        
        參數：
            message: AssistantMessage 統一格式的回覆
        """
        pass
