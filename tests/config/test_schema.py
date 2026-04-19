import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from opensprite.config.schema import ChannelsConfig, Config, MCPServerConfig, SearchConfig, SearchEmbeddingConfig, SpeechConfig, StorageConfig, ToolsConfig, VideoConfig, VisionConfig


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


def test_tools_config_provides_typed_tool_defaults():
    config = ToolsConfig()

    assert config.exec_tool.timeout == 60
    assert config.web_search.provider == "brave"
    assert config.web_search.max_results == 10
    assert config.web_fetch.max_chars == 50000
    assert config.web_fetch.timeout == 30
    assert config.web_fetch.prefer_trafilatura is True
    assert config.cron.default_timezone == "UTC"
    assert config.mcp_servers_file == "mcp_servers.json"


def test_tools_config_parses_nested_tool_sections_from_json_shape():
    config = ToolsConfig(
        **{
            "exec": {"timeout": 15},
            "web_search": {"provider": "jina", "max_results": 7},
            "web_fetch": {"max_chars": 1234, "timeout": 9, "prefer_trafilatura": False},
            "cron": {"default_timezone": "Asia/Taipei"},
        }
    )

    assert config.exec_tool.timeout == 15
    assert config.web_search.provider == "jina"
    assert config.web_search.max_results == 7
    assert config.web_fetch.max_chars == 1234
    assert config.web_fetch.timeout == 9
    assert config.web_fetch.prefer_trafilatura is False
    assert config.cron.default_timezone == "Asia/Taipei"


def test_vision_config_defaults_to_disabled_provider():
    config = VisionConfig()

    assert config.enabled is False
    assert config.provider == "minimax"
    assert config.api_key == ""


def test_speech_config_defaults_to_disabled_provider():
    config = SpeechConfig()

    assert config.enabled is False
    assert config.provider == "minimax"
    assert config.api_key == ""


def test_video_config_defaults_to_disabled_provider():
    config = VideoConfig()

    assert config.enabled is False
    assert config.provider == "minimax"
    assert config.api_key == ""


def test_vision_config_requires_api_key_and_model_when_enabled():
    with pytest.raises(ValidationError):
        VisionConfig(enabled=True)


def test_speech_config_requires_api_key_and_model_when_enabled():
    with pytest.raises(ValidationError):
        SpeechConfig(enabled=True)


def test_video_config_requires_api_key_and_model_when_enabled():
    with pytest.raises(ValidationError):
        VideoConfig(enabled=True)


def test_search_embedding_config_requires_model_when_enabled():
    with pytest.raises(ValidationError):
        SearchEmbeddingConfig(enabled=True)


def test_search_config_provides_embedding_defaults():
    config = SearchConfig()

    assert config.embedding.enabled is False
    assert config.embedding.provider == "openai"
    assert config.embedding.batch_size == 16
    assert config.embedding.candidate_count == 20
    assert config.embedding.candidate_strategy == "vector"
    assert config.embedding.vector_backend == "auto"
    assert config.embedding.vector_candidate_count == 50
    assert config.embedding.retry_failed_on_startup is False


def test_config_load_reads_search_from_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    search_path = tmp_path / "search.json"
    search_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "history_top_k": 7,
                "knowledge_top_k": 9,
                "embedding": {
                    "enabled": True,
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "candidate_count": 33,
                },
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
                "search_file": "search.json",
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)

    assert config.search.enabled is True
    assert config.search.history_top_k == 7
    assert config.search.knowledge_top_k == 9
    assert config.search.embedding.enabled is True
    assert config.search.embedding.model == "text-embedding-3-small"
    assert config.search.embedding.candidate_count == 33
    assert config.search_file == "search.json"


def test_config_load_defaults_agent_when_section_missing(tmp_path):
    path = tmp_path / "opensprite.json"
    path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(path)

    assert config.agent is not None
    assert config.agent.history_token_budget == 140000
    assert config.tools.exec_tool.timeout == 60
    assert config.tools.web_search.max_results == 10
    assert config.tools.web_fetch.timeout == 30
    assert config.tools.cron.default_timezone == "UTC"


