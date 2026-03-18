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
from typing import Any

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application

from minibot.bus.message import MessageAdapter, UserMessage, AssistantMessage
from minibot.utils.log import logger


class TelegramAdapter(MessageAdapter):
    """
    Telegram 訊息轉接器
    
    實作 MessageAdapter 介面，把 Telegram 的訊息轉換成統一格式。
    透過 MessageQueue 處理訊息，支援並行處理。
    """
    
    DEFAULT_CONFIG = {
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
        "pool_timeout": 30,
        "get_updates_connect_timeout": 10,
        "get_updates_read_timeout": 30,
        "get_updates_write_timeout": 30,
        "get_updates_pool_timeout": 30,
        "poll_timeout": 10,
        "bootstrap_retries": 3,
        "drop_pending_updates": False,
    }

    def __init__(self, bot_token: str, mq=None, config: dict[str, Any] | None = None):
        """
        初始化 Telegram Adapter
        
        參數：
            bot_token: Telegram Bot Token（從 @BotFather 取得）
            mq: MessageQueue 實例（可選，不給則用舊方式直接叫 agent）
        """
        self.bot_token = bot_token
        self.app = None
        self.mq = mq
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}

    def _get_int(self, key: str) -> int:
        """Read an integer config value with sane defaults."""
        return int(self.config.get(key, self.DEFAULT_CONFIG[key]))

    def _get_bool(self, key: str) -> bool:
        """Read a boolean config value with sane defaults."""
        return bool(self.config.get(key, self.DEFAULT_CONFIG[key]))

    def _build_application(self) -> Application:
        """Build the Telegram application with configured request timeouts."""
        builder = Application.builder().token(self.bot_token)
        timeout_keys = [
            "connect_timeout",
            "read_timeout",
            "write_timeout",
            "pool_timeout",
            "get_updates_connect_timeout",
            "get_updates_read_timeout",
            "get_updates_write_timeout",
            "get_updates_pool_timeout",
        ]
        for key in timeout_keys:
            method = getattr(builder, key, None)
            if callable(method):
                builder = method(self._get_int(key))
        return builder.build()

    async def _shutdown_app(self) -> None:
        """Best-effort cleanup for partially started Telegram applications."""
        if self.app is None:
            return

        updater = getattr(self.app, "updater", None)
        if updater is not None and getattr(updater, "running", False):
            try:
                await updater.stop()
            except Exception:
                pass

        if getattr(self.app, "running", False):
            try:
                await self.app.stop()
            except Exception:
                pass

        try:
            await self.app.shutdown()
        except Exception:
            pass
    
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
        
        # 處理圖片
        images = []
        if message.photo:
            images = self._download_images(message.photo, raw_update.bot)
        
        return UserMessage(
            text=text,
            sender=sender,
            chat_id=chat_id,
            images=images if images else None,
            raw=raw_update
        )
    
    async def _download_images(self, photos, bot) -> list[str]:
        """
        下載圖片並轉成 base64
        
        參數：
            photos: telegram photo 清單
            bot: telegram bot 實例
        
        回傳：
            list: base64 編碼的圖片清單
        """
        import base64
        import io
        
        images = []
        
        # 取最大張的圖片
        photo = photos[-1]
        
        try:
            # 取得檔案
            file = await bot.get_file(photo.file_id)
            
            # 下載到記憶體
            file_content = await file.download_as_bytearray()
            
            # 轉 base64
            b64 = base64.b64encode(bytes(file_content)).decode('utf-8')
            
            # 偵測 MIME type
            mime_type = photo.mime_type or "image/jpeg"
            
            images.append(f"data:{mime_type};base64,{b64}")
            
        except Exception as e:
            logger.warning(f"下載圖片失敗: {e}")
        
        return images
    
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
        
        # 轉換 markdown 為 HTML
        html_text = self._markdown_to_html(text)
        
        # 嘗試發送 HTML，失敗則用純文字
        try:
            await self.app.bot.send_message(
                chat_id=message.chat_id,
                text=html_text,
                parse_mode="HTML"
            )
        except Exception:
            # Fallback 到純文字
            await self.app.bot.send_message(
                chat_id=message.chat_id,
                text=text
            )
    
    def _markdown_to_html(self, text: str) -> str:
        """將 Markdown 轉換為 Telegram HTML 格式"""
        import markdown
        
        # 轉換 markdown → HTML
        html = markdown.markdown(text, extensions=['extra', 'codehilite'])
        
        # Telegram HTML 標籤處理
        html = html.replace('<strong>', '<b>').replace('</strong>', '</b>')
        html = html.replace('<em>', '<i>').replace('</em>', '</i>')
        html = html.replace('<code>', '<code>').replace('</code>', '</code>')
        html = html.replace('<pre><code>', '<code>').replace('</code></pre>', '</code>')
        
        return html
    
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
        self.app = self._build_application()
        
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
        try:
            logger.info("Initializing Telegram app...")
            await self.app.initialize()
            logger.info("Starting Telegram app...")
            await self.app.start()
            logger.info("Starting polling...")
            if self.app.updater is None:
                raise RuntimeError("Telegram updater is unavailable")
            await self.app.updater.start_polling(
                timeout=self._get_int("poll_timeout"),
                bootstrap_retries=self._get_int("bootstrap_retries"),
                drop_pending_updates=self._get_bool("drop_pending_updates"),
            )
            logger.info("Telegram polling started!")
        except (TimedOut, NetworkError) as exc:
            await self._shutdown_app()
            raise RuntimeError(
                "Telegram startup timed out while contacting the Bot API. "
                "Check network access to api.telegram.org or increase channels.telegram timeouts in ~/.minibot/minibot.json."
            ) from exc
        except Exception:
            await self._shutdown_app()
            raise
        
        # 保持執行
        await asyncio.Event().wait()


# ============================================
# 使用範例
# ============================================

"""
使用方式（Queue 版）：

```python
import asyncio
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage
from minibot.bus.dispatcher import MessageQueue
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
