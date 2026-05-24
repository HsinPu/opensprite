"""Runtime reload helpers for web settings updates."""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..tools.permissions import ToolPermissionPolicy


def reload_agent_llm_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return payload

    updated = dict(payload)
    agent = adapter._get_agent()
    reload_llm = getattr(agent, "reload_llm_from_config", None) if agent is not None else None
    if not callable(reload_llm):
        updated["runtime_reloaded"] = False
        return updated

    try:
        runtime = reload_llm(Config.load(adapter._get_config_path()))
    except Exception as exc:
        logger.warning("LLM runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return updated

    updated["restart_required"] = False
    updated["runtime_reloaded"] = True
    updated["runtime"] = adapter._json_safe(runtime)
    return updated


async def reload_channels_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return payload

    manager = getattr(adapter.mq, "channel_manager", None)
    apply_channels = getattr(manager, "apply", None)
    if not callable(apply_channels):
        return payload

    updated = dict(payload)
    try:
        runtime = await apply_channels(Config.load(adapter._get_config_path()).channels, include_fixed=False)
    except Exception as exc:
        logger.warning("Channel runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return updated

    runtime_ok = bool(runtime.get("ok"))
    updated["restart_required"] = not runtime_ok
    updated["runtime_reloaded"] = runtime_ok
    updated["runtime"] = adapter._json_safe(runtime)
    return updated


def reload_schedule_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return payload

    updated = dict(payload)
    agent = adapter._get_agent()
    if agent is None:
        updated["runtime_reloaded"] = False
        return updated

    try:
        config = Config.load(adapter._get_config_path())
    except Exception as exc:
        logger.warning("Schedule runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return updated

    agent.tools_config = config.tools
    cron_tool = getattr(getattr(agent, "tools", None), "get", lambda _name: None)("cron")
    set_default_timezone = getattr(cron_tool, "set_default_timezone", None)
    tool_updated = False
    if callable(set_default_timezone):
        set_default_timezone(config.tools.cron.default_timezone)
        tool_updated = True

    updated["restart_required"] = False
    updated["runtime_reloaded"] = True
    updated["runtime"] = {"default_timezone": config.tools.cron.default_timezone, "tool_updated": tool_updated}
    return updated


def reload_permissions_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return payload

    updated = dict(payload)
    agent = adapter._get_agent()
    tools = getattr(agent, "tools", None) if agent is not None else None
    set_permission_policy = getattr(tools, "set_permission_policy", None)
    if agent is None or not callable(set_permission_policy):
        updated["runtime_reloaded"] = False
        return updated

    try:
        config = Config.load(adapter._get_config_path())
        agent.tools_config = config.tools
        set_permission_policy(ToolPermissionPolicy.from_config(config.tools.permissions))
    except Exception as exc:
        logger.warning("Tool permission runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return updated

    updated["restart_required"] = False
    updated["runtime_reloaded"] = True
    return updated


def reload_media_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return payload

    updated = dict(payload)
    agent = adapter._get_agent()
    reload_media = getattr(agent, "reload_media_from_config", None) if agent is not None else None
    if not callable(reload_media):
        updated["runtime_reloaded"] = False
        return updated

    try:
        runtime = reload_media(Config.load(adapter._get_config_path()))
    except Exception as exc:
        logger.warning("Media runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return updated

    updated["restart_required"] = False
    updated["runtime_reloaded"] = True
    updated["runtime"] = adapter._json_safe(runtime)
    return updated


def reload_web_search_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return payload

    updated = dict(payload)
    agent = adapter._get_agent()
    reload_web_search = getattr(agent, "reload_web_search_from_config", None) if agent is not None else None
    if not callable(reload_web_search):
        updated["runtime_reloaded"] = False
        return updated

    try:
        runtime = reload_web_search(Config.load(adapter._get_config_path()))
    except Exception as exc:
        logger.warning("Web search runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return updated

    updated["restart_required"] = False
    updated["runtime_reloaded"] = True
    updated["runtime"] = adapter._json_safe(runtime)
    return updated


def reload_browser_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return payload

    updated = dict(payload)
    agent = adapter._get_agent()
    reload_browser = getattr(agent, "reload_browser_from_config", None) if agent is not None else None
    if not callable(reload_browser):
        updated["runtime_reloaded"] = False
        return updated

    try:
        runtime = reload_browser(Config.load(adapter._get_config_path()))
    except Exception as exc:
        logger.warning("Browser runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return updated

    updated["restart_required"] = False
    updated["runtime_reloaded"] = True
    updated["runtime"] = adapter._json_safe(runtime)
    return updated


async def reload_mcp_from_config(adapter: Any, payload: dict[str, Any], *, force: bool = False, logger) -> dict[str, Any]:
    if not force and not payload.get("restart_required"):
        return adapter._with_mcp_runtime(payload)

    updated = dict(payload)
    agent = adapter._get_agent()
    reload_mcp = getattr(agent, "reload_mcp_from_config", None) if agent is not None else None
    if not callable(reload_mcp):
        updated["runtime_reloaded"] = False
        return adapter._with_mcp_runtime(updated)

    try:
        reload_message = await reload_mcp()
    except Exception as exc:
        logger.warning("MCP runtime reload failed after settings change: {}", exc)
        updated["runtime_reloaded"] = False
        updated["reload_error"] = str(exc)
        return adapter._with_mcp_runtime(updated)

    updated["restart_required"] = False
    updated["runtime_reloaded"] = True
    updated["reload_message"] = reload_message
    return adapter._with_mcp_runtime(updated)
