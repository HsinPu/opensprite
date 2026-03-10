"""
minibot/message_queue.py - 訊息排程中心

角色：訊息調度中心
- 接收外面傳來的訊息，排隊交給 Agent 處理
- 將 Agent 的回覆排隊發送出去
- 支援多個對話同時並行處理

設計理念：
- 支援多個對話同時進行
- inbound / outbound 分離（解耦）
- 對話歷史由 Agent + Storage 管理
- 用 MessageBus 接收/發送訊息

流程：
  外部 → enqueue() → inbound Queue → Agent → outbound Queue → callback → 外部

"""

import asyncio
from dataclasses import dataclass, field
from minibot.bus import MessageBus, InboundMessage, OutboundMessage
from minibot.bus.message import UserMessage, AssistantMessage
from minibot.utils.log import logger


@dataclass
class Conversation:
    """
    單一對話的狀態
    
    這裡只追蹤「是否正在處理」，
    對話歷史由 Agent + Storage 管理。
    """
    chat_id: str
    pending: asyncio.Event = field(default_factory=asyncio.Event)  # 等待回覆


class MessageQueue:
    """
    訊息佇列管理器（Bus 版本）
    
    負責：
    - inbound: 接收新訊息
    - outbound: 發送回覆
    - **非同步並行處理多個對話**（每個訊息spawn成獨立task）
    - 對話歷史由 Agent + Storage 管理
    
    架構：
    ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
    │   inbound   │ ──→  │  AgentLoop   │ ──→  │   outbound   │
    │    Queue    │      │  (處理訊息)   │      │    Queue     │
    └──────────────┘      └──────────────┘      └──────────────┘
                                                            │
                                                            ▼
                                                    on_response callback
    """
    
    def __init__(self, agent, bus: MessageBus | None = None):
        """
        初始化
        
        參數：
            agent: AgentLoop 實例
            bus: MessageBus 實例（可選，預設新建）
        """
        self.agent = agent
        self.bus = bus or MessageBus()
        self.conversations: dict[str, Conversation] = {}  # chat_id -> Conversation
        self.running = False
        # 追蹤所有 active tasks: chat_id -> list of tasks
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        # Outbound 消費者任務
        self._outbound_task: asyncio.Task | None = None
    
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
        把訊息加入 inbound queue
        
        參數：
            user_message: 統一格式的訊息
        """
        # 轉換成 InboundMessage
        inbound = InboundMessage(
            channel=user_message.raw.get("channel") if user_message.raw else "unknown",
            sender_id=user_message.sender or "unknown",
            chat_id=user_message.chat_id or "default",
            content=user_message.text,
            metadata={"raw": user_message.raw} if user_message.raw else {}
        )
        await self.bus.publish_inbound(inbound)
    
    async def enqueue_raw(
        self,
        content: str,
        chat_id: str = "default",
        channel: str = "cli",
        sender_id: str = "user",
        metadata: dict | None = None
    ) -> None:
        """
        直接發送原始訊息到 inbound queue（不需 UserMessage 格式）
        
        參數：
            content: 訊息內容
            chat_id: 聊天室 ID
            channel: 頻道名稱
            sender_id: 發送者 ID
            metadata: 額外資料
        """
        inbound = InboundMessage(
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            metadata=metadata or {}
        )
        await self.bus.publish_inbound(inbound)
    
    async def _process_message(self, inbound: InboundMessage) -> None:
        """
        處理單一訊息（會spawn成独立task）
        
        參數：
            inbound: InboundMessage
        """
        chat_id = inbound.chat_id
        
        try:
            # 取得或建立對話
            conversation = self.get_or_create_conversation(chat_id)
            
            # 轉換成 UserMessage 給 Agent
            user_message = UserMessage(
                text=inbound.content,
                chat_id=inbound.chat_id,
                sender=inbound.sender_id,  # UserMessage 用 sender 不是 sender_id
                raw=inbound.metadata  # 把 metadata 放 raw 裡
            )
            
            # 把訊息傳給 Agent 處理
            response = await self.agent.process(user_message)
            
            # 放到 outbound queue（而不是直接發送）
            outbound = OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=response.text,
                metadata={"sender_id": inbound.sender_id}
            )
            await self.bus.publish_outbound(outbound)
                
        except asyncio.CancelledError:
            # Task 被取消時優雅退出
            pass
        except Exception as e:
            logger.error(f"[{chat_id}] 處理訊息時發生錯誤: {e}")
            # 發送錯誤訊息到 outbound
            outbound = OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=f"抱歉，處理您的訊息時發生錯誤: {str(e)[:100]}"
            )
            await self.bus.publish_outbound(outbound)
            if hasattr(self, 'on_error'):
                await self.on_error(chat_id, str(e))
    
    async def _consume_outbound(self) -> None:
        """
        消費 outbound queue的任務（獨立運作）
        不斷從 outbound 取訊息並呼叫 on_response
        """
        while self.running:
            try:
                outbound = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0
                )
                
                # 呼叫 on_response callback
                if hasattr(self, 'on_response'):
                    # 轉換成 AssistantMessage 格式
                    response = AssistantMessage(
                        text=outbound.content,
                        chat_id=outbound.chat_id
                    )
                    await self.on_response(response, outbound.channel, outbound.chat_id)
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Outbound consumer 發生錯誤: {e}")
    
    async def process_queue(self) -> None:
        """
        處理 inbound queue 中的訊息（非同步迴圈）
        
        每個訊息都會spawn成独立task並行處理
        結果會丟到 outbound queue，由 _consume_outbound 發送
        """
        self.running = True
        
        # 啟動 outbound 消費者
        self._outbound_task = asyncio.create_task(self._consume_outbound())
        
        while self.running:
            try:
                # 等待 inbound 訊息（超時 1 秒，檢查是否要停止）
                try:
                    inbound = await asyncio.wait_for(
                        self.bus.consume_inbound(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                chat_id = inbound.chat_id
                
                # Spawn 成獨立 task（不再await）
                task = asyncio.create_task(self._process_message(inbound))
                
                # 追蹤這個 chat_id 的 tasks
                if chat_id not in self._active_tasks:
                    self._active_tasks[chat_id] = []
                self._active_tasks[chat_id].append(task)
                
                # Task 完成後自動清理
                task.add_done_callback(
                    lambda t, cid=chat_id: self._active_tasks.get(cid, []).remove(t) 
                    if t in self._active_tasks.get(cid, []) else None
                )
                
            except Exception as e:
                logger.error(f"Inbound consumer 發生錯誤: {e}")
    
    async def cancel_chat(self, chat_id: str) -> int:
        """
        取消特定 chat_id 的所有正在處理的任務
        
        參數：
            chat_id: 聊天室 ID
        
        回傳：
            int: 被取消的任務數量
        """
        tasks = self._active_tasks.pop(chat_id, [])
        cancelled = 0
        for task in tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        return cancelled
    
    async def cancel_all(self) -> int:
        """
        取消所有正在處理的任務
        
        回傳：
            int: 被取消的任務數量
        """
        total = 0
        chat_ids = list(self._active_tasks.keys())
        for chat_id in chat_ids:
            total += await self.cancel_chat(chat_id)
        return total
    
    async def stop(self) -> None:
        """停止處理並取消所有進行中的任務"""
        self.running = False
        
        # 取消 outbound 消費者
        if self._outbound_task and not self._outbound_task.done():
            self._outbound_task.cancel()
            try:
                await self._outbound_task
            except asyncio.CancelledError:
                pass
        
        # 取消所有處理中的任務
        await self.cancel_all()
    
    async def reset_conversation(self, chat_id: str) -> None:
        """
        重置特定對話的歷史
        
        參數：
            chat_id: 聊天室 ID
        """
        # 先取消這個chat正在處理的任務
        await self.cancel_chat(chat_id)
        # 讓 Agent 去清除 Storage 裡的歷史
        await self.agent.reset_history(chat_id)
    
    @property
    def queue_sizes(self) -> tuple[int, int]:
        """回傳 (inbound_size, outbound_size)"""
        return (self.bus.inbound_size, self.bus.outbound_size)


# ============================================
# 使用範例（Console 版本）
# ============================================

"""
# 建立 Queue 版本（Bus 版）

