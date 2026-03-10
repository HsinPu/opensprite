"""
minibot/main.py - 入口點

啟動服務：
- 讀取設定檔
- 建立 Agent + MessageQueue
- 啟動所有頻道（Telegram 等）
"""

import asyncio

from minibot.core import AgentLoop
from minibot.config import AgentConfig
from minibot.llms import create_llm
from minibot.storage import MemoryStorage, StorageProvider
from minibot.bus.message_queue import MessageQueue
from minibot.config import Config
from minibot.utils.log import logger


# ============================================
# 共用設定
# ============================================

def create_storage(config: Config) -> StorageProvider:
    """根據設定建立 Storage"""
    storage_type = config.storage.type
    
    if storage_type == "memory":
        return MemoryStorage()
    elif storage_type == "file":
        from minibot.storage import FileStorage
        return FileStorage(base_path=config.storage.path)
    elif storage_type == "sqlite":
        from minibot.storage import SQLiteStorage
        return SQLiteStorage(db_path=config.storage.path)
    else:
        return MemoryStorage()


def create_agent(config: Config):
    """建立 Agent 和 Queue"""
    # 用 Registry 建立 LLM Provider
    cfg = config.llm.get_active()
    llm = create_llm(api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url or "", provider_name=config.llm.default or "")
    
    # 建立 Agent 設定
    agent_config = AgentConfig()
    
    # 建立 Storage
    storage = create_storage(config)
    
    # 建立 Agent
    brave_api_key = config.tools.brave_api_key if hasattr(config, 'tools') else ""
    agent = AgentLoop(agent_config, llm, storage, memory_config=config.memory, tools_config=config.tools, brave_api_key=brave_api_key)
    mq = MessageQueue(agent)
    
    return agent, mq


# ============================================
# 啟動服務
# ============================================

async def run():
    """啟動服務"""
    # 讀取設定
    config = Config.load()
    
    # 初始化日誌
    from minibot.utils.log import setup_log
    setup_log(config.log)
    
    # 檢查 LLM 設定
    if not config.is_llm_configured:
        logger.warning("請在 minibot.json 設定 LLM API Key")
        return
    
    # 建立 Agent + MessageQueue
    agent, mq = create_agent(config)
    
    # 啟動訊息處理迴圈
    processor = asyncio.create_task(mq.process_queue())
    
    # 啟動所有頻道
    from minibot.channels import start_channels
    await start_channels(mq, config.channels.telegram)
    
    logger.info("mini-bot 啟動完成！")
    logger.info("按 Ctrl+C 停止")
    
    # 等待直到被中斷
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("正在關閉...")
    
    # 關閉
    await mq.stop()
    await processor
    logger.info("再見！")


# ============================================
# 主程式
# ============================================

def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
