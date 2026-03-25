"""Service runtime for starting the OpenSprite gateway process."""

import asyncio
from pathlib import Path

from .agent import AgentLoop
from .config import AgentConfig
from .llms import create_llm
from .search.base import SearchStore
from .storage import MemoryStorage, StorageProvider
from .bus.dispatcher import MessageQueue
from .config import Config
from .utils.log import logger


# ============================================
# 共用設定
# ============================================

def create_storage(config: Config) -> StorageProvider:
    """根據設定建立 Storage"""
    storage_type = config.storage.type
    
    if storage_type == "memory":
        return MemoryStorage()
    elif storage_type == "file":
        from .storage import FileStorage
        return FileStorage(base_path=config.storage.path)
    elif storage_type == "sqlite":
        from .storage import SQLiteStorage
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

    from .search.lancedb_store import LanceDBSearchStore

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
        user_profile_config=config.user_profile,
    )
    mq = MessageQueue(agent)
    
    return agent, mq


# ============================================
# 啟動服務
# ============================================

async def run(config_path: str | Path | None = None) -> None:
    """Start the OpenSprite gateway service."""
    # 讀取設定
    config = Config.load(config_path)
    
    # 初始化日誌
    from .utils.log import setup_log
    setup_log(config.log)
    
    # 檢查 LLM 設定
    if not config.is_llm_configured:
        return
    
    # 建立 Agent + MessageQueue
    agent, mq = await create_agent(config)
    
    # 啟動訊息處理迴圈
    processor = asyncio.create_task(mq.process_queue())
    
    # 啟動所有頻道
    from .channels import start_channels
    await start_channels(mq, config.channels.telegram)
    
    logger.info("OpenSprite gateway 啟動完成！")
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

def gateway(config_path: str | Path | None = None) -> None:
    """Run the foreground OpenSprite gateway."""
    asyncio.run(run(config_path=config_path))


def main(config_path: str | Path | None = None) -> None:
    """Backward-compatible alias for the gateway entrypoint."""
    gateway(config_path=config_path)


if __name__ == "__main__":
    main()