import asyncio
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage
from minibot.bus.message_queue import MessageQueue
from minibot.bus.message import UserMessage

async def main():
    # 1. 建立 Storage（可替換）
    storage = MemoryStorage()
    
    # 2. 建立 LLM
    llm = OpenAILLM(
        api_key="your-key",
        default_model="gpt-4o-mini"
    )
    
    # 3. 建立 Agent（傳入 storage）
    config = AgentConfig(system_prompt="你是個簡潔的助理。")
    agent = AgentLoop(config, llm, storage)
    
    # 4. 建立 Queue（Bus 版本）
    mq = MessageQueue(agent)
    
    # 5. 定義收到回覆時要做什麼
    # 注意：現在多了 channel 參數
    async def on_response(response, channel, chat_id):
        logger.info(f"[{channel}] 🤖: {response.text}")
    
    mq.on_response = on_response
    
    # 6. 啟動處理迴圈（在背景執行）
    processor = asyncio.create_task(mq.process_queue())
    
    # 7. 主執行緒處理輸入
    while True:
        line = input("\n你: ").strip()
        
        if line.lower() == "/exit":
            await mq.stop()
            break
        
        if line.lower() == "/reset":
            await mq.reset_conversation("default")
            logger.info("歷史已清除")
            continue
        
        if line.lower() == "/queues":
            inbound, outbound = mq.queue_sizes
            logger.info(f"Queue sizes: inbound={inbound}, outbound={outbound}")
            continue
        
        # 解析 chat_id
        if line.startswith("@"):
            parts = line[1:].split(" ", 1)
            chat_id = parts[0]
            text = parts[1] if len(parts) > 1 else ""
        else:
            chat_id = "default"
            text = line
        
        # 加入佇列（現在用 enqueue_raw 更方便）
        await mq.enqueue_raw(content=text, chat_id=chat_id, channel="cli")

asyncio.run(main())
"""
