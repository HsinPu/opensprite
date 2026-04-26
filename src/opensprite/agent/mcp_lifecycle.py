"""MCP connection lifecycle helpers for AgentLoop."""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Callable

from ..config import Config, ToolsConfig
from ..tools import ToolRegistry
from ..utils.log import logger


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
    ):
        self.tools = tools
        self.tools_config = tools_config
        self.context_builder = context_builder
        self._config_path_getter = config_path_getter
        self.servers = dict(tools_config.mcp_servers)
        self.tool_names: set[str] = set()
        self.stack: AsyncExitStack | None = None
        self.connected = False
        self.connecting = False
        self.connect_failures = 0
        self.retry_after = 0.0
        self._connect_lock = asyncio.Lock()

    def sync_runtime_tools_context(self) -> None:
        """Expose connected MCP tools to context builders that support prompt summaries."""
        if not hasattr(self.context_builder, "set_runtime_mcp_tools"):
            return

        mcp_tools = sorted(
            [
                (tool.name, tool.description)
                for tool_name in self.tools.tool_names
                for tool in [self.tools.get(tool_name)]
                if tool is not None and tool.name.startswith("mcp_")
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
                    if name.startswith("mcp_") and name not in preexisting_tool_names
                }
                self.sync_runtime_tools_context()
                logger.info("agent.mcp.connected | tools={}", ", ".join(self.tools.tool_names))
            except BaseException as exc:
                for name in list(self.tools.tool_names):
                    if name.startswith("mcp_") and name not in preexisting_tool_names:
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
            return "Error: MCP config path is unavailable."

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
