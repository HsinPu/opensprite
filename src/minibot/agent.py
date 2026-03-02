"""
minibot/agent.py - 最簡單的 Agent Loop（參考 nanobot 架構）

核心流程：
1. 接收使用者訊息
2. 把訊息丟給 LLM
3. LLM 回覆後回傳給使用者

設計重點：
- 只認得「統一的訊息格式」：UserMessage、AssistantMessage
- 只認得「統一的 LLM Provider 介面」
- 只認得「統一的 Storage 介面」
- 具体的訊息來源（telegram、discord）由外部 Adapter 轉換
- 具体的 LLM 廠商由 Provider 實作
- 具体的存放方式由 Storage 實作

"""

from dataclasses import dataclass
from minibot.message import UserMessage, AssistantMessage
from minibot.llms import LLMProvider, ChatMessage
from minibot.storage import StorageProvider, StoredMessage


@dataclass
class AgentConfig:
    """
    Agent 的設定參數集合，相當於「設定檔」。
    """
    model: str | None = None
    system_prompt: str = "你是個有用的助理。"
    max_tokens: int = 2048
    temperature: float = 0.7


class AgentLoop:
    """
    極簡 Agent Loop
    
    這是整個 Agent 的核心類別，負責：
    - 維護對話歷史（透過 Storage）
    - 呼叫 LLM（透過 Provider 介面）
    - 處理使用者輸入並回傳（process）
    
    設計重點：
    - 只認得「統一的訊息格式」：UserMessage、AssistantMessage
    - 只認得「統一的 LLM Provider 介面」
    - 只認得「統一的 Storage 介面」
    - 具体的 LLM 廠商由外部注入
    - 具体的存放方式由外部注入
    """

    def __init__(
        self, 
        config: AgentConfig, 
        provider: LLMProvider,
        storage: StorageProvider | None = None
    ):
        """
        AgentLoop 的建構函式。
        
        參數：
            config: AgentConfig 物件，包含所有設定
            provider: LLMProvider 物件（OpenAI/Anthropic/其他）
            storage: StorageProvider 物件（記憶體/檔案/資料庫），可選
        
        初始化時會：
            1. 儲存設定到 self.config
            2. 儲存 LLM Provider
            3. 儲存 Storage Provider（如果沒給，用預設的記憶體）
        """
        self.config = config
        self.provider = provider
        
        # 如果沒給 storage，用記憶體 storage
        if storage is None:
            from minibot.storage import MemoryStorage
            storage = MemoryStorage()
        self.storage = storage
        
        # 目前處理的 chat_id
        self._current_chat_id: str | None = None

    async def _load_history(self, chat_id: str) -> list[ChatMessage]:
        """
        從 Storage 載入對話歷史
        
        參數：
            chat_id: 聊天室 ID
        
        回傳：
            list[ChatMessage]: 給 LLM 用的訊息格式
        """
        # 從 storage 取訊息
        stored_messages = await self.storage.get_messages(chat_id)
        
        # 轉換成 ChatMessage 格式
        return [
            ChatMessage(role=m.role, content=m.content)
            for m in stored_messages
        ]

    async def _save_message(self, chat_id: str, role: str, content: str) -> None:
        """
        把訊息存到 Storage
        
        參數：
            chat_id: 聊天室 ID
            role: user / assistant
            content: 訊息內容
        """
        await self.storage.add_message(
            chat_id,
            StoredMessage(role=role, content=content, timestamp=0)
        )

    async def call_llm(self, chat_id: str) -> str:
        """
        呼叫 LLM（大型語言模型）並取得回應。
        
        參數：
            chat_id: 聊天室 ID（用來取歷史）
        
        回傳：
            str: LLM 回覆的內容文字
        """
        # 從 storage 載入歷史
        messages = await self._load_history(chat_id)
        
        # 加入 system prompt
        full_messages = [
            ChatMessage(role="system", content=self.config.system_prompt)
        ] + messages
        
        # 呼叫 Provider
        response = await self.provider.chat(
            messages=full_messages,
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens
        )
        
        return response.content

    async def process(self, user_message: UserMessage) -> AssistantMessage:
        """
        處理使用者訊息的主要入口函式。
        
        參數：
            user_message: UserMessage 統一格式的訊息
        
        回傳：
            AssistantMessage: 統一格式的回覆
        """
        chat_id = user_message.chat_id or "default"
        
        # 1. 把使用者訊息存入 storage
        await self._save_message(chat_id, "user", user_message.text)

        # 2. 呼叫 LLM
        response = await self.call_llm(chat_id)

        # 3. 把 AI 回覆存入 storage
        await self._save_message(chat_id, "assistant", response)

        # 4. 回傳
        return AssistantMessage(
            text=response,
            chat_id=chat_id
        )

    async def reset_history(self, chat_id: str | None = None) -> None:
        """
        清除對話歷史。
        
        參數：
            chat_id: 聊天室 ID，如果沒給就清除所有
        """
        if chat_id:
            await self.storage.clear_messages(chat_id)
        else:
            # 清除所有聊天室
            all_chats = await self.storage.get_all_chats()
            for c in all_chats:
                await self.storage.clear_messages(c)


# ============================================
# 使用範例
# ============================================

"""
建立 Agent 時，可以選擇不同的 Storage：

```python
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage  # 或未來的 FileStorage、SQLiteStorage

# 1. 建立 Storage（可替換）
storage = MemoryStorage()
# storage = FileStorage()  # 未來
# storage = SQLiteStorage()  # 未來

# 2. 建立 LLM
llm = OpenAILLM(api_key="your-key")

# 3. 建立 Agent（傳入 storage）
config = AgentConfig(system_prompt="你是個助理。")
agent = AgentLoop(config, llm, storage)

# 4. 使用（不變）
user_msg = UserMessage(text="你好", chat_id="123")
response = await agent.process(user_msg)
```

之後要換 Storage（例：檔案）：

```python
from minibot.storage import FileStorage

storage = FileStorage(base_path="./data")
agent = AgentLoop(config, llm, storage)  # Agent 程式碼不用改！
```
"""
