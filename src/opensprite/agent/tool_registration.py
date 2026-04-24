"""Helpers for registering AgentLoop tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import CronMessagesConfig, SearchConfig, ToolsConfig
from ..documents.memory import MemoryStore
from ..cron import CronManager
from ..media import MediaRouter
from ..search.base import SearchStore
from ..tools import (
    Tool,
    ToolRegistry,
    AnalyzeImageTool,
    OCRImageTool,
    CronTool,
    TranscribeAudioTool,
    AnalyzeVideoTool,
    ReadFileTool,
    WriteFileTool,
    ListDirTool,
    EditFileTool,
    ExecTool,
    ProcessTool,
    SearchHistoryTool,
    SearchKnowledgeTool,
    WebSearchTool,
    WebFetchTool,
    ReadSkillTool,
    ConfigureSkillTool,
    ConfigureMCPTool,
    ConfigureSubagentTool,
)
from ..tools.delegate import DelegateTool


class SaveMemoryTool(Tool):
    name = "save_memory"
    description = "Save important information to long-term memory. Include all existing facts plus new ones."
    parameters = {
        "type": "object",
        "properties": {
            "memory_update": {"type": "string", "description": "Updated memory as markdown"}
        },
        "required": ["memory_update"],
    }

    def __init__(self, memory_store: MemoryStore, get_chat_id: Callable[[], str | None]):
        self.memory_store = memory_store
        self.get_chat_id = get_chat_id

    async def _execute(self, memory_update: str, **kwargs: Any) -> str:
        chat_id = self.get_chat_id()
        if not chat_id:
            return "Error: current chat_id is unavailable. save_memory requires an active chat context."
        current = self.memory_store.read(chat_id)
        if memory_update != current:
            self.memory_store.write(chat_id, memory_update)
            return f"Memory saved ({len(memory_update)} chars)"
        return "Memory unchanged"


def register_memory_tool(
    registry: ToolRegistry,
    memory_store: MemoryStore,
    get_chat_id: Callable[[], str | None],
) -> None:
    """Register the long-term memory update tool."""
    registry.register(SaveMemoryTool(memory_store, get_chat_id))


def register_filesystem_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    skills_loader: Any = None,
    config_path_resolver: Callable[[], Path | None] | None = None,
) -> None:
    """Register filesystem-oriented tools."""
    registry.register(ReadFileTool(workspace_resolver=workspace_resolver, skills_loader=skills_loader))
    registry.register(
        WriteFileTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
        )
    )
    registry.register(
        EditFileTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
        )
    )
    registry.register(ListDirTool(workspace_resolver=workspace_resolver))


def register_skill_tools(
    registry: ToolRegistry,
    *,
    skills_loader: Any = None,
    workspace_resolver: Callable[[], Path],
) -> None:
    """Register optional skill-loading tools."""
    if skills_loader:
        registry.register(
            ReadSkillTool(
                skills_loader=skills_loader,
                personal_skills_dir_resolver=lambda: workspace_resolver() / "skills",
            )
        )
        registry.register(
            ConfigureSkillTool(
                skills_loader=skills_loader,
                workspace_resolver=workspace_resolver,
            )
        )


def register_shell_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    tools_config: ToolsConfig | None = None,
    background_notification_factory: Callable[[], Any | None] | None = None,
) -> None:
    """Register shell execution tools."""
    current_tools_config = tools_config or ToolsConfig()
    process_tool = ProcessTool()
    registry.register(
        ExecTool(
            workspace_resolver=workspace_resolver,
            timeout=current_tools_config.exec_tool.timeout,
            process_manager=process_tool.manager,
            background_notification_factory=background_notification_factory,
            notify_on_exit=current_tools_config.exec_tool.notify_on_exit,
            notify_on_exit_empty_success=current_tools_config.exec_tool.notify_on_exit_empty_success,
        )
    )
    registry.register(process_tool)


def register_config_tools(
    registry: ToolRegistry,
    *,
    config_path_resolver: Callable[[], Path | None],
    reload_mcp: Callable[[], Awaitable[str]],
    app_home: Path | None = None,
    workspace_resolver: Callable[[], Path] | None = None,
) -> None:
    """Register tools that safely update application configuration."""
    registry.register(
        ConfigureMCPTool(
            config_path_resolver=config_path_resolver,
            reload_callback=reload_mcp,
        )
    )
    registry.register(
        ConfigureSubagentTool(
            app_home=app_home,
            workspace_resolver=workspace_resolver,
        )
    )


def register_web_tools(
    registry: ToolRegistry,
    *,
    tools_config: ToolsConfig | None = None,
) -> None:
    """Register web search and fetch tools."""
    current_tools_config = tools_config or ToolsConfig()
    web_search_config = current_tools_config.web_search
    web_fetch_config = current_tools_config.web_fetch

    registry.register(WebSearchTool(config=web_search_config))
    registry.register(
        WebFetchTool(
            max_chars=web_fetch_config.max_chars,
            max_response_size=web_fetch_config.max_response_size,
            timeout=web_fetch_config.timeout,
            prefer_trafilatura=web_fetch_config.prefer_trafilatura,
            firecrawl_api_key=web_fetch_config.firecrawl_api_key,
        )
    )


def register_media_tools(
    registry: ToolRegistry,
    *,
    media_router: MediaRouter | None = None,
    get_current_images: Callable[[], list[str] | None],
    get_current_audios: Callable[[], list[str] | None],
    get_current_videos: Callable[[], list[str] | None],
) -> None:
    """Register media-analysis tools."""
    registry.register(
        AnalyzeImageTool(
            media_router or MediaRouter(),
            get_current_images=get_current_images,
        )
    )
    registry.register(
        OCRImageTool(
            media_router or MediaRouter(),
            get_current_images=get_current_images,
        )
    )
    registry.register(
        TranscribeAudioTool(
            media_router or MediaRouter(),
            get_current_audios=get_current_audios,
        )
    )
    registry.register(
        AnalyzeVideoTool(
            media_router or MediaRouter(),
            get_current_videos=get_current_videos,
        )
    )


def register_delegate_tools(
    registry: ToolRegistry,
    *,
    run_subagent: Callable[[str, str], Awaitable[str]],
    app_home: Path | None = None,
    workspace_resolver: Callable[[], Path] | None = None,
) -> None:
    """Register delegated subagent execution tools."""
    registry.register(
        DelegateTool(
            run_subagent=run_subagent,
            app_home=app_home,
            workspace_resolver=workspace_resolver,
        )
    )


def register_search_tools(
    registry: ToolRegistry,
    *,
    search_store: SearchStore | None = None,
    search_config: SearchConfig | None = None,
    get_chat_id: Callable[[], str | None],
) -> None:
    """Register per-chat search tools when search is enabled."""
    if search_store is None:
        return

    current_search_config = search_config or SearchConfig()
    registry.register(
        SearchHistoryTool(
            store=search_store,
            get_chat_id=get_chat_id,
            default_limit=current_search_config.history_top_k,
        )
    )
    registry.register(
        SearchKnowledgeTool(
            store=search_store,
            get_chat_id=get_chat_id,
            default_limit=current_search_config.knowledge_top_k,
        )
    )


def register_cron_tools(
    registry: ToolRegistry,
    *,
    cron_manager: CronManager | None = None,
    tools_config: ToolsConfig | None = None,
    messages_config: CronMessagesConfig | None = None,
    get_chat_id: Callable[[], str | None],
) -> None:
    """Register per-session cron scheduling tools when cron is enabled."""
    current_tools_config = tools_config or ToolsConfig()
    registry.register(
        CronTool(
            cron_manager,
            get_chat_id=get_chat_id,
            default_timezone=current_tools_config.cron.default_timezone,
            messages_config=messages_config,
        )
    )


def register_default_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    get_chat_id: Callable[[], str | None],
    run_subagent: Callable[[str, str], Awaitable[str]],
    config_path_resolver: Callable[[], Path | None],
    reload_mcp: Callable[[], Awaitable[str]],
    app_home: Path | None = None,
    skills_loader: Any = None,
    tools_config: ToolsConfig | None = None,
    search_store: SearchStore | None = None,
    search_config: SearchConfig | None = None,
    cron_manager: CronManager | None = None,
    cron_messages_config: CronMessagesConfig | None = None,
    media_router: MediaRouter | None = None,
    get_current_images: Callable[[], list[str] | None] | None = None,
    get_current_audios: Callable[[], list[str] | None] | None = None,
    get_current_videos: Callable[[], list[str] | None] | None = None,
    background_notification_factory: Callable[[], Any | None] | None = None,
) -> None:
    """Register the built-in tools used by AgentLoop."""
    register_filesystem_tools(
        registry,
        workspace_resolver=workspace_resolver,
        skills_loader=skills_loader,
        config_path_resolver=config_path_resolver,
    )
    register_skill_tools(
        registry,
        skills_loader=skills_loader,
        workspace_resolver=workspace_resolver,
    )
    register_config_tools(
        registry,
        config_path_resolver=config_path_resolver,
        reload_mcp=reload_mcp,
        app_home=app_home,
        workspace_resolver=workspace_resolver,
    )
    register_shell_tools(
        registry,
        workspace_resolver=workspace_resolver,
        tools_config=tools_config,
        background_notification_factory=background_notification_factory,
    )
    register_web_tools(registry, tools_config=tools_config)
    register_media_tools(
        registry,
        media_router=media_router,
        get_current_images=get_current_images or (lambda: None),
        get_current_audios=get_current_audios or (lambda: None),
        get_current_videos=get_current_videos or (lambda: None),
    )
    register_delegate_tools(
        registry,
        run_subagent=run_subagent,
        app_home=app_home,
        workspace_resolver=workspace_resolver,
    )
    register_search_tools(
        registry,
        search_store=search_store,
        search_config=search_config,
        get_chat_id=get_chat_id,
    )
    register_cron_tools(
        registry,
        cron_manager=cron_manager,
        tools_config=tools_config,
        messages_config=cron_messages_config,
        get_chat_id=get_chat_id,
    )
