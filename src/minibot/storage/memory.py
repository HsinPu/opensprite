"""
minibot/storage/memory.py - 記憶體 Storage 實作

把對話歷史存放在記憶體中（current implementation）

"""

import time
from collections import defaultdict
from minibot.storage.base import StorageProvider, StoredMessage


class MemoryStorage(StorageProvider):
    """
    記憶體 Storage 實作
    
    把對話歷史存在 dict 裡，重啟後會消失。
    適合開發測試用。
    """
    
    def __init__(self):
        """初始化"""
        self._messages: dict[str, list[StoredMessage]] = defaultdict(list)
        self._consolidated_index: dict[str, int] = {}  # Per-chat consolidation tracking
    
    async def get_messages(self, chat_id: str, limit: int | None = None) -> list[StoredMessage]:
        """
        取得對話歷史
        """
        messages = self._messages.get(chat_id, [])
        if limit:
            return messages[-limit:]
        return messages
    
    async def add_message(self, chat_id: str, message: StoredMessage) -> None:
        """
        加入訊息
        """
        # 設定時間戳記（如果沒有的話）
        if message.timestamp == 0:
            message.timestamp = time.time()
        
        self._messages[chat_id].append(message)
    
    async def clear_messages(self, chat_id: str) -> None:
        """
        清除歷史
        """
        if chat_id in self._messages:
            self._messages[chat_id].clear()
        self._consolidated_index.pop(chat_id, None)
    
    async def get_consolidated_index(self, chat_id: str) -> int:
        """取得 consolidation 標記"""
        return self._consolidated_index.get(chat_id, 0)
    
    async def set_consolidated_index(self, chat_id: str, index: int) -> None:
        """設定 consolidation 標記"""
        self._consolidated_index[chat_id] = index
    
    async def get_all_chats(self) -> list[str]:
        """
        取得所有聊天室
        """
        return list(self._messages.keys())


# ============================================
# 之後可擴充：File Storage
# ============================================

"""
未來要加入檔案儲存時：

class FileStorage(StorageProvider):
    async def get_messages(self, chat_id, limit):
        # 從檔案讀取
        pass
    
    async def add_message(self, chat_id, message):
        # 寫入檔案
        pass
    
    async def clear_messages(self, chat_id):
        # 刪除檔案
        pass

"""
