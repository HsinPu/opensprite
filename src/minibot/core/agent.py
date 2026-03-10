"""
minibot/agent.py - Agent Loop

核心流程：
1. 接收使用者訊息
2. 用 ContextBuilder 組 prompt
3. 叫 LLM
4. 執行 tool calls（如果 LLM 請求）
5. 回覆給使用者

設計重點：
- 只認得「統一的訊息格式」：UserMessage、AssistantMessage
- 只認得「統一的 LLM Provider 介面」
- 只認得「統一的 Storage 介面」
- 只認得「統一的 ContextBuilder 介面」
- 具体的訊息來源（telegram、discord）由外部 Adapter 轉換
- 具体的 LLM 廠商由 Provider 實作
- 具体的存放方式由 Storage 實作
- 具体的 prompt 組裝由 ContextBuilder 實作
- Tool 由 ToolRegistry 管理
"""

import json
import asyncio
from pathlib import Path
from typing import Any

from minibot.bus.message import UserMessage, AssistantMessage
from minibot.llms import LLMProvider, ChatMessage
from minibot.storage import StorageProvider, StoredMessage
from minibot.context.builder import ContextBuilder
from minibot.memory import MemoryStore, consolidate
from minibot.tools import ToolRegistry, ReadFileTool, WriteFileTool, ListDirTool, EditFileTool, ExecTool, WebSearchTool, WebFetchTool
from minibot.utils.log import logger
from minibot.config import AgentConfig, MemoryConfig, ToolsConfig


