"""Service runtime for starting the OpenSprite gateway process."""

import asyncio
from pathlib import Path

from .agent import AgentLoop
from .bus.message import UserMessage
from .config import AgentConfig
from .context.paths import split_session_id
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
from .llms.registry import find_provider
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

    search_backend = getattr(config.search, "backend", "sqlite")
    if search_backend == "sqlite":
        if config.storage.type != "sqlite":
            raise ValueError('search.backend="sqlite" requires storage.type="sqlite"')

        from .search.sqlite_store import SQLiteSearchStore
        embedding_provider = create_search_embedding_provider(config)

        return SQLiteSearchStore(
            path=config.storage.path,
            history_top_k=config.search.history_top_k,
            knowledge_top_k=config.search.knowledge_top_k,
            embedding_provider=embedding_provider,
            hybrid_candidate_count=config.search.embedding.candidate_count,
            embedding_candidate_strategy=config.search.embedding.candidate_strategy,
            vector_backend=config.search.embedding.vector_backend,
            vector_candidate_count=config.search.embedding.vector_candidate_count,
            retry_failed_on_startup=config.search.embedding.retry_failed_on_startup,
        )

    raise ValueError(f"Unsupported search backend: {search_backend}")


def create_search_embedding_provider(config: Config):
    """Create the optional embedding provider for hybrid search."""
    embedding_config = getattr(config.search, "embedding", None)
    if not embedding_config or not embedding_config.enabled:
        return None

    active_llm = config.llm.get_active()
    api_key = embedding_config.api_key or active_llm.api_key
    base_url = embedding_config.base_url or active_llm.base_url
    if not api_key:
        raise ValueError("search.embedding.api_key is required when enabled=true")

    from .search.embeddings import OpenAIEmbeddingProvider

    provider_spec = find_provider(
        api_key=api_key,
        base_url=base_url or "",
        model=embedding_config.model,
        provider_name=embedding_config.provider,
    )
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        model=embedding_config.model,
        provider_name=provider_spec.name,
        base_url=base_url or provider_spec.default_base_url,
        batch_size=embedding_config.batch_size,
    )


def should_start_search_queue_worker(search_store: SearchStore | None) -> bool:
    """Return whether the runtime should start the persistent embedding queue worker."""
    return bool(
        search_store is not None
        and getattr(search_store, "embedding_provider", None) is not None
        and hasattr(search_store, "run_queue")
    )


def start_search_queue_worker(search_store: SearchStore | None) -> asyncio.Task | None:
    """Start the long-running embedding queue worker when embeddings are enabled."""
    if not should_start_search_queue_worker(search_store):
        return None
    logger.info("Starting search embedding queue worker")
    return asyncio.create_task(search_store.run_queue(once=False))


async def stop_background_task(task: asyncio.Task | None, *, name: str) -> None:
    """Cancel and await one runtime background task."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Stopped {}", name)


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
        llm_chat_temperature=config.llm.temperature,
        llm_chat_max_tokens=config.llm.max_tokens,
        llm_chat_top_p=config.llm.top_p,
        llm_chat_frequency_penalty=config.llm.frequency_penalty,
        llm_chat_presence_penalty=config.llm.presence_penalty,
        llm_pass_decoding_params=config.llm.pass_decoding_params,
        llm_context_window_tokens=cfg.context_window_tokens,
        log_config=config.log,
        search_store=search_store,
        search_config=config.search,
        user_profile_config=config.user_profile,
        active_task_config=config.active_task,
        recent_summary_config=config.recent_summary,
        cron_manager=None,
        media_router=media_router,
        config_path=config.source_path,
        llm_config=config.llm,
        llm_configured=config.is_llm_configured,
        messages_config=config.messages,
    )
    mq = MessageQueue(agent)
    agent._message_bus = mq.bus
    cron_manager = create_cron_manager(config, agent, mq)
    agent.cron_manager = cron_manager
    cron_tool = agent.tools.get("cron")
    if cron_tool is not None:
        cron_tool.set_cron_manager(cron_manager)
    
    return agent, mq, cron_manager


def create_cron_manager(config: Config, agent: AgentLoop, mq: MessageQueue) -> CronManager:
    """Create the per-session cron manager bound to the running agent."""

    async def on_job(session_id: str, job: CronJob) -> str | None:
        channel, raw_external_chat_id = split_session_id(session_id)
        user_message = UserMessage(
            text=job.payload.message,
            channel=job.payload.channel or channel,
            external_chat_id=job.payload.external_chat_id or raw_external_chat_id,
            session_id=session_id,
            sender_id="system:cron",
            sender_name="cron",
            metadata={
                "source": "cron",
                "job_id": job.id,
                "_bypass_commands": True,
                "_suppress_outbound": not job.payload.deliver,
            },
        )
        await mq.enqueue(user_message)
        return None

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

    if not config.is_llm_configured:
        logger.warning(
            "LLM is not configured. Gateway will still start, but agent replies will ask users to configure LLM first."
        )
    
    # 建立 Agent + MessageQueue
    agent, mq, cron_manager = await create_agent(config)
    search_queue_worker = start_search_queue_worker(getattr(agent, "search_store", None))

    # 啟動前先連 MCP，讓外部 tools 在服務運行時就緒
    await agent.connect_mcp()
    await cron_manager.start()
    
    # 啟動訊息處理迴圈
    processor = asyncio.create_task(mq.process_queue())
    channel_manager = None

    try:
        # 啟動所有頻道
        from .channels import start_channels

        channel_manager = await start_channels(mq, config.channels)

        logger.info("OpenSprite gateway 啟動完成！")
        logger.info("按 Ctrl+C 停止")

        # 等待直到被中斷
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("正在關閉...")
    finally:
        if channel_manager is not None:
            await channel_manager.stop_all()
        await mq.stop()
        await stop_background_task(processor, name="message queue processor")
        await stop_background_task(search_queue_worker, name="search embedding queue worker")
        await cron_manager.stop()
        await agent.close_background_maintenance()
        await agent.close_background_skill_reviews()
        close_background_processes = getattr(agent, "close_background_processes", None)
        if close_background_processes is not None:
            await close_background_processes()
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
