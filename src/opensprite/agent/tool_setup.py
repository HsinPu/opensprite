"""Agent tool registry setup."""

from __future__ import annotations

from typing import Any

from ..storage.base import get_storage_message_count
from ..tools import ToolRegistry
from ..tools.registration import register_default_tools
from ..utils.log import logger


def setup_agent_tools(agent: Any, tools: ToolRegistry | None) -> ToolRegistry:
    """Resolve the tool registry and populate defaults when needed."""
    registry = tools or ToolRegistry()
    if registry.tool_names:
        return registry

    agent.tools = registry
    register_default_agent_tools(agent)
    return agent.tools


def register_default_agent_tools(agent: Any) -> None:
    """Register OpenSprite's default tools using the owning AgentLoop callbacks."""
    register_default_tools(
        agent.tools,
        workspace_resolver=agent._get_current_workspace,
        get_session_id=agent._get_current_session_id,
        run_subagent=agent.run_subagent,
        run_subagents_many=agent.run_subagents_many,
        run_workflow=agent.run_workflow,
        workflow_catalog_getter=lambda: agent.workflows.catalog(),
        config_path_resolver=agent._get_config_path,
        reload_mcp=agent.reload_mcp_from_config,
        app_home=agent.app_home,
        skills_loader=getattr(agent._context_builder, "skills_loader", None),
        tools_config=agent.tools_config,
        search_store=agent.search_store,
        search_config=agent.search_config,
        cron_manager=agent.cron_manager,
        cron_messages_config=agent.messages.cron,
        media_router=agent.media_router,
        get_current_images=agent._get_current_images,
        get_current_audios=agent._get_current_audios,
        get_current_videos=agent._get_current_videos,
        queue_outbound_media=agent._queue_outbound_media,
        background_notification_factory=agent._make_background_session_exit_notifier,
        background_session_owner_factory=agent._current_background_session_owner,
        process_manager_callback=agent._set_background_process_manager,
        active_task_store_factory=agent._get_active_task_store,
        get_message_count=lambda session_id: get_storage_message_count(agent.storage, session_id),
        file_change_recorder=agent._record_file_changes,
        storage=agent.storage,
        preview_run_file_change_revert=agent.preview_run_file_change_revert,
    )

    logger.info(f"agent.init | tools={', '.join(agent.tools.tool_names)}")
