"""
minibot/queue.py - 訊息佇列

設計理念：
- 支援多個對話同時進行
- 每個聊天室（chat_id）有自己的對話歷史
- 用佇列接收訊息，非同步處理

"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Awaitable
from minibot.message import UserMessage, AssistantMessage


@dataclass
class Conversation:
    """
    單一對話的狀態
    
    每個聊天室（chat_id）會有一個獨立的 Conversation 物件，
    包含自己的對話歷史。
    """
    chat_id: str
    messages: list = field(default_factory=list)  # 對話歷史
    pending: asyncio.Event = field(default_factory=asyncio.Event)  # 等待回覆


class MessageQueue:
    """
    訊息佇列管理器
    
    負責：
    - 接收新訊息
    - 把訊息分發到對應的對話
    - 維護每個聊天室的對話歷史
    - 非同步處理多個對話
    """
    
    def __init__(self, agent):
        """
        初始化
        
        參數：
            agent: AgentLoop 實例
        """
        self.agent = agent
        self.conversations: dict[str, Conversation] = {}  # chat_id -> Conversation
        self.queue: asyncio.Queue = asyncio.Queue()
        self.running = False
    
    def get_or_create_conversation(self, chat_id: str) -> Conversation:
        """
        取得或建立對話
        
        參數：
            chat_id: 聊天室 ID
        
        回傳：
            Conversation 物件
        """
        if chat_id not in self.conversations:
            self.conversations[chat_id] = Conversation(chat_id=chat_id)
        return self.conversations[chat_id]
    
    async def enqueue(self, user_message: UserMessage) -> None:
        """
        把訊息加入佇列
        
        參數：
            user_message: 統一格式的訊息
        """
        await self.queue.put(user_message)
    
    async def process_queue(self) -> None:
        """
        處理佇列中的訊息（非同步迴圈）
        
        這個函式會一直執行，直到 running = False
        """
        self.running = True
        
        while self.running:
            try:
                # 等待訊息（超時 1 秒，檢查是否要停止）
                try:
                    user_message = await asyncio.wait_for(
                        self.queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # 取得或建立對話
                chat_id = user_message.chat_id or "console"
                conversation = self.get_or_create_conversation(chat_id)
                
                # 把訊息傳給 Agent 處理
                response = await self.agent.process(user_message)
                
                # 發送回覆（如果 Adapter 有實作 send）
                if hasattr(self, 'on_response'):
                    await self.on_response(response)
                
            except Exception as e:
                print(f"處理訊息時發生錯誤: {e}")
    
    async def stop(self) -> None:
        """停止處理"""
        self.running = False
    
    def reset_conversation(self, chat_id: str) -> None:
        """
        重置特定對話的歷史
        
        參數：
            chat_id: 聊天室 ID
        """
        if chat_id in self.conversations:
            self.conversations[chat_id].messages.clear()


# ============================================
# 使用範例（Console 版本）
# ============================================

"""
# 建立 Queue 版本

import asyncio
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.queue import MessageQueue
from minibot.message import UserMessage

async def main():
    # 1. 建立 LLM
    llm = OpenAILLM(
        api_key="your-key",
        default_model="gpt-4o-mini"
    )
    
    # 2. 建立 Agent
    config = AgentConfig(system_prompt="你是個簡潔的助理。")
    agent = AgentLoop(config, llm)
    
    # 3. 建立 Queue
    mq = MessageQueue(agent)
    
    # 4. 定義收到回覆時要做什麼（這裡直接印出）
    async def on_response(response):
        print(f"\n🤖: {response.text}")
    
    mq.on_response = on_response
    
    # 5. 啟動處理迴圈（在背景執行）
    processor = asyncio.create_task(mq.process_queue())
    
    # 6. 主執行緒處理輸入
    print("輸入訊息（格式：@chat_id 訊息，或直接輸入）")
    print("範例：@123 你好  → 發送到 chat_id=123")
    print("      你好       → 發送到預設對話")
    print("      /reset    → 重置對話")
    print("      /exit     → 離開")
    
    while True:
        try:
            line = input("\n你: ").strip()
        except EOFError:
            break
        
        if not line:
            continue
        
        if line.lower() == "/exit":
            await mq.stop()
            await processor
            break
        
        if line.lower() == "/reset":
            mq.reset_conversation("default")
            print("✅ 歷史已清除")
            continue
        
        # 解析 chat_id
        if line.startswith("@"):
            # 格式：@chat_id 訊息
            parts = line[1:].split(" ", 1)
            chat_id = parts[0]
            text = parts[1] if len(parts) > 1 else ""
        else:
            # 預設 chat_id
            chat_id = "default"
            text = line
        
        # 加入佇列
        user_msg = UserMessage(text=text, chat_id=chat_id)
        await mq.enqueue(user_msg)

asyncio.run(main())
"""