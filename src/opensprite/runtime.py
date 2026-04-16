"""Service runtime for starting the OpenSprite gateway process."""

import asyncio
from pathlib import Path

from .agent import AgentLoop
from .bus.events import OutboundMessage
from .bus.message import UserMessage
from .config import AgentConfig
from .context.paths import split_session_chat_id
from .cron import CronManager, CronJob
from .llms import create_llm
from .media import (
    MediaRouter,
    OpenAICompatibleImageProvider,
    OpenAICompatibleSpeechProvider,
    OpenAICompatibleVideoProvider,
)
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
    if storage_type == "sqlite":
        from .storage import SQLiteStorage
        return SQLiteStorage(db_path=config.storage.path)

    raise ValueError(f"Unsupported storage provider: {storage_type}")


def create_search_store(config: Config) -> SearchStore | None:
    """Create the optional search store."""
    if not getattr(config, "search", None) or not config.search.enabled:
        return None

    if config.storage.type != "sqlite":
        raise ValueError("search.enabled=true requires storage.type=sqlite")

    from .search.sqlite_store import SQLiteSearchStore

    return SQLiteSearchStore(
        path=config.storage.path,
        history_top_k=config.search.history_top_k,
        knowledge_top_k=config.search.knowledge_top_k,
    )


def create_media_router(config: Config) -> MediaRouter:
    """Create the media router with optional image analysis support."""
    vision = getattr(config, "vision", None)
    speech = getattr(config, "speech", None)
    video = getattr(config, "video", None)

    image_provider = None
    if vision and vision.enabled:
        image_provider = OpenAICompatibleImageProvider(
            api_key=vision.api_key,
            default_model=vision.model,
            base_url=vision.base_url,
        )

    speech_provider = None
    if speech and speech.enabled:
        speech_provider = OpenAICompatibleSpeechProvider(
            api_key=speech.api_key,
            default_model=speech.model,
            base_url=speech.base_url,
        )

    video_provider = None
    if video and video.enabled:
        video_provider = OpenAICompatibleVideoProvider(
            api_key=video.api_key,
            default_model=video.model,
            base_url=video.base_url,
        )

    return MediaRouter(
        image_provider=image_provider,
        speech_provider=speech_provider,
        video_provider=video_provider,
    )


async def create_agent(config: Config):
    """建立 Agent 和 Queue"""
    # 用 Registry 建立 LLM Provider
    cfg = config.llm.get_active()
    llm = create_llm(api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url or "", provider_name=config.llm.default or "", enabled=cfg.enabled if hasattr(cfg, 'enabled') else True)
    
    # 建立 Agent 設定
    agent_config = config.agent
    
    # 建立 Storage
    storage = create_storage(config)
    search_store = create_search_store(config)
    media_router = create_media_router(config)
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
        recent_summary_config=config.recent_summary,
        cron_manager=None,
        media_router=media_router,
    )
    mq = MessageQueue(agent)
    cron_manager = create_cron_manager(config, agent, mq)
    agent.cron_manager = cron_manager
    cron_tool = agent.tools.get("cron")
    if cron_tool is not None:
        cron_tool.set_cron_manager(cron_manager)
    
    return agent, mq, cron_manager


def create_cron_manager(config: Config, agent: AgentLoop, mq: MessageQueue) -> CronManager:
    """Create the per-session cron manager bound to the running agent."""

    async def on_job(session_chat_id: str, job: CronJob) -> str | None:
        channel, raw_chat_id = split_session_chat_id(session_chat_id)
        user_message = UserMessage(
            text=job.payload.message,
            channel=job.payload.channel or channel,
            chat_id=job.payload.chat_id or raw_chat_id,
            session_chat_id=session_chat_id,
            sender_id="system:cron",
            sender_name="cron",
            metadata={"source": "cron", "job_id": job.id},
        )
        response = await agent.process(user_message)
        if job.payload.deliver and response.text and job.payload.channel and job.payload.chat_id:
            await mq.bus.publish_outbound(
                OutboundMessage(
                    channel=job.payload.channel,
                    chat_id=job.payload.chat_id,
                    session_chat_id=session_chat_id,
                    content=response.text,
                )
            )
        return response.text

    return CronManager(
        workspace_root=Path(agent.tool_workspace or Path.home() / ".opensprite" / "workspace"),
        on_job=on_job,
    )


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
    agent, mq, cron_manager = await create_agent(config)

    # 啟動前先連 MCP，讓外部 tools 在服務運行時就緒
    await agent.connect_mcp()
    await cron_manager.start()
    
    # 啟動訊息處理迴圈
    processor = asyncio.create_task(mq.process_queue())

    try:
        # 啟動所有頻道
        from .channels import start_channels

        await start_channels(mq, config.channels)

        logger.info("OpenSprite gateway 啟動完成！")
        logger.info("按 Ctrl+C 停止")

        # 等待直到被中斷
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("正在關閉...")
    finally:
        await mq.stop()
        await processor
        await cron_manager.stop()
        await agent.close_mcp()
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
