"""Built-in tool registration helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import CronMessagesConfig, SearchConfig, ToolsConfig
from ..config.defaults import DEFAULT_BROWSER_COMMAND_TIMEOUT, DEFAULT_BROWSER_SESSION_TIMEOUT
from ..cron import CronManager
from ..documents.memory import MemoryStore
from ..media import MediaRouter, outbound_media_error_result
from ..search.base import SearchStore
from .active_task import TaskUpdateTool
from .audio import TranscribeAudioTool
from .batch import BatchTool
from .browser import (
    BrowserBackTool,
    BrowserClickTool,
    BrowserConsoleTool,
    BrowserNavigateTool,
    BrowserPressTool,
    BrowserScrollTool,
    BrowserSnapshotTool,
    BrowserTypeTool,
)
from .browser_runtime import AgentBrowserRuntime, cloud_provider_from_config
from .code_navigation import CodeNavigationTool
from .credential_store import CredentialStoreTool
from .cron import CronTool
from .filesystem import (
    ApplyPatchTool,
    EditFileTool,
    GlobFilesTool,
    GrepFilesTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from .image import AnalyzeImageTool, OCRImageTool
from .memory import SaveMemoryTool
from .mcp_config import ConfigureMCPTool
from .outbound_media import SendMediaTool
from .permissions import ToolPermissionPolicy
from .process import ProcessTool
from .process_runtime import BackgroundProcessManager
from .registry import ToolRegistry
from .run_trace import ListRunFileChangesTool, PreviewRunFileChangeRevertTool
from .search import SearchHistoryTool
from .shell import ExecTool
from .skill import ReadSkillTool
from .skill_config import ConfigureSkillTool
from .verify import VerifyTool
from .video import AnalyzeVideoTool
from .web_fetch import WebFetchTool
from .web_research import WebResearchTool
from .web_search import WebSearchTool
from .workflow import RunWorkflowTool

BROWSER_TOOL_NAMES = (
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_scroll",
    "browser_back",
    "browser_console",
)


def register_memory_tool(
    registry: ToolRegistry,
    memory_store: MemoryStore,
    get_session_id: Callable[[], str | None],
) -> None:
    """Register the long-term memory update tool."""
    registry.register(SaveMemoryTool(memory_store, get_session_id))


def register_task_tools(
    registry: ToolRegistry,
    *,
    get_session_id: Callable[[], str | None],
    active_task_store_factory: Callable[[str], Any | None] | None = None,
    get_message_count: Callable[[str], Awaitable[int]] | None = None,
) -> None:
    """Register explicit active-task state management tools."""
    registry.register(
        TaskUpdateTool(
            get_session_id=get_session_id,
            active_task_store_factory=active_task_store_factory,
            get_message_count=get_message_count,
        )
    )


def register_run_trace_tools(
    registry: ToolRegistry,
    *,
    storage: Any,
    get_session_id: Callable[[], str | None],
    preview_run_file_change_revert: Callable[[str, str, int], Awaitable[dict[str, Any]]],
) -> None:
    """Register read-only run trace inspection tools."""
    registry.register(ListRunFileChangesTool(storage=storage, get_session_id=get_session_id))
    registry.register(
        PreviewRunFileChangeRevertTool(
            get_session_id=get_session_id,
            preview_revert=preview_run_file_change_revert,
        )
    )


def register_filesystem_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    skills_loader: Any = None,
    config_path_resolver: Callable[[], Path | None] | None = None,
    file_change_recorder: Callable[[str, list[dict[str, Any]]], Awaitable[None]] | None = None,
) -> None:
    """Register filesystem-oriented tools."""
    registry.register(ReadFileTool(workspace_resolver=workspace_resolver, skills_loader=skills_loader))
    registry.register(GlobFilesTool(workspace_resolver=workspace_resolver))
    registry.register(GrepFilesTool(workspace_resolver=workspace_resolver))
    registry.register(CodeNavigationTool(workspace_resolver=workspace_resolver))
    registry.register(
        ApplyPatchTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
            file_change_recorder=file_change_recorder,
        )
    )
    registry.register(
        WriteFileTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
            file_change_recorder=file_change_recorder,
        )
    )
    registry.register(
        EditFileTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
            file_change_recorder=file_change_recorder,
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
    background_session_owner_factory: Callable[[], dict[str, str | None] | None] | None = None,
    process_manager_callback: Callable[[Any], None] | None = None,
    storage: Any = None,
) -> None:
    """Register shell execution tools."""
    current_tools_config = tools_config or ToolsConfig()
    process_tool = ProcessTool(manager=BackgroundProcessManager(storage=storage))
    if process_manager_callback is not None:
        process_manager_callback(process_tool.manager)
    registry.register(
        ExecTool(
            workspace_resolver=workspace_resolver,
            timeout=current_tools_config.exec_tool.timeout,
            process_manager=process_tool.manager,
            background_notification_factory=background_notification_factory,
            background_session_owner_factory=background_session_owner_factory,
            notify_on_exit=current_tools_config.exec_tool.notify_on_exit,
            notify_on_exit_empty_success=current_tools_config.exec_tool.notify_on_exit_empty_success,
        )
    )
    registry.register(process_tool)


def register_verify_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
) -> None:
    """Register fixed project verification checks."""
    registry.register(VerifyTool(workspace_resolver=workspace_resolver))


def register_config_tools(
    registry: ToolRegistry,
    *,
    config_path_resolver: Callable[[], Path | None],
    reload_mcp: Callable[[], Awaitable[str]],
    app_home: Path | None = None,
    workspace_resolver: Callable[[], Path] | None = None,
) -> None:
    """Register tools that safely update application configuration."""
    from ..tools.subagent_config import ConfigureSubagentTool

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
    registry.register(CredentialStoreTool(app_home=app_home))


def register_web_tools(
    registry: ToolRegistry,
    *,
    tools_config: ToolsConfig | None = None,
    get_session_id: Callable[[], str | None] | None = None,
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
    registry.register(
        WebResearchTool(
            search_config=web_search_config,
            fetch_config=web_fetch_config,
        )
    )


def register_browser_tools(
    registry: ToolRegistry,
    *,
    get_session_id: Callable[[], str | None],
    tools_config: ToolsConfig | None = None,
) -> None:
    """Register local browser automation tools."""
    browser_config = getattr(tools_config, "browser", None) if tools_config is not None else None
    if browser_config is not None and not browser_config.enabled:
        return
    runtime = AgentBrowserRuntime(
        command_timeout=getattr(browser_config, "command_timeout", DEFAULT_BROWSER_COMMAND_TIMEOUT),
        session_timeout=getattr(browser_config, "session_timeout", DEFAULT_BROWSER_SESSION_TIMEOUT),
        cdp_url=getattr(browser_config, "cdp_url", ""),
        launch_args=getattr(browser_config, "launch_args", ""),
        cloud_provider=cloud_provider_from_config(browser_config) if browser_config is not None else None,
    )
    kwargs = {"get_session_id": get_session_id, "runtime": runtime, "browser_config": browser_config}
    registry.register(BrowserNavigateTool(**kwargs))
    registry.register(BrowserSnapshotTool(**kwargs))
    registry.register(BrowserClickTool(**kwargs))
    registry.register(BrowserTypeTool(**kwargs))
    registry.register(BrowserPressTool(**kwargs))
    registry.register(BrowserScrollTool(**kwargs))
    registry.register(BrowserBackTool(**kwargs))
    registry.register(BrowserConsoleTool(**kwargs))


def register_media_tools(
    registry: ToolRegistry,
    *,
    media_router: MediaRouter | None = None,
    get_current_images: Callable[[], list[str] | None],
    get_current_audios: Callable[[], list[str] | None],
    get_current_videos: Callable[[], list[str] | None],
    workspace_resolver: Callable[[], Path] | None = None,
    queue_outbound_media: Callable[[str, str], str | None] | None = None,
) -> None:
    """Register media-analysis tools."""
    registry.register(
        AnalyzeImageTool(
            media_router or MediaRouter(),
            get_current_images=get_current_images,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        OCRImageTool(
            media_router or MediaRouter(),
            get_current_images=get_current_images,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        TranscribeAudioTool(
            media_router or MediaRouter(),
            get_current_audios=get_current_audios,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        AnalyzeVideoTool(
            media_router or MediaRouter(),
            get_current_videos=get_current_videos,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        SendMediaTool(
            queue_media=queue_outbound_media
            or (
                lambda kind, payload: outbound_media_error_result(
                    "outbound media is unavailable.",
                    category="missing_turn_context",
                )
            ),
            get_current_images=get_current_images,
            get_current_audios=get_current_audios,
            get_current_videos=get_current_videos,
        )
    )


def register_delegate_tools(
    registry: ToolRegistry,
    *,
    run_subagent: Callable[[str, str | None, str | None], Awaitable[str]],
    run_subagents_many: Callable[[list[dict[str, Any]], int | None], Awaitable[str]] | None = None,
    app_home: Path | None = None,
    workspace_resolver: Callable[[], Path] | None = None,
) -> None:
    """Register delegated subagent execution tools."""
    from ..tools.delegate import DelegateTool

    registry.register(
        DelegateTool(
            run_subagent=run_subagent,
            app_home=app_home,
            workspace_resolver=workspace_resolver,
        )
    )
    if run_subagents_many is not None:
        from ..tools.delegate_many import DelegateManyTool

        registry.register(
            DelegateManyTool(
                run_subagents_many=run_subagents_many,
                app_home=app_home,
                workspace_resolver=workspace_resolver,
            )
        )


def register_workflow_tools(
    registry: ToolRegistry,
    *,
    run_workflow: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    workflow_catalog_getter: Callable[[], dict[str, str]] | None = None,
) -> None:
    """Register fixed orchestration workflow tools."""
    if run_workflow is None or workflow_catalog_getter is None:
        return
    registry.register(
        RunWorkflowTool(
            run_workflow=run_workflow,
            workflow_catalog_getter=workflow_catalog_getter,
        )
    )


def register_search_tools(
    registry: ToolRegistry,
    *,
    search_store: SearchStore | None = None,
    search_config: SearchConfig | None = None,
    get_session_id: Callable[[], str | None],
) -> None:
    """Register per-session search tools when search is enabled."""
    if search_store is None:
        return

    current_search_config = search_config or SearchConfig()
    registry.register(
        SearchHistoryTool(
            store=search_store,
            get_session_id=get_session_id,
            default_limit=current_search_config.history_top_k,
        )
    )


def register_cron_tools(
    registry: ToolRegistry,
    *,
    cron_manager: CronManager | None = None,
    tools_config: ToolsConfig | None = None,
    messages_config: CronMessagesConfig | None = None,
    get_session_id: Callable[[], str | None],
) -> None:
    """Register per-session cron scheduling tools when cron is enabled."""
    current_tools_config = tools_config or ToolsConfig()
    registry.register(
        CronTool(
            cron_manager,
            get_session_id=get_session_id,
            default_timezone=current_tools_config.cron.default_timezone,
            messages_config=messages_config,
        )
    )


def register_batch_tools(registry: ToolRegistry) -> None:
    """Register safe parallel read-only batch execution."""
    registry.register(BatchTool(registry_resolver=lambda: registry))


def register_default_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    get_session_id: Callable[[], str | None],
    run_subagent: Callable[[str, str | None, str | None], Awaitable[str]],
    run_subagents_many: Callable[[list[dict[str, Any]], int | None], Awaitable[str]] | None = None,
    run_workflow: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    workflow_catalog_getter: Callable[[], dict[str, str]] | None = None,
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
    queue_outbound_media: Callable[[str, str], str | None] | None = None,
    background_notification_factory: Callable[[], Any | None] | None = None,
    background_session_owner_factory: Callable[[], dict[str, str | None] | None] | None = None,
    process_manager_callback: Callable[[Any], None] | None = None,
    active_task_store_factory: Callable[[str], Any | None] | None = None,
    get_message_count: Callable[[str], Awaitable[int]] | None = None,
    file_change_recorder: Callable[[str, list[dict[str, Any]]], Awaitable[None]] | None = None,
    storage: Any = None,
    preview_run_file_change_revert: Callable[[str, str, int], Awaitable[dict[str, Any]]] | None = None,
) -> None:
    """Register the built-in tools used by AgentLoop."""
    current_tools_config = tools_config or ToolsConfig()
    registry.set_permission_policy(ToolPermissionPolicy.from_config(current_tools_config.permissions))
    register_filesystem_tools(
        registry,
        workspace_resolver=workspace_resolver,
        skills_loader=skills_loader,
        config_path_resolver=config_path_resolver,
        file_change_recorder=file_change_recorder,
    )
    register_skill_tools(
        registry,
        skills_loader=skills_loader,
        workspace_resolver=workspace_resolver,
    )
    register_task_tools(
        registry,
        get_session_id=get_session_id,
        active_task_store_factory=active_task_store_factory,
        get_message_count=get_message_count,
    )
    if storage is not None and preview_run_file_change_revert is not None:
        register_run_trace_tools(
            registry,
            storage=storage,
            get_session_id=get_session_id,
            preview_run_file_change_revert=preview_run_file_change_revert,
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
        tools_config=current_tools_config,
        background_notification_factory=background_notification_factory,
        background_session_owner_factory=background_session_owner_factory,
        process_manager_callback=process_manager_callback,
        storage=storage,
    )
    register_verify_tools(registry, workspace_resolver=workspace_resolver)
    register_web_tools(
        registry,
        tools_config=current_tools_config,
        get_session_id=get_session_id,
    )
    register_browser_tools(registry, get_session_id=get_session_id, tools_config=current_tools_config)
    register_media_tools(
        registry,
        media_router=media_router,
        get_current_images=get_current_images or (lambda: None),
        get_current_audios=get_current_audios or (lambda: None),
        get_current_videos=get_current_videos or (lambda: None),
        workspace_resolver=workspace_resolver,
        queue_outbound_media=queue_outbound_media,
    )
    register_delegate_tools(
        registry,
        run_subagent=run_subagent,
        run_subagents_many=run_subagents_many,
        app_home=app_home,
        workspace_resolver=workspace_resolver,
    )
    register_workflow_tools(
        registry,
        run_workflow=run_workflow,
        workflow_catalog_getter=workflow_catalog_getter,
    )
    register_search_tools(
        registry,
        search_store=search_store,
        search_config=search_config,
        get_session_id=get_session_id,
    )
    register_cron_tools(
        registry,
        cron_manager=cron_manager,
        tools_config=current_tools_config,
        messages_config=cron_messages_config,
        get_session_id=get_session_id,
    )
    register_batch_tools(registry)
