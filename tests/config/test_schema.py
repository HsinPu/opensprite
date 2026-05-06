import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from opensprite.auth.codex import CodexToken, save_codex_token
from opensprite.config.schema import (
    AgentConfig,
    ChannelsConfig,
    Config,
    LLMsConfig,
    LogConfig,
    MCPServerConfig,
    MessagesConfig,
    NetworkConfig,
    OcrConfig,
    ProviderConfig,
    SearchConfig,
    SearchEmbeddingConfig,
    SpeechConfig,
    StorageConfig,
    ToolsConfig,
    VideoConfig,
    VisionConfig,
)


def test_storage_config_accepts_supported_types():
    memory = StorageConfig(type="memory", path="memory.db")
    sqlite = StorageConfig(type="sqlite", path="sessions.db")

    assert memory.type == "memory"
    assert sqlite.type == "sqlite"


def test_storage_config_rejects_unsupported_file_type():
    with pytest.raises(ValidationError):
        StorageConfig(type="file", path="sessions.db")


def test_log_config_does_not_print_full_reasoning_by_default():
    assert LogConfig().log_reasoning_details is False


def test_network_config_defaults_to_loopback_no_proxy():
    config = NetworkConfig()

    assert config.http_proxy == ""
    assert config.https_proxy == ""
    assert config.no_proxy == "127.0.0.1,localhost"


def test_provider_config_supports_codex_oauth_shape():
    provider = ProviderConfig(
        provider="openai-codex",
        auth_type="openai_codex_oauth",
        api_mode="responses",
        model="gpt-5.1-codex",
    )

    assert provider.api_key == ""
    assert provider.auth_type == "openai_codex_oauth"
    assert provider.api_mode == "responses"


