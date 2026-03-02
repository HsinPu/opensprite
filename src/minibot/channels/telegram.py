"""
minibot/channels/telegram.py - Telegram 訊息 Adapter 範例

展示如何把 Telegram 的原始訊息轉換成「統一訊息格式」。

流程：
1. 收到 Telegram Update
2. 用 TelegramAdapter.to_user_message() 轉成 UserMessage
3. 傳給 Agent
4. Agent 回傳 AssistantMessage
5. 用 TelegramAdapter.send() 發送回去

"""

from telegram import Update
from telegram.ext import Application

from minibot.message import MessageAdapter, UserMessage, AssistantMessage


class TelegramAdapter(MessageAdapter):
    """
    Telegram 訊息轉接器
    
    實作 MessageAdapter 介面，把 Telegram 的訊息轉換成統一格式。
    """
    
    def __init__(self, bot_token: str):
        """
        初始化 Telegram Adapter
        
        參數：
            bot_token: Telegram Bot Token（從 @BotFather 取得）
        """
        self.bot_token = bot_token
        self.app = None  # 等到 run() 才會建立
    
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
            # 如果是 edited_message 或 callback_query 等，邏輯會不同
            # 這裡先只處理一般文字訊息
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
            raw=raw_update  # 保留原始訊息，備用
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
            # 如果沒有指定 chat_id，不知道要發到哪
            raise ValueError("AssistantMessage 缺少 chat_id，無法發送")
        
        await self.app.bot.send_message(
            chat_id=message.chat_id,
            text=message.text
        )
    
    async def run(self, on_message_callback):
        """
        啟動 Telegram Bot
        
        參數：
            on_message_callback: 收到訊息時要呼叫的回調函式
                             格式：async def callback(adapter, update)
        """
        self.app = Application.builder().token(self.bot_token).build()
        
        # 註冊訊息 handler
        async def handle_update(update: Update, context):
            # 轉換成統一格式
            user_msg = self.to_user_message(update)
            
            # 檢查是否是空訊息
            if not user_msg.text:
                return
            
            # 呼叫回調
            await on_message_callback(self, update, user_msg)
        
        from telegram.ext import MessageHandler, filters
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_update))
        
        # 啟動 polling
        await self.app.run_polling()


# ============================================
# 使用範例
# ============================================

"""
使用方式：

```python
from minibot.agent import AgentLoop, AgentConfig
from minibot.channels.telegram import TelegramAdapter

# 1. 建立 Agent
config = AgentConfig(api_key="your-openai-key")
agent = AgentLoop(config)

# 2. 建立 Telegram Adapter
telegram = TelegramAdapter(bot_token="your-telegram-token")

# 3. 處理訊息的回調
async def handle(adapter: TelegramAdapter, raw_update, user_msg):
    # 傳給 Agent 處理
    response = await agent.process(user_msg)
    
    # 把回覆發送回去
    await adapter.send(response)

# 4. 啟動
import asyncio
asyncio.run(telegram.run(handle))
```

這樣 Agent 完全不需要知道「這是 Telegram」，
只需要處理 UserMessage -> 回傳 AssistantMessage。
"""
