import asyncio
import json

from opensprite.tools.mcp_config import ConfigureMCPTool


def _write_config(tmp_path, tools: dict | None = None) -> tuple:
    config_path = tmp_path / "opensprite.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
                "tools": tools or {"mcp_servers_file": "mcp_servers.json"},
            }
        ),
        encoding="utf-8",
    )
    return config_path, tmp_path / "mcp_servers.json"


def test_configure_mcp_lists_servers_from_external_file(tmp_path):
    config_path, mcp_path = _write_config(tmp_path)
    mcp_path.write_text(
        json.dumps({"demo": {"command": "npx", "args": ["-y", "demo-mcp"]}}),
        encoding="utf-8",
    )

    async def fake_reload() -> str:
        return "reloaded"

    tool = ConfigureMCPTool(
        config_path_resolver=lambda: config_path,
        reload_callback=fake_reload,
    )

    result = asyncio.run(tool.execute(action="list"))
    payload = json.loads(result)

    assert payload["mcp_servers_file"].endswith("mcp_servers.json")
    assert payload["servers"]["demo"]["command"] == "npx"


def test_configure_mcp_upserts_server_and_reloads(tmp_path):
    config_path, mcp_path = _write_config(tmp_path)
    reload_calls = []

    async def fake_reload() -> str:
        reload_calls.append("reload")
        return "reloaded now"

    tool = ConfigureMCPTool(
        config_path_resolver=lambda: config_path,
        reload_callback=fake_reload,
    )

    result = asyncio.run(
        tool.execute(
            action="upsert",
            server_name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
            enabled_tools=["*"],
            reload=True,
        )
    )

    saved = json.loads(mcp_path.read_text(encoding="utf-8"))

    assert "Added MCP server 'filesystem'" in result
    assert "reloaded now" in result
    assert reload_calls == ["reload"]
    assert saved["filesystem"]["command"] == "npx"
    assert saved["filesystem"]["type"] == "stdio"


def test_configure_mcp_defaults_remote_servers_to_streamable_http(tmp_path):
    config_path, mcp_path = _write_config(tmp_path)

    async def fake_reload() -> str:
        return "reloaded"

    tool = ConfigureMCPTool(
        config_path_resolver=lambda: config_path,
        reload_callback=fake_reload,
    )

    result = asyncio.run(
        tool.execute(
            action="upsert",
            server_name="remote",
            url="https://example.test/mcp",
            reload=False,
        )
    )

    saved = json.loads(mcp_path.read_text(encoding="utf-8"))

    assert "Added MCP server 'remote'" in result
    assert saved["remote"]["type"] == "streamableHttp"
    assert saved["remote"]["url"] == "https://example.test/mcp"


def test_configure_mcp_removes_server_without_reload(tmp_path):
    config_path, mcp_path = _write_config(tmp_path)
    mcp_path.write_text(
        json.dumps({"demo": {"command": "npx", "args": ["-y", "demo-mcp"]}}),
        encoding="utf-8",
    )

    async def fake_reload() -> str:
        raise AssertionError("reload should not run")

    tool = ConfigureMCPTool(
        config_path_resolver=lambda: config_path,
        reload_callback=fake_reload,
    )

    result = asyncio.run(tool.execute(action="remove", server_name="demo", reload=False))
    saved = json.loads(mcp_path.read_text(encoding="utf-8"))

    assert "Removed MCP server 'demo'" in result
    assert saved == {}
