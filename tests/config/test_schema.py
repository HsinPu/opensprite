import json

import pytest
from pydantic import ValidationError

from opensprite.config.schema import Config, MCPServerConfig, SearchConfig, SearchEmbeddingConfig, SpeechConfig, StorageConfig, ToolsConfig, VideoConfig, VisionConfig


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
    assert config.embedding.candidate_strategy == "fts"
    assert config.embedding.vector_backend == "exact"
    assert config.embedding.vector_candidate_count == 50
    assert config.embedding.retry_failed_on_startup is False


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
