import pytest
from pydantic import ValidationError

from opensprite.config.schema import MCPServerConfig, StorageConfig, ToolsConfig, VisionConfig


def test_storage_config_accepts_supported_types():
    memory = StorageConfig(type="memory", path="memory.db")
    sqlite = StorageConfig(type="sqlite", path="sessions.db")

    assert memory.type == "memory"
    assert sqlite.type == "sqlite"


def test_storage_config_rejects_unsupported_file_type():
    with pytest.raises(ValidationError):
        StorageConfig(type="file", path="sessions.db")


def test_tools_config_parses_mcp_server_entries():
    config = ToolsConfig(
        mcp_servers={
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                "enabled_tools": ["read_file"],
            }
        }
    )

    server = config.mcp_servers["filesystem"]

    assert isinstance(server, MCPServerConfig)
    assert server.command == "npx"
    assert server.args == ["-y", "@modelcontextprotocol/server-filesystem"]
    assert server.enabled_tools == ["read_file"]


def test_vision_config_defaults_to_disabled_provider():
    config = VisionConfig()

    assert config.enabled is False
    assert config.provider == "openai"
    assert config.api_key == ""