class AgentLoop:
    """
    Agent Loop

    這是整個 Agent 的核心類別，負責：
    - 維護對話歷史（透過 Storage）
    - 組 prompt（透過 ContextBuilder）
    - 呼叫 LLM（透過 Provider 介面）
    - 執行 Tool Calls
    - 處理使用者輸入並回傳（process）

    設計重點：
    - 只認得「統一的訊息格式」：UserMessage、AssistantMessage
    - 只認得「統一的 LLM Provider 介面」
    - 只認得「統一的 Storage 介面」
    - 只認得「統一的 ContextBuilder 介面」
    - 具体的 LLM 廠商由外部注入
    - 具体的存放方式由外部注入
    - 具体的 prompt 組裝由外部注入
    - Tools 由 ToolRegistry 管理
    """

    MAX_TOOL_ITERATIONS = 10

    def __init__(
        self,
        config: AgentConfig,
        provider: LLMProvider,
        storage: StorageProvider | None = None,
        context_builder: ContextBuilder | None = None,
        tools: ToolRegistry | None = None,
        memory_config: MemoryConfig | None = None,
        tools_config: ToolsConfig | None = None,
        brave_api_key: str = "",
    ):
        ...
        self.memory_config = memory_config or MemoryConfig()
        self.tools_config = tools_config or ToolsConfig()
        self.brave_api_key = brave_api_key
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
            except Exception as e:
                raise RuntimeError(f"無法建立 ContextBuilder: {e}")

        # Tools
        self.tools = tools or ToolRegistry()
        if not self.tools.tool_names:
            self._register_default_tools()

        # Memory store (long-term memory)
        workspace = getattr(self._context_builder, 'workspace', Path.cwd())
        self.memory = MemoryStore(workspace)
        self._last_consolidated: dict[str, int] = {}  # Per-chat tracking

        # Register save_memory tool
        self._register_memory_tool()

        # 目前處理的 chat_id
        self._current_chat_id: str | None = None

    def _register_memory_tool(self) -> None:
        """Register the save_memory tool."""
        # Dynamic tool for saving memory
        from minibot.tools.base import Tool
        
        class SaveMemoryTool(Tool):
            name = "save_memory"
            description = "Save important information to long-term memory. Include all existing facts plus new ones."
            parameters = {
                "type": "object",
                "properties": {
                    "memory_update": {"type": "string", "description": "Updated memory as markdown"}
                },
                "required": ["memory_update"]
            }
            
            def __init__(self, memory_store: MemoryStore, get_chat_id: callable):
                self.memory_store = memory_store
                self.get_chat_id = get_chat_id
            
            async def execute(self, memory_update: str, **kwargs: Any) -> str:
                chat_id = self.get_chat_id()
                current = self.memory_store.read(chat_id)
                if memory_update != current:
                    self.memory_store.write(chat_id, memory_update)
                    return f"Memory saved ({len(memory_update)} chars)"
                return "Memory unchanged"
        
        # Pass a lambda that returns current chat_id
        self.tools.register(SaveMemoryTool(self.memory, lambda: self._current_chat_id))

    def _register_default_tools(self) -> None:
        """註冊預設的工具"""
        workspace = getattr(self._context_builder, 'workspace', Path.cwd())
        
        # 檔案工具
        self.tools.register(ReadFileTool(workspace=workspace))
        self.tools.register(WriteFileTool(workspace=workspace))
        self.tools.register(EditFileTool(workspace=workspace))
        self.tools.register(ListDirTool(workspace=workspace))
        
        # 執行命令
        self.tools.register(ExecTool(workspace=workspace))
        
        # 網路工具
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        logger.info(f"已註冊工具: {self.tools.tool_names}")

    async def _load_history(self, chat_id: str) -> list[ChatMessage]:
        """
        從 Storage 載入對話歷史

        參數：
            chat_id: 聊天室 ID

        回傳：
            list[ChatMessage]: 給 LLM 用的訊息格式
        """
        # 從 storage 取訊息（使用 max_history 限制數量）
        stored_messages = await self.storage.get_messages(
            chat_id, 
            limit=self.memory_config.max_history
        )

        # 轉換成 ChatMessage 格式
        chat_messages = []
        for m in stored_messages:
            if isinstance(m, dict):
                chat_messages.append(ChatMessage(role=m.get("role", "?"), content=m.get("content", "")))
            else:
                chat_messages.append(ChatMessage(role=m.role, content=m.content))
        
        return chat_messages

    async def _save_message(self, chat_id: str, role: str, content: str, tool_name: str | None = None) -> None:
        """
        把訊息存到 Storage

        參數：
            chat_id: 聊天室 ID
            role: user / assistant / tool
            content: 訊息內容
            tool_name: 如果是 tool，記錄用了什麼工具
        """
        await self.storage.add_message(
            chat_id,
            StoredMessage(role=role, content=content, timestamp=0, tool_name=tool_name)
        )

    async def call_llm(
        self,
        chat_id: str,
        channel: str | None = None,
        allow_tools: bool = True,
    ) -> str:
        """
        呼叫 LLM（大型語言模型）並取得回應。

        參數：
            chat_id: 聊天室 ID（用來取歷史）
            channel: 頻道名稱（可選，用於 context）
            allow_tools: 是否允許使用工具

        回傳：
            str: LLM 回覆的內容文字
        """
        # 從 storage 載入歷史
        logger.info(f"[{chat_id}] 載入歷史訊息...")
        history_messages = await self._load_history(chat_id)

        # 轉換成 dict 格式（給 context builder 用）
        history_dicts = []
        for m in history_messages:
            if isinstance(m, dict):
                history_dicts.append({"role": m.get("role", "?"), "content": m.get("content", "")})
            else:
                history_dicts.append({"role": m.role, "content": m.content})

        # 用 context builder 組 messages
        logger.info(f"[{chat_id}] 使用 context builder")
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

        # Log 完整 prompt
        try:
            # 找出 system prompt
            system_msg = next((m for m in full_messages if m.get("role") == "system"), None)
            if system_msg:
                logger.info(f"[{chat_id}] === SYSTEM PROMPT ===")
                logger.info(f"[{chat_id}] {system_msg.get('content', '')}")
                logger.info(f"[{chat_id}] ====================")
            
            # 其他訊息（完整印出）
            logger.info(f"[{chat_id}] === MESSAGES ===")
            for msg in full_messages:
                role = msg.get('role', 'unknown')
                if role == "system":
                    continue
                content = msg.get('content', '')  # 完整印出
                logger.info(f"[{chat_id}] {role}: {content}")
            logger.info(f"[{chat_id}] ==============")
        except Exception as e:
            logger.error(f"[{chat_id}] Log prompt error: {e}")

        # 準備 tools
        tools = None
        if allow_tools and self.tools.tool_names:
            tools = self.tools.get_definitions()
            logger.info(f"[{chat_id}] 使用工具: {self.tools.tool_names}")

        # 追蹤 tool 執行結果
        tool_results_history = []

        # 迴圈：執行 tool calls 直到沒有為止
        for iteration in range(self.tools_config.max_tool_iterations):
            # 呼叫 Provider
            logger.info(f"[{chat_id}] 呼叫 LLM... (iteration {iteration + 1})")
            response = await self.provider.chat(
                messages=chat_messages,
                tools=tools,
            )

            # 檢查是否有 tool calls
            if response.tool_calls:
                logger.info(f"[{chat_id}] LLM 請求執行 {len(response.tool_calls)} 個工具")
                
                # 記錄 assistant 訊息（包含 tool calls）
                tool_calls_api = []
                for tc in response.tool_calls:
                    tool_calls_api.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    })
                
                chat_messages.append(ChatMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=tool_calls_api
                ))
                
                # 執行每個 tool call
                for tc in response.tool_calls:
                    tool_name = tc.name
                    tool_args = tc.arguments
                    
                    logger.info(f"[{chat_id}] 執行工具: {tool_name}({tool_args})")
                    
                    result = await self.tools.execute(tool_name, tool_args)
                    logger.info(f"[{chat_id}] 工具結果: {result[:200]}...")
                    
                    # 記錄 tool 結果
                    tool_results_history.append(f"{tool_name}: {result[:200]}")
                    
                    # 將 tool result 加入訊息
                    chat_messages.append(ChatMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id
                    ))
                    
                    # 存到歷史訊息
                    await self._save_message(chat_id, "tool", result, tool_name=tool_name)
                
                # 繼續迴圈，讓 LLM 根據 tool results 生成回覆
                continue
            
            # 沒有 tool calls，回覆完成
            logger.info(f"[{chat_id}] 收到 LLM 回覆: {response.content[:50]}...")
            return response.content

        # 超過最大迭代次數
        logger.warning(f"[{chat_id}] 超過最大工具迭代次數 ({self.tools_config.max_tool_iterations})")
        
        # 回報問題並附上工具執行歷史
        history_msg = ""
        if tool_results_history:
            history_msg = f"\n\n我嘗試了以下工具但未能完成任務：\n" + "\n".join(f"- {r}" for r in tool_results_history[-5:])
        
        return f"我嘗試完成你的請求，但超過了最大迭代次數（{self.tools_config.max_tool_iterations}次）。請將任務拆分為較小的步驟。{history_msg}"

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

        # Set current chat_id for save_memory tool
        self._current_chat_id = chat_id

        # 1. 把使用者訊息存入 storage
        await self._save_message(chat_id, "user", user_message.text)

        # 2. 呼叫 LLM（傳入 channel）
        logger.info(f"[{chat_id}] 處理中...")
        response = await self.call_llm(chat_id, channel=channel)
        
        logger.info(f"[{chat_id}] 回覆: {response[:50]}...")

        # 3. 把 AI 回覆存入 storage
        await self._save_message(chat_id, "assistant", response)

        # 4. 檢查是否需要 consolidation
        await self._maybe_consolidate_memory(chat_id)

        # 5. 回傳
        return AssistantMessage(
            text=response,
            chat_id=chat_id
        )

    async def _maybe_consolidate_memory(self, chat_id: str) -> None:
        """Check if memory consolidation is needed and run it."""
        # Get total message count
        messages = await self.storage.get_messages(chat_id, limit=1000)
        message_count = len(messages)
        
        # Get last consolidated for this chat
        last_consolidated = self._last_consolidated.get(chat_id, 0)
        
        # Check if we should consolidate
        unconsolidated = message_count - last_consolidated
        if unconsolidated >= self.memory_config.threshold:
            logger.info(f"[{chat_id}] Consolidating memory ({unconsolidated} messages)")
            try:
                # Get messages to consolidate
                old_messages = messages[last_consolidated:]
                # Convert to dicts (handle both object and dict formats)
                msg_dicts = []
                for m in old_messages:
                    if isinstance(m, dict):
                        msg_dicts.append({"role": m.get("role", "?"), "content": m.get("content", "")})
                    else:
                        msg_dicts.append({"role": m.role, "content": m.content})
                
                success = await consolidate(
                    memory_store=self.memory,
                    chat_id=chat_id,
                    messages=msg_dicts,
                    provider=self.provider,
                    model=self.provider.get_default_model(),
                )
                if success:
                    self._last_consolidated[chat_id] = message_count
                    logger.info(f"[{chat_id}] Memory consolidated")
            except Exception as e:
                logger.error(f"[{chat_id}] Memory consolidation failed: {e}")

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
