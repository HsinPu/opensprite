"""
minibot/agent.py - Agent Loop

核心流程：
1. 接收使用者訊息
2. 用 ContextBuilder 組 prompt
3. 叫 LLM
4. 回覆給使用者

設計重點：
- 只認得「統一的訊息格式」：UserMessage、AssistantMessage
- 只認得「統一的 LLM Provider 介面」
- 只認得「統一的 Storage 介面」
- 只認得「統一的 ContextBuilder 介面」
- 具体的訊息來源（telegram、discord）由外部 Adapter 轉換
- 具体的 LLM 廠商由 Provider 實作
- 具体的存放方式由 Storage 實作
- 具体的 prompt 組裝由 ContextBuilder 實作
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from minibot.bus.message import UserMessage, AssistantMessage
from minibot.llms import LLMProvider, ChatMessage
from minibot.storage import StorageProvider, StoredMessage
from minibot.utils.log import logger


class ContextBuilderProtocol(Protocol):
    """Context builder protocol for type hints."""

    def build_system_prompt(self) -> str: ...

    def build_messages(
        self,
        history: list[dict],
        current_message: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict]: ...

    def add_tool_result(
        self,
        messages: list[dict],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict]: ...

    def add_assistant_message(
        self,
        messages: list[dict],
        content: str | None,
        tool_calls: list[dict] | None = None,
    ) -> list[dict]: ...


@dataclass
class AgentConfig:
    """
    Agent 的設定參數集合。
    """
    model: str | None = None
    system_prompt: str = ""
    max_tokens: int = 2048
    temperature: float = 0.7


class AgentLoop:
    """
    Agent Loop

    這是整個 Agent 的核心類別，負責：
    - 維護對話歷史（透過 Storage）
    - 組 prompt（透過 ContextBuilder）
    - 呼叫 LLM（透過 Provider 介面）
    - 處理使用者輸入並回傳（process）

    設計重點：
    - 只認得「統一的訊息格式」：UserMessage、AssistantMessage
    - 只認得「統一的 LLM Provider 介面」
    - 只認得「統一的 Storage 介面」
    - 只認得「統一的 ContextBuilder 介面」
    - 具体的 LLM 廠商由外部注入
    - 具体的存放方式由外部注入
    - 具体的 prompt 組裝由外部注入
    """

    def __init__(
        self,
        config: AgentConfig,
        provider: LLMProvider,
        storage: StorageProvider | None = None,
        context_builder: ContextBuilderProtocol | None = None,
    ):
        """
        AgentLoop 的建構函式。

        參數：
            config: AgentConfig 物件，包含所有設定
            provider: LLMProvider 物件（OpenAI/Anthropic/其他）
            storage: StorageProvider 物件（記憶體/檔案/資料庫），可選
            context_builder: ContextBuilder 物件，可選

        初始化時會：
            1. 儲存設定到 self.config
            2. 儲存 LLM Provider
            3. 儲存 Storage Provider（如果沒給，用預設的記憶體）
            4. 儲存 ContextBuilder（如果沒給，用簡單的 fallback）
        """
        self.config = config
        self.provider = provider

        # 如果沒給 storage，用記憶體 storage
        if storage is None:
            from minibot.storage import MemoryStorage
            storage = MemoryStorage()
        self.storage = storage

        # 如果沒給 context_builder，自動偵測 workspace 建立
        self._context_builder = context_builder
        if context_builder is None:
            try:
                from minibot.context.workspace import get_workspace_path, sync_templates
                from minibot.context import FileContextBuilder
                workspace = get_workspace_path()
                sync_templates(workspace)  # 同步範本（如果不存在會创建）
                context_builder = FileContextBuilder(workspace)
                self._context_builder = context_builder
                self._use_simple_context = False
            except Exception:
                self._use_simple_context = True
        else:
            self._use_simple_context = False

        # 目前處理的 chat_id
        self._current_chat_id: str | None = None

    def _build_simple_system_prompt(self) -> str:
        """Simple fallback system prompt if no context_builder provided."""
        return "You are a helpful AI assistant."

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

    async def call_llm(
        self,
        chat_id: str,
        channel: str | None = None,
    ) -> str:
        """
        呼叫 LLM（大型語言模型）並取得回應。

        參數：
            chat_id: 聊天室 ID（用來取歷史）
            channel: 頻道名稱（可選，用於 context）

        回傳：
            str: LLM 回覆的內容文字
        """
        # 從 storage 載入歷史
        logger.info(f"[{chat_id}] 載入歷史訊息...")
        history_messages = await self._load_history(chat_id)

        # 轉換成 dict 格式（給 context builder 用）
        history_dicts = [
            {"role": m.role, "content": m.content}
            for m in history_messages
        ]

        # 用 context builder 組 messages
        if self._use_simple_context:
            # Fallback: 簡單的 system prompt
            full_messages = [
                {"role": "system", "content": self._build_simple_system_prompt()},
                *history_dicts,
            ]
        else:
            # 使用 context builder
            full_messages = self._context_builder.build_messages(
                history=history_dicts,
                current_message="",  # 這裡不放 content，我們會用 user message
                channel=channel,
                chat_id=chat_id,
            )

        # 轉換成 ChatMessage 格式
        chat_messages = [
            ChatMessage(role=m["role"], content=m.get("content", ""))
            for m in full_messages
        ]

        # 呼叫 Provider
        logger.info(f"[{chat_id}] 呼叫 LLM...")
        response = await self.provider.chat(
            messages=chat_messages,
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens
        )
        logger.info(f"[{chat_id}] 收到 LLM 回覆: {response.content[:50]}...")
        
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
        channel = getattr(user_message, 'channel', None)
        
        logger.info(f"[{chat_id}] 收到訊息: {user_message.text[:50]}...")

        # 1. 把使用者訊息存入 storage
        await self._save_message(chat_id, "user", user_message.text)

        # 2. 呼叫 LLM（傳入 channel）
        logger.info(f"[{chat_id}] 處理中...")
        response = await self.call_llm(chat_id, channel=channel)
        
        logger.info(f"[{chat_id}] 回覆: {response[:50]}...")

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
使用 ContextBuilder（推薦）：

```python
from pathlib import Path
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage
from minibot.context import FileContextBuilder
from minibot.context.workspace import get_workspace_path

# 1. 建立 ContextBuilder（從 workspace 讀檔案）
workspace = get_workspace_path()
context_builder = FileContextBuilder(workspace)

# 2. 建立 Storage
storage = MemoryStorage()

# 3. 建立 LLM
llm = OpenAILLM(api_key="your-key")

# 4. 建立 Agent（傳入 context_builder）
config = AgentConfig()
agent = AgentLoop(config, llm, storage, context_builder)

# 5. 使用
user_msg = UserMessage(text="你好", chat_id="123", channel="telegram")
response = await agent.process(user_msg)
```

不使用 ContextBuilder（向後相容）：

```python
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage

storage = MemoryStorage()
llm = OpenAILLM(api_key="your-key")
config = AgentConfig()

# 不傳 context_builder 也行，會用簡單的 system prompt
agent = AgentLoop(config, llm, storage)

user_msg = UserMessage(text="你好", chat_id="123")
response = await agent.process(user_msg)
```
"""
