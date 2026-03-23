"""
opensprite/channels/__init__.py - 訊息頻道適配器

匯出各平台的訊息適配器
"""

import asyncio
from .telegram import TelegramAdapter


async def start_channels(mq, telegram_config):
    """啟動所有已設定的頻道"""
    tasks = []
    
    # Telegram
    if telegram_config and telegram_config.get("enabled"):
        telegram = TelegramAdapter(
            bot_token=telegram_config.get("token", ""),
            mq=mq,
            config=telegram_config,
        )
        tasks.append(asyncio.create_task(telegram.run()))
        from ..utils.log import logger
        logger.info("Telegram 啟動中...")
    
    # 等待所有頻道
    if tasks:
        await asyncio.gather(*tasks)


__all__ = ["TelegramAdapter", "start_channels"]
