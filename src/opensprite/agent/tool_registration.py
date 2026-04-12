"""Helpers for registering AgentLoop tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import SearchConfig, ToolsConfig
from ..documents.memory import MemoryStore
from ..cron import CronManager
from ..media import MediaRouter
from ..search.base import SearchStore
from ..tools import (
    Tool,
    ToolRegistry,
    AnalyzeImageTool,
    CronTool,
    ReadFileTool,
    WriteFileTool,
    ListDirTool,
    EditFileTool,
    ExecTool,
    SearchHistoryTool,
    SearchKnowledgeTool,
    WebSearchTool,
    WebFetchTool,
    ReadSkillTool,
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

    async def execute(self, memory_update: str, **kwargs: Any) -> str:
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
) -> None:
    """Register filesystem-oriented tools."""
    registry.register(ReadFileTool(workspace_resolver=workspace_resolver, skills_loader=skills_loader))
    registry.register(WriteFileTool(workspace_resolver=workspace_resolver))
    registry.register(EditFileTool(workspace_resolver=workspace_resolver))
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


def register_shell_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
) -> None:
    """Register shell execution tools."""
    registry.register(ExecTool(workspace_resolver=workspace_resolver))


def register_web_tools(
    registry: ToolRegistry,
    *,
    tools_config: ToolsConfig | None = None,
) -> None:
    """Register web search and fetch tools."""
    current_tools_config = tools_config or ToolsConfig()
    web_search_config = getattr(current_tools_config, "web_search", None) or {}
    web_fetch_config = getattr(current_tools_config, "web_fetch", None) or {}

    registry.register(WebSearchTool(config=web_search_config))
    registry.register(
        WebFetchTool(
            max_chars=web_fetch_config.get("max_chars", 50000),
            timeout=web_fetch_config.get("timeout", 30),
            prefer_trafilatura=web_fetch_config.get("prefer_trafilatura", True),
            firecrawl_api_key=web_fetch_config.get("firecrawl_api_key"),
        )
    )


def register_media_tools(
    registry: ToolRegistry,
    *,
    media_router: MediaRouter | None = None,
    get_current_images: Callable[[], list[str] | None],
) -> None:
    """Register media-analysis tools."""
    registry.register(
        AnalyzeImageTool(
            media_router or MediaRouter(),
            get_current_images=get_current_images,
        )
    )


def register_delegate_tools(
    registry: ToolRegistry,
    *,
    run_subagent: Callable[[str, str], Awaitable[str]],
) -> None:
    """Register delegated subagent execution tools."""
    registry.register(DelegateTool(run_subagent=run_subagent))


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
    get_chat_id: Callable[[], str | None],
) -> None:
    """Register per-session cron scheduling tools when cron is enabled."""
    registry.register(
        CronTool(
            cron_manager,
            get_chat_id=get_chat_id,
        )
    )


def register_default_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    get_chat_id: Callable[[], str | None],
    run_subagent: Callable[[str, str], Awaitable[str]],
    skills_loader: Any = None,
    tools_config: ToolsConfig | None = None,
    search_store: SearchStore | None = None,
    search_config: SearchConfig | None = None,
    cron_manager: CronManager | None = None,
    media_router: MediaRouter | None = None,
    get_current_images: Callable[[], list[str] | None] | None = None,
) -> None:
    """Register the built-in tools used by AgentLoop."""
    register_filesystem_tools(
        registry,
        workspace_resolver=workspace_resolver,
        skills_loader=skills_loader,
    )
    register_skill_tools(
        registry,
        skills_loader=skills_loader,
        workspace_resolver=workspace_resolver,
    )
    register_shell_tools(registry, workspace_resolver=workspace_resolver)
    register_web_tools(registry, tools_config=tools_config)
    register_media_tools(
        registry,
        media_router=media_router,
        get_current_images=get_current_images or (lambda: None),
    )
    register_delegate_tools(registry, run_subagent=run_subagent)
    register_search_tools(
        registry,
        search_store=search_store,
        search_config=search_config,
        get_chat_id=get_chat_id,
    )
    register_cron_tools(
        registry,
        cron_manager=cron_manager,
        get_chat_id=get_chat_id,
    )
