"""MCP client helpers that expose external MCP tools as OpenSprite tools."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from ..config.schema import MCPServerConfig
from ..utils.log import logger
from .base import Tool
from .registry import ToolRegistry


def _extract_nullable_branch(options: Any) -> tuple[dict[str, Any], bool] | None:
    """Return the single non-null branch for nullable unions."""
    if not isinstance(options, list):
        return None

    non_null: list[dict[str, Any]] = []
    saw_null = False
    for option in options:
        if not isinstance(option, dict):
            return None
        if option.get("type") == "null":
            saw_null = True
            continue
        non_null.append(option)

    if saw_null and len(non_null) == 1:
        return non_null[0], True
    return None


def _normalize_schema_for_openai(schema: Any) -> dict[str, Any]:
    """Normalize nullable JSON Schema patterns for tool definitions."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    normalized = dict(schema)

    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if "null" in raw_type and len(non_null) == 1:
            normalized["type"] = non_null[0]
            normalized["nullable"] = True

    for key in ("oneOf", "anyOf"):
        nullable_branch = _extract_nullable_branch(normalized.get(key))
        if nullable_branch is not None:
            branch, _ = nullable_branch
            merged = {k: v for k, v in normalized.items() if k != key}
            merged.update(branch)
            normalized = merged
            normalized["nullable"] = True
            break

    if "properties" in normalized and isinstance(normalized["properties"], dict):
        normalized["properties"] = {
            name: _normalize_schema_for_openai(prop) if isinstance(prop, dict) else prop
            for name, prop in normalized["properties"].items()
        }

    if "items" in normalized and isinstance(normalized["items"], dict):
        normalized["items"] = _normalize_schema_for_openai(normalized["items"])

    if normalized.get("type") != "object":
        return normalized

    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    return normalized


class _ReparentAsyncExitStack:
    """Close a manually-managed AsyncExitStack when the parent AsyncExitStack unwinds."""

    def __init__(self, inner: AsyncExitStack) -> None:
        self._inner = inner

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        await self._inner.aclose()
        return False


def _http_url_transport_attempts(_url: str) -> list[str]:
    return ["streamableHttp", "sse"]


def _use_implicit_http_transport_fallback(cfg: MCPServerConfig) -> bool:
    """When type is omitted and only a URL is given, try streamable HTTP before SSE."""
    return cfg.type is None and bool(cfg.url) and not (cfg.command or "").strip()


def _mcp_connect_timeout_seconds(cfg: MCPServerConfig) -> int:
    return max(1, int(cfg.tool_timeout or 30))


async def _open_mcp_transport(
    stack: AsyncExitStack,
    cfg: MCPServerConfig,
    transport_type: str,
    httpx: Any,
) -> tuple[Any, Any]:
    from mcp import StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    if transport_type == "stdio":
        params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env or None)
        read, write = await stack.enter_async_context(stdio_client(params))
        return read, write
    if transport_type == "sse":

        def httpx_client_factory(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
        ) -> httpx.AsyncClient:
            merged_headers = {
                "Accept": "application/json, text/event-stream",
                **(cfg.headers or {}),
                **(headers or {}),
            }
            return httpx.AsyncClient(
                headers=merged_headers or None,
                follow_redirects=True,
                timeout=timeout or _mcp_connect_timeout_seconds(cfg),
                auth=auth,
            )

        read, write = await stack.enter_async_context(
            sse_client(cfg.url, httpx_client_factory=httpx_client_factory)
        )
        return read, write
    if transport_type == "streamableHttp":
        http_client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=cfg.headers or None,
                follow_redirects=True,
                timeout=_mcp_connect_timeout_seconds(cfg),
            )
        )
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(cfg.url, http_client=http_client)
        )
        return read, write
    raise ValueError(f"unsupported transport type {transport_type!r}")


class MCPToolWrapper(Tool):
    """Wrap one MCP tool as an OpenSprite-native tool."""

    def __init__(self, session: Any, server_name: str, tool_def: Any, tool_timeout: int = 30):
        self._session = session
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name
        raw_schema = getattr(tool_def, "inputSchema", None) or {"type": "object", "properties": {}}
        self._parameters = _normalize_schema_for_openai(raw_schema)
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def _execute(self, **kwargs: Any) -> str:
        from mcp import types

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
            return f"(MCP tool call timed out after {self._tool_timeout}s)"
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP tool '{}' was cancelled by server/SDK", self._name)
            return "(MCP tool call was cancelled)"
        except Exception as exc:
            logger.exception(
                "MCP tool '{}' failed: {}: {}",
                self._name,
                type(exc).__name__,
                exc,
            )
            return f"(MCP tool call failed: {type(exc).__name__})"

        parts: list[str] = []
        for block in getattr(result, "content", []):
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts) or "(no output)"