def test_codex_oauth_provider_is_configured_when_token_exists(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    providers_path = tmp_path / "llm.providers.json"
    providers_path.write_text(
        json.dumps(
            {
                "openai-codex": {
                    "provider": "openai-codex",
                    "auth_type": "openai_codex_oauth",
                    "api_mode": "responses",
                    "model": "gpt-5.1-codex",
                    "base_url": "https://chatgpt.com/backend-api/codex",
                    "enabled": True,
                }
            }
        ),
        encoding="utf-8",
    )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["llm"]["default"] = "openai-codex"
    config_path.write_text(json.dumps(data), encoding="utf-8")

    assert Config.from_json(config_path).is_llm_configured is False

    save_codex_token(CodexToken(access_token="codex-token"), tmp_path)

    assert Config.from_json(config_path).is_llm_configured is True


def test_agent_config_requires_template_backed_values():
    with pytest.raises(ValidationError):
        AgentConfig()


def test_config_load_reads_llm_providers_from_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    providers_path = tmp_path / "llm.providers.json"
    providers_path.write_text(
        json.dumps(
            {
                "openai": {
                    "api_key": "key-1",
                    "enabled": True,
                    "model": "gpt-4.1",
                    "base_url": "https://api.openai.com/v1",
                    "context_window_tokens": 128000,
                }
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "providers_file": "llm.providers.json",
                    "default": "openai",
                    "context_window_tokens": 32000,
                    "temperature": 0.7,
                    "max_tokens": 2048,
                },
                "storage": {"type": "memory", "path": "memory.db"},
                "network": {
                    "http_proxy": "http://proxy.local:8080",
                    "https_proxy": "http://proxy.local:8443",
                    "no_proxy": "127.0.0.1,localhost,.internal",
                },
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)

    assert isinstance(config.llm.providers["openai"], ProviderConfig)
    assert config.llm.providers["openai"].api_key == "key-1"
    assert config.llm.providers["openai"].model == "gpt-4.1"
    assert config.llm.providers["openai"].context_window_tokens == 128000
    assert config.llm.context_window_tokens == 32000
    assert config.llm.get_active().context_window_tokens == 128000
    assert config.llm.providers_file == "llm.providers.json"
    assert config.network.http_proxy == "http://proxy.local:8080"
    assert config.network.https_proxy == "http://proxy.local:8443"
    assert config.network.no_proxy == "127.0.0.1,localhost,.internal"


def test_config_load_creates_default_config_and_split_files(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    config = Config.load()

    app_home = tmp_path / ".opensprite"
    assert config.source_path == app_home / "opensprite.json"
    assert (app_home / "opensprite.json").exists()
    assert (app_home / "channels.json").exists()
    assert (app_home / "search.json").exists()
    assert (app_home / "media.json").exists()
    assert (app_home / "messages.json").exists()
    assert (app_home / "mcp_servers.json").exists()
    assert (app_home / "llm.providers.json").exists()


def test_llm_context_window_falls_back_to_top_level_setting():
    llm = LLMsConfig(
        **{
            **Config.packaged_llm_flat_dict(),
            "providers": {
                "openai": {
                    "api_key": "key-1",
                    "enabled": True,
                    "model": "gpt-4.1",
                    "base_url": "https://api.openai.com/v1",
                }
            },
            "default": "openai",
            "api_key": "",
            "model": "",
            "context_window_tokens": 32000,
        }
    )

    assert llm.get_active().context_window_tokens == 32000


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
    assert config.exec_tool.notify_on_exit is True
    assert config.exec_tool.notify_on_exit_empty_success is False
    assert config.web_search.provider == "duckduckgo"
    assert config.web_search.max_results == 25
    assert config.web_search.duckduckgo_max_pages == 10
    assert config.web_fetch.max_chars == 50000
    assert config.web_fetch.max_response_size == 5242880
    assert config.web_fetch.timeout == 30
    assert config.web_fetch.prefer_trafilatura is True
    assert config.cron.default_timezone == "UTC"
    assert config.permissions.enabled is True
    assert config.permissions.approval_mode is None
    assert config.permissions.approval_timeout_seconds == 300.0
    assert config.permissions.allowed_tools == ["*"]
    assert config.permissions.denied_tools == []
    assert config.mcp_servers_file == "mcp_servers.json"


def test_tools_config_parses_nested_tool_sections_from_json_shape():
    config = ToolsConfig(
        **{
            "exec": {
                "timeout": 15,
                "notify_on_exit": False,
                "notify_on_exit_empty_success": True,
            },
            "web_search": {"provider": "jina", "max_results": 7, "duckduckgo_max_pages": 3},
            "web_fetch": {
                "max_chars": 1234,
                "max_response_size": 2048,
                "timeout": 9,
                "prefer_trafilatura": False,
            },
            "cron": {"default_timezone": "Asia/Taipei"},
            "permissions": {
                "approval_mode": "ask",
                "approval_timeout_seconds": 12,
                "denied_tools": ["exec"],
                "denied_risk_levels": ["network"],
            },
        }
    )

    assert config.exec_tool.timeout == 15
    assert config.exec_tool.notify_on_exit is False
    assert config.exec_tool.notify_on_exit_empty_success is True
    assert config.web_search.provider == "jina"
    assert config.web_search.max_results == 7
    assert config.web_search.duckduckgo_max_pages == 3
    assert config.web_fetch.max_chars == 1234
    assert config.web_fetch.max_response_size == 2048
    assert config.web_fetch.timeout == 9
    assert config.web_fetch.prefer_trafilatura is False
    assert config.cron.default_timezone == "Asia/Taipei"
    assert config.permissions.approval_mode == "ask"
    assert config.permissions.approval_timeout_seconds == 12
    assert config.permissions.denied_tools == ["exec"]
    assert config.permissions.denied_risk_levels == ["network"]


def test_tools_config_rejects_unknown_approval_mode():
    with pytest.raises(ValidationError):
        ToolsConfig(**{"permissions": {"approval_mode": "sometimes"}})


def test_vision_config_defaults_to_disabled_provider():
    config = VisionConfig()

    assert config.enabled is False
    assert config.provider == "minimax"
    assert config.api_key == ""


def test_ocr_config_defaults_to_disabled_provider():
    config = OcrConfig()

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


def test_ocr_config_requires_api_key_and_model_when_enabled():
    with pytest.raises(ValidationError):
        OcrConfig(enabled=True)


def test_speech_config_requires_api_key_and_model_when_enabled():
    with pytest.raises(ValidationError):
        SpeechConfig(enabled=True)


def test_video_config_requires_api_key_and_model_when_enabled():
    with pytest.raises(ValidationError):
        VideoConfig(enabled=True)


def test_config_load_reads_media_from_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    media_path = tmp_path / "media.json"
    media_path.write_text(
        json.dumps(
            {
                "vision": {"enabled": True, "model": "vision-model", "api_key": "vision-key"},
                "ocr": {"enabled": True, "model": "ocr-model", "api_key": "ocr-key"},
                "speech": {"enabled": True, "model": "speech-model", "api_key": "speech-key"},
                "video": {"enabled": True, "model": "video-model", "api_key": "video-key"},
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
                "media_file": "media.json",
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)

    assert config.vision.enabled is True
    assert config.vision.model == "vision-model"
    assert config.ocr.enabled is True
    assert config.ocr.model == "ocr-model"
    assert config.speech.enabled is True
    assert config.speech.model == "speech-model"
    assert config.video.enabled is True
    assert config.video.model == "video-model"
    assert config.media_file == "media.json"


def test_config_load_reads_messages_from_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    messages_path = tmp_path / "messages.json"
    messages_path.write_text(
        json.dumps(
            {
                "agent": {"llm_not_configured": "請先設定模型"},
                "queue": {"stop_idle": "目前沒有任務"},
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
                "messages_file": "messages.json",
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)

    assert config.messages.agent.llm_not_configured == "請先設定模型"
    assert config.messages.queue.stop_idle == "目前沒有任務"
    assert config.messages.telegram.empty_message_fallback == MessagesConfig().telegram.empty_message_fallback
    assert config.messages_file == "messages.json"


def test_search_embedding_config_requires_model_when_enabled():
    with pytest.raises(ValidationError):
        SearchEmbeddingConfig(enabled=True)


def test_search_config_provides_embedding_defaults():
    config = SearchConfig()

    assert config.enabled is True
    assert config.backend == "sqlite"
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
                "backend": "sqlite",
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
    assert config.search.backend == "sqlite"
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

    assert config.llm.context_window_tokens == 1047576
    assert config.agent is not None
    assert config.agent.max_history == 300
    assert config.agent.history_token_budget == 200000
    assert config.agent.context_compaction_enabled is True
    assert config.agent.context_compaction_threshold_ratio == 0.9
    assert config.agent.context_compaction_min_messages == 8
    assert config.agent.context_compaction_strategy == "deterministic"
    assert config.agent.context_compaction_llm.max_tokens == 4096
    assert config.agent.worktree_sandbox_enabled is False
    assert config.tools.exec_tool.timeout == 60
    assert config.tools.web_search.max_results == 25
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

    assert config.channels.instances["telegram"]["enabled"] is True
    assert config.channels.instances["telegram"]["token"] == "abc"
    assert config.channels.instances["web"]["enabled"] is True
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
    config.channels.instances["telegram"]["enabled"] = True
    config.channels.instances["telegram"]["token"] = "secret"
    config.save(config_path)

    saved_main = json.loads(config_path.read_text(encoding="utf-8"))
    saved_channels = json.loads((tmp_path / "channels.json").read_text(encoding="utf-8"))

    assert saved_main["channels_file"] == "channels.json"
    assert "channels" not in saved_main
    assert saved_channels["instances"]["telegram"]["enabled"] is True
    assert saved_channels["instances"]["telegram"]["token"] == "secret"
    assert saved_channels["instances"]["web"]["enabled"] is True


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


def test_config_save_writes_media_to_external_file(tmp_path):
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
    config.vision.enabled = True
    config.vision.model = "vision-model"
    config.vision.api_key = "vision-key"
    config.ocr.enabled = True
    config.ocr.model = "ocr-model"
    config.ocr.api_key = "ocr-key"
    config.speech.enabled = True
    config.speech.model = "speech-model"
    config.speech.api_key = "speech-key"
    config.video.enabled = True
    config.video.model = "video-model"
    config.video.api_key = "video-key"
    config.save(config_path)

    saved_main = json.loads(config_path.read_text(encoding="utf-8"))
    saved_media = json.loads((tmp_path / "media.json").read_text(encoding="utf-8"))

    assert saved_main["media_file"] == "media.json"
    assert "vision" not in saved_main
    assert "ocr" not in saved_main
    assert "speech" not in saved_main
    assert "video" not in saved_main
    assert saved_media["vision"]["model"] == "vision-model"
    assert saved_media["ocr"]["model"] == "ocr-model"
    assert saved_media["speech"]["model"] == "speech-model"
    assert saved_media["video"]["model"] == "video-model"


def test_config_save_writes_messages_to_external_file(tmp_path):
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
    config.messages.agent.llm_not_configured = "請先設定 LLM"
    config.messages.queue.stop_cancelled = "已停止"
    config.save(config_path)

    saved_main = json.loads(config_path.read_text(encoding="utf-8"))
    saved_messages = json.loads((tmp_path / "messages.json").read_text(encoding="utf-8"))

    assert saved_main["messages_file"] == "messages.json"
    assert "messages" not in saved_main
    assert saved_messages["agent"]["llm_not_configured"] == "請先設定 LLM"
    assert saved_messages["queue"]["stop_cancelled"] == "已停止"


def test_config_save_writes_llm_providers_to_external_file(tmp_path):
    config_path = tmp_path / "opensprite.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "default": "openai",
                    "temperature": 0.7,
                    "max_tokens": 2048,
                },
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {"telegram": {"enabled": False}, "console": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    config = Config.from_json(config_path)
    config.llm.default = "openai"
    config.llm.context_window_tokens = 32000
    config.llm.providers["openai"] = ProviderConfig(
        api_key="new-key",
        enabled=True,
        model="gpt-4.1",
        base_url="https://api.openai.com/v1",
        context_window_tokens=128000,
    )
    config.save(config_path)

    saved_main = json.loads(config_path.read_text(encoding="utf-8"))
    saved_providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))

    assert saved_main["llm"]["providers_file"] == "llm.providers.json"
    assert saved_main["llm"]["context_window_tokens"] == 32000
    assert "providers" not in saved_main["llm"]
    assert saved_providers["openai"]["api_key"] == "new-key"
    assert saved_providers["openai"]["model"] == "gpt-4.1"
    assert saved_providers["openai"]["context_window_tokens"] == 128000


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
    assert json.loads(mcp_path.read_text(encoding="utf-8")) == Config.load_external_template_data("mcp_servers")


def test_copy_template_creates_external_channels_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    channels_path = tmp_path / "channels.json"

    assert template_data["channels_file"] == "channels.json"
    assert channels_path.exists()
    assert json.loads(channels_path.read_text(encoding="utf-8")) == Config.load_external_template_data("channels")


def test_copy_template_creates_external_search_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    search_path = tmp_path / "search.json"

    assert template_data["search_file"] == "search.json"
    assert search_path.exists()
    assert json.loads(search_path.read_text(encoding="utf-8")) == Config.load_external_template_data("search")


def test_copy_template_creates_external_media_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    media_path = tmp_path / "media.json"

    assert template_data["media_file"] == "media.json"
    assert media_path.exists()
    assert json.loads(media_path.read_text(encoding="utf-8")) == Config.load_external_template_data("media")


def test_copy_template_creates_external_messages_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    messages_path = tmp_path / "messages.json"

    assert template_data["messages_file"] == "messages.json"
    assert messages_path.exists()
    assert json.loads(messages_path.read_text(encoding="utf-8")) == Config.load_external_template_data("messages")


def test_copy_template_creates_external_llm_providers_file(tmp_path):
    config_path = tmp_path / "opensprite.json"

    Config.copy_template(config_path)

    template_data = json.loads(config_path.read_text(encoding="utf-8"))
    providers_path = tmp_path / "llm.providers.json"

    assert template_data["llm"]["providers_file"] == "llm.providers.json"
    assert template_data["llm"]["context_window_tokens"] == 1047576
    assert providers_path.exists()
    assert json.loads(providers_path.read_text(encoding="utf-8")) == Config.load_external_template_data("llm.providers")


def test_external_template_paths_exist():
    assert Config.external_template_path("channels").exists()
    assert Config.external_template_path("search").exists()
    assert Config.external_template_path("mcp_servers").exists()
    assert Config.external_template_path("media").exists()
    assert Config.external_template_path("messages").exists()
    assert Config.external_template_path("llm.providers").exists()
