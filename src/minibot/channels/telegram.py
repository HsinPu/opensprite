"""
minibot/channels/telegram.py - Telegram 訊息 Adapter

把 Telegram 的原始訊息轉換成「統一訊息格式」並走 MessageQueue。

流程：
1. 收到 Telegram Update
2. 用 TelegramAdapter.to_user_message() 轉成 UserMessage
3. 丟到 MessageQueue（enqueue_raw）
4. Queue 處理完後，on_response callback 會把回覆發送到 Telegram

"""

import asyncio
from telegram import Update
from telegram.ext import Application

from minibot.bus.message import MessageAdapter, UserMessage, AssistantMessage
from minibot.utils.log import logger


class TelegramAdapter(MessageAdapter):
    """
    Telegram 訊息轉接器
    
    實作 MessageAdapter 介面，把 Telegram 的訊息轉換成統一格式。
    透過 MessageQueue 處理訊息，支援並行處理。
    """
    
    def __init__(self, bot_token: str, mq=None):
        """
        初始化 Telegram Adapter
        
        參數：
            bot_token: Telegram Bot Token（從 @BotFather 取得）
            mq: MessageQueue 實例（可選，不給則用舊方式直接叫 agent）
        """
        self.bot_token = bot_token
        self.app = None
        self.mq = mq
    
    def to_user_message(self, raw_update: Update) -> UserMessage:
        """
        把 Telegram Update 轉成統一的 UserMessage
        
        實作 MessageAdapter 介面。
        
        參數：
            raw_update: telegram.Update 物件
        
        回傳：
            UserMessage: 統一格式的訊息
        """
        # 取出訊息內容
        message = raw_update.message
        if message is None:
            return UserMessage(text="", sender=None, chat_id=None, raw=raw_update)
        
        text = message.text or ""
        
        # 取出發送者資訊
        sender = None
        if message.from_user:
            sender = message.from_user.username or message.from_user.name or str(message.from_user.id)
        
        # 取出聊天室 ID
        chat_id = str(message.chat.id) if message.chat else None
        
        return UserMessage(
            text=text,
            sender=sender,
            chat_id=chat_id,
            raw=raw_update
        )
    
    async def send(self, message: AssistantMessage) -> None:
        """
        把助理回覆發送到 Telegram
        
        實作 MessageAdapter 介面。
        
        參數：
            message: AssistantMessage 統一格式的回覆
        """
        if self.app is None:
            raise RuntimeError("TelegramAdapter 未啟動，請先呼叫 run()")
        
        if message.chat_id is None:
            raise ValueError("AssistantMessage 缺少 chat_id，無法發送")
        
        text = message.text
        max_length = 4000
        
        # 截斷過長的訊息
        if len(text) > max_length:
            text = text[:max_length] + f"\n\n... (訊息太長，已截斷，共 {len(message.text)} 字)"
        
        await self.app.bot.send_message(
            chat_id=message.chat_id,
            text=text
        )
    
    async def _on_response(self, response: AssistantMessage, channel: str, chat_id: str) -> None:
        """
        Queue 的回調：收到 Agent 回覆後發送到 Telegram
        """
        # 轉換成 AssistantMessage
        msg = AssistantMessage(text=response.text, chat_id=chat_id)
        await self.send(msg)
    
    async def run(self):
        """
        啟動 Telegram Bot
        
        使用 MessageQueue 時：
        - 訊息會丟到 Queue 處理
        - 回覆會透過 _on_response 發送
        """
        self.app = Application.builder().token(self.bot_token).build()
        
        # 註冊訊息 handler
        async def handle_update(update: Update, context):
            # 轉換成統一格式
            user_msg = self.to_user_message(update)
            
            # 檢查是否是空訊息
            if not user_msg.text:
                return
            
            if self.mq:
                # === 新方式：走 MessageQueue ===
                await self.mq.enqueue_raw(
                    content=user_msg.text,
                    chat_id=user_msg.chat_id,
                    channel="telegram",
                    sender_id=user_msg.sender or "unknown"
                )
            else:
                # === 舊方式：直接叫 agent（向後相容）===
                # 這裡需要傳入 agent，暫時不支援
                raise RuntimeError("請传入 mq (MessageQueue) 來啟動")
        
        from telegram.ext import MessageHandler, filters
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_update))
        
        # 設置 callback 來發送回覆到 Telegram
        async def on_response(response, channel, chat_id):
            await self.send(AssistantMessage(text=response.text, chat_id=chat_id))
        
        self.mq.on_response = on_response
        
        # 初始化並啟動
        logger.info("Initializing Telegram app...")
        await self.app.initialize()
        logger.info("Starting Telegram app...")
        await self.app.start()
        logger.info("Starting polling...")
        await self.app.updater.start_polling()
        logger.info("Telegram polling started!")
        
        # 保持執行
        await asyncio.Event().wait()


# ============================================
# 使用範例
# ============================================

"""
使用方式（Queue 版）：

```python
import asyncio
from minibot.core import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage
from minibot.bus.message_queue import MessageQueue
from minibot.channels.telegram import TelegramAdapter

async def main():
    # 1. 建立 LLM
    llm = OpenAILLM(api_key="your-openai-key")
    
    # 2. 建立 Agent 設定
    config = AgentConfig(system_prompt="你是個有用的助理。")
    
    # 3. 建立 Storage
    storage = MemoryStorage()
    
    # 4. 建立 Agent
    agent = AgentLoop(config, llm, storage)
    
    # 5. 建立 MessageQueue
    mq = MessageQueue(agent)
    
    # 6. 建立 Telegram Adapter（傳入 mq）
    telegram = TelegramAdapter(bot_token="your-telegram-token", mq=mq)
    
    # 7. 啟動（不需要callback，Queue 會自動處理回覆）
    await telegram.run()

asyncio.run(main())
```

這樣：
- 收到 Telegram 訊息 → 丟到 Queue
- Agent 處理完 → Queue 的 on_response 會發送到 Telegram
- 支援多訊息並行處理
"""
