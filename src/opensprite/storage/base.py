"""
opensprite/storage/base.py - Storage 介面定義

設計理念：
- Agent 只認得「統一的 Storage 介面」
- 不同存放方式（記憶體、檔案、資料庫、Redis）都實作這個介面
- 以後要換存放方式 Agent 不用改

"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class StoredMessage:
    """
    已儲存的訊息格式
    """
    role: str      # "user" / "assistant" / "tool"
    content: str   # 訊息內容
    timestamp: float  # 時間戳記
    tool_name: str | None = None  # 如果是 tool，記錄用了什麼工具
    is_consolidated: bool = False  # 是否已被 consolidate 過


class StorageProvider(ABC):
    """
    Storage Provider 的抽象基底類別
    
    每種存放方式都應該實作這個類別。
    
    抽象方法：
        - get_messages(): 取得對話歷史
        - add_message(): 加入訊息
        - clear_messages(): 清除歷史
        - get_consolidated_index(): 取得 consolidation 標記
        - set_consolidated_index(): 設定 consolidation 標記
    """
    
    @abstractmethod
    async def get_messages(self, chat_id: str, limit: int | None = None) -> list[StoredMessage]:
        """
        取得對話歷史
        
        參數：
            chat_id: 聊天室 ID
            limit: 最多取幾筆（可選）
        
        回傳：
            list[StoredMessage]: 訊息清單
        """
        pass
    
    @abstractmethod
    async def add_message(self, chat_id: str, message: StoredMessage) -> None:
        """
        加入訊息到歷史
        
        參數：
            chat_id: 聊天室 ID
            message: StoredMessage 訊息
        """
        pass
    
    @abstractmethod
    async def clear_messages(self, chat_id: str) -> None:
        """
        清除指定聊天室的歷史
        
        參數：
            chat_id: 聊天室 ID
        """
        pass

    @abstractmethod
    async def get_consolidated_index(self, chat_id: str) -> int:
        """Get the last consolidated message index for a chat."""
        pass

    @abstractmethod
    async def set_consolidated_index(self, chat_id: str, index: int) -> None:
        """Persist the last consolidated message index for a chat."""
        pass
    
    @abstractmethod
    async def get_all_chats(self) -> list[str]:
        """
        取得所有聊天室 ID
        
        回傳：
            list[str]: 聊天室 ID 清單
        """
        pass