def test_config_load_reads_channels_from_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    channels_path = tmp_path / "channels.json"
    channels_path.write_text(
        json.dumps(
            {
                "telegram": {"enabled": True, "token": "abc"},
                "console": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels_file": "channels.json",
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)

    assert config.channels.telegram["enabled"] is True
    assert config.channels.telegram["token"] == "abc"
    assert config.channels.console["enabled"] is False
    assert config.channels_file == "channels.json"


def test_config_save_writes_channels_to_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)
    config.channels.telegram["enabled"] = True
    config.channels.telegram["token"] = "secret"
    config.save(config_path)

    saved_main = json.loads(config_path.read_text(encoding="utf-8"))
    saved_channels = json.loads((tmp_path / "channels.json").read_text(encoding="utf-8"))

    assert saved_main["channels_file"] == "channels.json"
    assert "channels" not in saved_main
    assert saved_channels["telegram"]["enabled"] is True
    assert saved_channels["telegram"]["token"] == "secret"


def test_config_save_writes_search_to_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)
    config.search.enabled = True
    config.search.embedding.enabled = True
    config.search.embedding.model = "text-embedding-3-small"
    config.search.embedding.candidate_count = 77
    config.save(config_path)

    saved_main = json.loads(config_path.read_text(encoding="utf-8"))
    saved_search = json.loads((tmp_path / "search.json").read_text(encoding="utf-8"))

    assert saved_main["search_file"] == "search.json"
    assert "search" not in saved_main
    assert saved_search["enabled"] is True
    assert saved_search["embedding"]["enabled"] is True
    assert saved_search["embedding"]["model"] == "text-embedding-3-small"
    assert saved_search["embedding"]["candidate_count"] == 77


def test_config_load_merges_external_mcp_servers_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    mcp_path = tmp_path / "mcp_servers.json"
    mcp_path.write_text(
        json.dumps(
            {
                "external": {
                    "command": "npx",
                    "args": ["-y", "external-mcp"],
                }
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
                "tools": {
                    "mcp_servers_file": "mcp_servers.json",
                    "mcp_servers": {
                        "inline": {
                            "command": "npx",
                            "args": ["-y", "inline-mcp"],
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)

    assert sorted(config.tools.mcp_servers) == ["external", "inline"]
    assert config.tools.mcp_servers["external"].args == ["-y", "external-mcp"]
    assert config.tools.mcp_servers["inline"].args == ["-y", "inline-mcp"]


def test_config_save_writes_mcp_servers_to_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
                "tools": {
                    "mcp_servers_file": "mcp_servers.json",
                    "mcp_servers": {
                        "demo": {
                            "command": "npx",
                            "args": ["-y", "demo-mcp"],
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)
    config.save(config_path)

    saved_main = json.loads(config_path.read_text(encoding="utf-8"))
    saved_mcp = json.loads((tmp_path / "mcp_servers.json").read_text(encoding="utf-8"))

    assert saved_main["tools"]["mcp_servers_file"] == "mcp_servers.json"
    assert "mcp_servers" not in saved_main["tools"]
    assert saved_mcp == {
        "demo": {
            "type": None,
            "command": "npx",
            "args": ["-y", "demo-mcp"],
            "env": {},
            "url": "",
            "headers": {},
            "tool_timeout": 30,
            "enabled_tools": ["*"],
        }
    }


def test_copy_template_creates_external_mcp_servers_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    mcp_path = (tmp_path / "mcp_servers.json")

    assert template_data["tools"]["mcp_servers_file"] == "mcp_servers.json"
    assert mcp_path.exists()
    assert json.loads(mcp_path.read_text(encoding="utf-8")) == {}


def test_copy_template_creates_external_channels_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    channels_path = tmp_path / "channels.json"

    assert template_data["channels_file"] == "channels.json"
    assert channels_path.exists()
    assert json.loads(channels_path.read_text(encoding="utf-8")) == ChannelsConfig().model_dump()


def test_copy_template_creates_external_search_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    search_path = tmp_path / "search.json"

    assert template_data["search_file"] == "search.json"
    assert search_path.exists()
    assert json.loads(search_path.read_text(encoding="utf-8")) == SearchConfig().model_dump()
