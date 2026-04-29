"""Shared MCP server settings helpers for Web settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .provider_settings import load_json_dict, write_json_dict
from .schema import Config, MCPServerConfig


class MCPSettingsError(Exception):
    """Base error for MCP settings operations."""


class MCPSettingsValidationError(MCPSettingsError):
    """Raised when a request is malformed."""


class MCPSettingsNotFound(MCPSettingsError):
    """Raised when an MCP server cannot be found."""


TRANSPORT_TYPES = ("stdio", "sse", "streamableHttp")


def _coerce_server_id(value: Any) -> str:
    server_id = str(value or "").strip()
    if not server_id:
        raise MCPSettingsValidationError("server_id is required")
    return server_id


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_string_list(value: Any, *, field: str, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]
    if not isinstance(value, list):
        raise MCPSettingsValidationError(f"{field} must be a list of strings")
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def _coerce_string_dict(value: Any, *, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MCPSettingsValidationError(f"{field} must be an object")
    return {str(key): str(item) for key, item in value.items() if str(key).strip()}


def _coerce_timeout(value: Any, *, default: int = 30) -> int:
    if value is None or value == "":
        return default
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise MCPSettingsValidationError("tool_timeout must be an integer") from exc
    if timeout < 1:
        raise MCPSettingsValidationError("tool_timeout must be greater than zero")
    return timeout


def _serialize_server(server_id: str, server: MCPServerConfig) -> dict[str, Any]:
    return {
        "id": server_id,
        "name": server_id,
        "type": server.type,
        "command": server.command,
        "args": list(server.args),
        "url": server.url,
        "tool_timeout": server.tool_timeout,
        "enabled_tools": list(server.enabled_tools),
        "env_configured": bool(server.env),
        "env_keys": sorted(server.env),
        "headers_configured": bool(server.headers),
        "headers_keys": sorted(server.headers),
    }


class MCPSettingsService:
    """Read and mutate MCP server settings on disk."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).expanduser().resolve()

    def _load_main_data(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise MCPSettingsNotFound(f"Config file not found: {self.config_path}")
        return load_json_dict(self.config_path)

    def _load_state(self) -> tuple[dict[str, Any], dict[str, MCPServerConfig], Path]:
        main_data = self._load_main_data()
        loaded = Config.from_json(self.config_path)
        mcp_path = Config.get_mcp_servers_file_path(self.config_path, loaded.tools)
        return main_data, dict(loaded.tools.mcp_servers), mcp_path

    def _persist_servers(self, main_data: dict[str, Any], servers: dict[str, MCPServerConfig]) -> Path:
        tools = main_data.setdefault("tools", {})
        if not isinstance(tools, dict):
            raise MCPSettingsValidationError("tools config must be an object")

        tools.pop("mcp_servers", None)
        tools.setdefault("mcp_servers_file", "mcp_servers.json")
        write_json_dict(self.config_path, main_data)

        mcp_path = Config.ensure_mcp_servers_file(self.config_path, main_data)
        write_json_dict(
            mcp_path,
            {name: server.model_dump() for name, server in sorted(servers.items())},
        )
        return mcp_path

    @staticmethod
    def _payload(servers: dict[str, MCPServerConfig], mcp_path: Path) -> dict[str, Any]:
        return {
            "servers": [_serialize_server(name, server) for name, server in sorted(servers.items())],
            "mcp_servers_file": str(mcp_path),
            "transport_types": list(TRANSPORT_TYPES),
            "restart_required": False,
        }

    def list_servers(self) -> dict[str, Any]:
        """Return configured MCP servers without leaking env/header values."""
        _, servers, mcp_path = self._load_state()
        return self._payload(servers, mcp_path)

    def upsert_server(self, server_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Create or update one MCP server."""
        normalized_id = _coerce_server_id(server_id)
        main_data, servers, _mcp_path = self._load_state()
        existing = servers.get(normalized_id)
        payload = existing.model_dump() if existing is not None else MCPServerConfig().model_dump()

        if "type" in body:
            transport_type = _coerce_text(body.get("type"))
            if transport_type not in TRANSPORT_TYPES:
                raise MCPSettingsValidationError("type must be one of stdio, sse, or streamableHttp")
            payload["type"] = transport_type

        for key in ("command", "url"):
            if key in body:
                payload[key] = _coerce_text(body.get(key))

        if "args" in body:
            payload["args"] = _coerce_string_list(body.get("args"), field="args")
        if "enabled_tools" in body:
            payload["enabled_tools"] = _coerce_string_list(
                body.get("enabled_tools"),
                field="enabled_tools",
                default=["*"],
            ) or ["*"]
        if "env" in body:
            payload["env"] = _coerce_string_dict(body.get("env"), field="env")
        if "headers" in body:
            payload["headers"] = _coerce_string_dict(body.get("headers"), field="headers")
        if "tool_timeout" in body:
            payload["tool_timeout"] = _coerce_timeout(body.get("tool_timeout"), default=30)

        transport_type = payload.get("type")
        command = _coerce_text(payload.get("command"))
        url = _coerce_text(payload.get("url"))
        if not transport_type:
            if command:
                payload["type"] = "stdio"
                transport_type = "stdio"
            elif url:
                payload["type"] = "streamableHttp"
                transport_type = "streamableHttp"
        if transport_type == "stdio" and not _coerce_text(payload.get("command")):
            raise MCPSettingsValidationError("stdio MCP servers require command")
        if transport_type in {"sse", "streamableHttp"} and not _coerce_text(payload.get("url")):
            raise MCPSettingsValidationError(f"{transport_type} MCP servers require url")

        servers[normalized_id] = MCPServerConfig(**payload)
        mcp_path = self._persist_servers(main_data, servers)
        payload_out = self._payload(servers, mcp_path)
        payload_out.update({"ok": True, "server": _serialize_server(normalized_id, servers[normalized_id]), "restart_required": True})
        return payload_out

    def remove_server(self, server_id: str) -> dict[str, Any]:
        """Remove one MCP server."""
        normalized_id = _coerce_server_id(server_id)
        main_data, servers, _mcp_path = self._load_state()
        if normalized_id not in servers:
            raise MCPSettingsNotFound(f"MCP server not found: {normalized_id}")
        servers.pop(normalized_id)
        mcp_path = self._persist_servers(main_data, servers)
        payload = self._payload(servers, mcp_path)
        payload.update({"ok": True, "server_id": normalized_id, "restart_required": True})
        return payload
