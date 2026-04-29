"""Tool for safely managing MCP server configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Config, MCPServerConfig
from .base import Tool


ConfigPathResolver = Callable[[], Path | None]
ReloadMCPCallback = Callable[[], Awaitable[str]]


class ConfigureMCPTool(Tool):
    """Read and update MCP server configuration without exposing full config writes."""

    name = "configure_mcp"
    description = (
        "Inspect, add, update, or remove MCP server configuration in the dedicated MCP settings file. "
        "Use this when the user wants MCP servers configured instead of editing JSON files directly."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "upsert", "remove"],
                "description": "Required. Whether to list all servers, read one server, create/update one server, or remove one server.",
            },
            "server_name": {
                "type": "string",
                "description": "Server name to inspect, create/update, or remove.",
            },
            "transport_type": {
                "type": "string",
                "enum": ["stdio", "sse", "streamableHttp"],
                "description": "Optional transport type override for upsert.",
            },
            "command": {
                "type": "string",
                "description": "Command for stdio-based MCP servers.",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command arguments for stdio-based MCP servers.",
            },
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Environment variables for stdio-based MCP servers.",
            },
            "url": {
                "type": "string",
                "description": "URL for sse or streamableHttp MCP servers.",
            },
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "HTTP headers for remote MCP servers.",
            },
            "tool_timeout": {
                "type": "integer",
                "minimum": 1,
                "description": "Per-tool timeout in seconds.",
            },
            "enabled_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowed MCP tools list. Use ['*'] to allow all tools.",
            },
            "reload": {
                "type": "boolean",
                "description": "When true after upsert/remove, reload MCP in the current agent session immediately.",
            },
        },
        "required": ["action"],
    }

    def __init__(self, *, config_path_resolver: ConfigPathResolver, reload_callback: ReloadMCPCallback):
        self._config_path_resolver = config_path_resolver
        self._reload_callback = reload_callback

    def _get_config_path(self) -> Path | None:
        config_path = self._config_path_resolver()
        if config_path is None:
            return None
        return Path(config_path).expanduser().resolve()

    def _load_config(self) -> tuple[Config, Path, Path]:
        config_path = self._get_config_path()
        if config_path is None:
            raise RuntimeError("Active config path is unavailable")
        loaded = Config.load(config_path)
        mcp_path = Config.get_mcp_servers_file_path(config_path, loaded.tools)
        return loaded, config_path, mcp_path

    @staticmethod
    def _render_server(server: MCPServerConfig) -> dict[str, Any]:
        return server.model_dump()

    @staticmethod
    def _validate_upsert_payload(server_name: str, payload: dict[str, Any]) -> MCPServerConfig:
        if not server_name.strip():
            raise ValueError("server_name is required for upsert")

        transport_type = payload.get("type")
        command = str(payload.get("command", "") or "")
        url = str(payload.get("url", "") or "")

        if not transport_type:
            if command:
                payload["type"] = "stdio"
                transport_type = "stdio"
            elif url:
                payload["type"] = "streamableHttp"
                transport_type = "streamableHttp"

        if transport_type == "stdio" and not command:
            raise ValueError("stdio MCP servers require command")
        if transport_type in {"sse", "streamableHttp"} and not url:
            raise ValueError(f"{transport_type} MCP servers require url")
        if not transport_type and not command and not url:
            raise ValueError("upsert requires either command or url when creating a server")

        return MCPServerConfig(**payload)

    async def _execute(self, action: str, **kwargs: Any) -> str:
        try:
            loaded, config_path, mcp_path = self._load_config()
        except Exception as exc:
            return f"Error: {exc}"

        reload_now = bool(kwargs.get("reload", True))

        if action == "list":
            payload = {
                "config_path": str(config_path),
                "mcp_servers_file": str(mcp_path),
                "servers": {
                    name: self._render_server(server)
                    for name, server in sorted(loaded.tools.mcp_servers.items())
                },
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        server_name = str(kwargs.get("server_name", "") or "").strip()
        if action in {"get", "upsert", "remove"} and not server_name:
            return "Error: server_name is required for this action"

        if action == "get":
            server = loaded.tools.mcp_servers.get(server_name)
            if server is None:
                return f"Error: MCP server '{server_name}' not found"
            payload = {
                "config_path": str(config_path),
                "mcp_servers_file": str(mcp_path),
                "server_name": server_name,
                "server": self._render_server(server),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        if action == "remove":
            if server_name not in loaded.tools.mcp_servers:
                return f"Error: MCP server '{server_name}' not found"
            del loaded.tools.mcp_servers[server_name]
            loaded.save(config_path)
            message = f"Removed MCP server '{server_name}' from {mcp_path}."
            if reload_now:
                reload_result = await self._reload_callback()
                message = f"{message}\n{reload_result}"
            return message

        if action != "upsert":
            return f"Error: unsupported action '{action}'"

        existing = loaded.tools.mcp_servers.get(server_name)
        merged_payload = existing.model_dump() if existing is not None else MCPServerConfig().model_dump()
        field_map = {
            "transport_type": "type",
            "command": "command",
            "args": "args",
            "env": "env",
            "url": "url",
            "headers": "headers",
            "tool_timeout": "tool_timeout",
            "enabled_tools": "enabled_tools",
        }
        changed_fields: list[str] = []
        for input_key, model_key in field_map.items():
            if input_key not in kwargs or kwargs.get(input_key) is None:
                continue
            merged_payload[model_key] = kwargs[input_key]
            changed_fields.append(model_key)

        try:
            loaded.tools.mcp_servers[server_name] = self._validate_upsert_payload(server_name, merged_payload)
        except Exception as exc:
            return f"Error: {exc}"

        loaded.save(config_path)
        mode = "Updated" if existing is not None else "Added"
        fields_preview = ", ".join(changed_fields) if changed_fields else "no explicit fields"
        message = f"{mode} MCP server '{server_name}' in {mcp_path} ({fields_preview})."
        if reload_now:
            reload_result = await self._reload_callback()
            message = f"{message}\n{reload_result}"
        return message