def _register_mcp_server_tools(registry: ToolRegistry, name: str, cfg: MCPServerConfig, session: Any, tools: Any) -> int:
    enabled_tools = set(cfg.enabled_tools)
    allow_all_tools = "*" in enabled_tools
    registered_count = 0
    matched_enabled_tools: set[str] = set()
    available_raw_names = [tool_def.name for tool_def in tools.tools]
    available_wrapped_names = [f"mcp_{name}_{tool_def.name}" for tool_def in tools.tools]

    for tool_def in tools.tools:
        wrapped_name = f"mcp_{name}_{tool_def.name}"
        if (
            not allow_all_tools
            and tool_def.name not in enabled_tools
            and wrapped_name not in enabled_tools
        ):
            logger.debug(
                "MCP: skipping tool '{}' from server '{}' (not in enabled_tools)",
                wrapped_name,
                name,
            )
            continue

        wrapper = MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.tool_timeout)
        registry.register(wrapper)
        registered_count += 1
        if enabled_tools:
            if tool_def.name in enabled_tools:
                matched_enabled_tools.add(tool_def.name)
            if wrapped_name in enabled_tools:
                matched_enabled_tools.add(wrapped_name)

    if enabled_tools and not allow_all_tools:
        unmatched_enabled_tools = sorted(enabled_tools - matched_enabled_tools)
        if unmatched_enabled_tools:
            logger.warning(
                "MCP server '{}': enabled_tools entries not found: {}. Available raw names: {}. Available wrapped names: {}",
                name,
                ", ".join(unmatched_enabled_tools),
                ", ".join(available_raw_names) or "(none)",
                ", ".join(available_wrapped_names) or "(none)",
            )

    return registered_count


async def connect_mcp_servers(
    mcp_servers: dict[str, MCPServerConfig],
    registry: ToolRegistry,
    stack: AsyncExitStack,
) -> None:
    """Connect to configured MCP servers and register their tools."""
    import httpx
    from mcp import ClientSession

    for name, cfg in mcp_servers.items():
        timeout_seconds = _mcp_connect_timeout_seconds(cfg)
        try:
            if not cfg.command and not cfg.url:
                logger.warning("MCP server '{}': no command or url configured, skipping", name)
                continue

            if _use_implicit_http_transport_fallback(cfg):
                ordered = _http_url_transport_attempts(cfg.url)
                last_exc: Exception | None = None
                for transport_type in ordered:
                    attempt_stack = AsyncExitStack()
                    try:
                        read, write = await asyncio.wait_for(
                            _open_mcp_transport(attempt_stack, cfg, transport_type, httpx),
                            timeout=timeout_seconds,
                        )
                        session = await attempt_stack.enter_async_context(ClientSession(read, write))
                        await asyncio.wait_for(session.initialize(), timeout=timeout_seconds)
                        tools = await asyncio.wait_for(session.list_tools(), timeout=timeout_seconds)
                        await stack.enter_async_context(_ReparentAsyncExitStack(attempt_stack))
                        if transport_type != ordered[0]:
                            logger.info(
                                "MCP server '{}': implicit transport '{}' failed earlier; using '{}'",
                                name,
                                ordered[0],
                                transport_type,
                            )
                        registered_count = _register_mcp_server_tools(registry, name, cfg, session, tools)
                        logger.info("MCP server '{}': connected, {} tools registered", name, registered_count)
                        break
                    except Exception as exc:
                        last_exc = exc
                        logger.warning(
                            "MCP server '{}': transport '{}' connect failed ({}: {})",
                            name,
                            transport_type,
                            type(exc).__name__,
                            exc,
                        )
                        await attempt_stack.aclose()
                else:
                    logger.error(
                        "MCP server '{}': failed after trying transports {}: {}",
                        name,
                        ", ".join(ordered),
                        last_exc,
                    )
                continue

            transport_type = cfg.type
            if not transport_type:
                if cfg.command:
                    transport_type = "stdio"
                elif cfg.url:
                    transport_type = "streamableHttp"
                else:
                    continue

            if transport_type not in {"stdio", "sse", "streamableHttp"}:
                logger.warning("MCP server '{}': unknown transport type '{}'", name, transport_type)
                continue

            read, write = await asyncio.wait_for(
                _open_mcp_transport(stack, cfg, transport_type, httpx),
                timeout=timeout_seconds,
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=timeout_seconds)
            tools = await asyncio.wait_for(session.list_tools(), timeout=timeout_seconds)
            registered_count = _register_mcp_server_tools(registry, name, cfg, session, tools)
            logger.info("MCP server '{}': connected, {} tools registered", name, registered_count)
        except Exception as exc:
            logger.error("MCP server '{}': failed to connect: {}", name, exc)


__all__ = ["MCPToolWrapper", "connect_mcp_servers"]
