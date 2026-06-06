"""MCP connection lifecycle helpers for AgentLoop."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Config, ToolsConfig
from ..runs.events import MCP_CONNECTED_EVENT, MCP_CONNECTION_FAILED_EVENT
from ..tool_names import (
    DELEGATE_MANY_TOOL_NAME,
    DELEGATE_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
    RUN_WORKFLOW_TOOL_NAME,
)
from ..tools import ToolRegistry
from ..tools.result_status import tool_error_result
from ..utils.log import logger


MCP_TOOL_NAME_PREFIX = "mcp_"
PROGRESS_NOTICE_TOOL_NAMES = frozenset(
    {
        READ_SKILL_TOOL_NAME,
        DELEGATE_TOOL_NAME,
        DELEGATE_MANY_TOOL_NAME,
        RUN_WORKFLOW_TOOL_NAME,
    }
)


def is_mcp_tool_name(tool_name: str | None) -> bool:
    return str(tool_name or "").startswith(MCP_TOOL_NAME_PREFIX)


def mcp_tool_display_name(tool_name: str | None) -> str:
    text = str(tool_name or "")
    return text[len(MCP_TOOL_NAME_PREFIX) :] if is_mcp_tool_name(text) else text


def mcp_tool_names(tool_names: Iterable[str]) -> list[str]:
    return sorted(name for name in tool_names if is_mcp_tool_name(name))


def tool_warrants_progress_notice(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in PROGRESS_NOTICE_TOOL_NAMES or is_mcp_tool_name(tool_name)


def _mcp_lifecycle_error_result(message: str, *, category: str) -> str:
    return tool_error_result(
        str(message or "").strip(),
        error_type="ConfigureMCPToolError",
        category=category,
        metadata={"tool_name": "configure_mcp"},
    )


class McpLifecycleService:
    """Owns MCP connection state, reconnect backoff, and runtime tool summaries."""

    INITIAL_RETRY_BACKOFF_SECONDS = 15.0
    MAX_RETRY_BACKOFF_SECONDS = 300.0

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        tools_config: ToolsConfig,
        context_builder: Any,
        config_path_getter: Callable[[], Path | None],
        current_session_id_getter: Callable[[], str | None],
        current_run_id_getter: Callable[[], str | None],
        current_channel_getter: Callable[[], str | None],
        current_external_chat_id_getter: Callable[[], str | None],
        emit_run_event: Callable[..., Awaitable[None]],
    ):
        self.tools = tools
        self.tools_config = tools_config
        self.context_builder = context_builder
        self._config_path_getter = config_path_getter
        self._current_session_id_getter = current_session_id_getter
        self._current_run_id_getter = current_run_id_getter
        self._current_channel_getter = current_channel_getter
        self._current_external_chat_id_getter = current_external_chat_id_getter
        self._emit_run_event = emit_run_event
        self.servers = dict(tools_config.mcp_servers)
        self.tool_names: set[str] = set()
        self.stack: AsyncExitStack | None = None
        self.connected = False
        self.connecting = False
        self.connect_failures = 0
        self.retry_after = 0.0
        self._connect_lock = asyncio.Lock()

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        session_id = self._current_session_id_getter()
        run_id = self._current_run_id_getter()
        if session_id is None or run_id is None:
            return
        await self._emit_run_event(
            session_id,
            run_id,
            event_type,
            payload,
            channel=self._current_channel_getter(),
            external_chat_id=self._current_external_chat_id_getter(),
        )

    def sync_runtime_tools_context(self) -> None:
        """Expose connected MCP tools to context builders that support prompt summaries."""
        if not hasattr(self.context_builder, "set_runtime_mcp_tools"):
            return

        mcp_tools = sorted(
            [
                (tool.name, tool.description)
                for tool_name in self.tools.tool_names
                for tool in [self.tools.get(tool_name)]
                if tool is not None and is_mcp_tool_name(tool.name)
            ],
            key=lambda item: item[0],
        )
        self.context_builder.set_runtime_mcp_tools(mcp_tools)

    async def connect(self) -> None:
        """Connect configured MCP servers once and register their tools."""
        now = time.monotonic()
        if self.connected or self.connecting or not self.servers or now < self.retry_after:
            return

        async with self._connect_lock:
            now = time.monotonic()
            if self.connected or self.connecting or not self.servers or now < self.retry_after:
                return

            self.connecting = True
            stack: AsyncExitStack | None = None
            preexisting_tool_names = set(self.tools.tool_names)
            try:
                from ..tools.mcp import connect_mcp_servers

                stack = AsyncExitStack()
                await stack.__aenter__()
                await connect_mcp_servers(self.servers, self.tools, stack)
                self.stack = stack
                self.connected = True
                self.connect_failures = 0
                self.retry_after = 0.0
                self.tool_names = {
                    name for name in self.tools.tool_names
                    if is_mcp_tool_name(name) and name not in preexisting_tool_names
                }
                self.sync_runtime_tools_context()
                await self._emit_event(
                    MCP_CONNECTED_EVENT,
                    {
                        "server_count": len(self.servers),
                        "tool_names": sorted(self.tool_names),
                        "registered_tool_count": len(self.tool_names),
                    },
                )
                logger.info("agent.{} | tools={}", MCP_CONNECTED_EVENT, ", ".join(self.tools.tool_names))
            except BaseException as exc:
                for name in list(self.tools.tool_names):
                    if is_mcp_tool_name(name) and name not in preexisting_tool_names:
                        self.tools.unregister(name)
                self.connected = False
                self.tool_names.clear()
                self.connect_failures += 1
                retry_delay = min(
                    self.INITIAL_RETRY_BACKOFF_SECONDS * (2 ** (self.connect_failures - 1)),
                    self.MAX_RETRY_BACKOFF_SECONDS,
                )
                self.retry_after = time.monotonic() + retry_delay
                logger.error(
                    "agent.mcp.connect.error | error={} retry_in_s={} failures={}",
                    exc,
                    retry_delay,
                    self.connect_failures,
                )
                await self._emit_event(
                    MCP_CONNECTION_FAILED_EVENT,
                    {
                        "server_count": len(self.servers),
                        "error": str(exc),
                        "connect_failures": self.connect_failures,
                        "retry_in_seconds": retry_delay,
                    },
                )
                if stack is not None:
                    try:
                        await stack.aclose()
                    except Exception:
                        pass
                self.stack = None
            finally:
                self.connecting = False

    async def close(self) -> None:
        """Close any active MCP sessions and reset lifecycle flags."""
        async with self._connect_lock:
            stack = self.stack
            self.stack = None
            self.connected = False
            self.connecting = False
            for tool_name in list(self.tool_names):
                self.tools.unregister(tool_name)
            self.tool_names.clear()
            self.sync_runtime_tools_context()

        if stack is None:
            return

        try:
            await stack.aclose()
        except Exception as exc:
            logger.warning("agent.mcp.close.error | error={}", exc)

    async def reload_from_config(self) -> str:
        """Reload MCP settings from disk and reconnect MCP tools."""
        config_path = self._config_path_getter()
        if config_path is None:
            return _mcp_lifecycle_error_result(
                "MCP config path is unavailable.",
                category="missing_config_path",
            )

        loaded = Config.load(config_path)
        self.tools_config.mcp_servers_file = loaded.tools.mcp_servers_file
        self.tools_config.mcp_servers = dict(loaded.tools.mcp_servers)
        self.servers = dict(loaded.tools.mcp_servers)
        self.connect_failures = 0
        self.retry_after = 0.0

        await self.close()
        if not self.servers:
            return "MCP configuration reloaded. No MCP servers are configured now."

        await self.connect()
        if not self.connected:
            return "MCP configuration reloaded, but no MCP servers connected successfully."

        connected_tools = ", ".join(sorted(self.tool_names)) or "(none)"
        return f"MCP configuration reloaded. Connected tools: {connected_tools}"
