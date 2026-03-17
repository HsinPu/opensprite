"""
minibot/main.py - 入口點

啟動服務：
- 讀取設定檔
- 建立 Agent + MessageQueue
- 啟動所有頻道（Telegram 等）
"""

import asyncio
import subprocess
import sys
from pathlib import Path

from minibot.agent import AgentLoop
from minibot.config import AgentConfig
from minibot.llms import create_llm
from minibot.storage import MemoryStorage, StorageProvider
from minibot.search import LanceDBSearchStore, SearchStore
from minibot.bus.message_queue import MessageQueue
from minibot.config import Config
from minibot.utils.log import logger


# ============================================
# 檢查依賴
# ============================================

def check_and_install_dependencies():
    """檢查並安裝必要套件"""
    req_file = Path(__file__).parent.parent.parent / "requirements.txt"
    
    if not req_file.exists():
        return
    
    with open(req_file, "r") as f:
        requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    
    for req in requirements:
        # 取出套件名稱（不含版本）
        pkg_name = req.split(">=")[0].split("==")[0].split(">")[0].strip()
        
        try:
            __import__(pkg_name)
        except ImportError:
            logger.info(f"安裝缺失套件：{pkg_name}")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", req])
            except subprocess.CalledProcessError as e:
                logger.error(f"安裝失敗：{pkg_name} - {e}")


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


def create_search_store(config: Config) -> SearchStore | None:
    """Create the optional search store."""
    if not getattr(config, "search", None) or not config.search.enabled:
        return None

    provider = (config.search.provider or "lancedb").strip().lower()
    if provider != "lancedb":
        raise ValueError(f"Unsupported search provider: {provider}")

    return LanceDBSearchStore(
        path=config.search.path,
        history_top_k=config.search.history_top_k,
        knowledge_top_k=config.search.knowledge_top_k,
    )


async def create_agent(config: Config):
    """建立 Agent 和 Queue"""
    # 用 Registry 建立 LLM Provider
    cfg = config.llm.get_active()
    llm = create_llm(api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url or "", provider_name=config.llm.default or "", enabled=cfg.enabled if hasattr(cfg, 'enabled') else True)
    
    # 建立 Agent 設定
    agent_config = AgentConfig()
    
    # 建立 Storage
    storage = create_storage(config)
    search_store = create_search_store(config)
    if search_store is not None:
        try:
            await search_store.sync_from_storage(storage)
        except Exception as e:
            logger.warning("Search store sync failed; continuing without search: {}", e)
            search_store = None
    
    # 建立 Agent
    agent = AgentLoop(
        agent_config,
        llm,
        storage,
        memory_config=config.memory,
        tools_config=config.tools,
        log_config=config.log,
        search_store=search_store,
        search_config=config.search,
    )
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
        return
    
    # 建立 Agent + MessageQueue
    agent, mq = await create_agent(config)
    
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
    check_and_install_dependencies()
    asyncio.run(run())


if __name__ == "__main__":
    main()
