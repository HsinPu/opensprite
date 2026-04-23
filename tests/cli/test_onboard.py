import json
from pathlib import Path

from opensprite.cli import onboard


def _patch_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(onboard.Path, "home", classmethod(lambda cls: home))


def test_run_onboard_creates_external_config_files(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard, "sync_templates", lambda app_home: [])

    result = onboard.run_onboard(interactive=False)

    app_home = tmp_path / ".opensprite"
    config_path = app_home / "opensprite.json"
    channels = json.loads((app_home / "channels.json").read_text(encoding="utf-8"))

    assert result.created_config is True
    assert result.channel_name == "web"
    assert config_path.exists()
    assert (app_home / "channels.json").exists()
    assert (app_home / "search.json").exists()
    assert (app_home / "media.json").exists()
    assert (app_home / "messages.json").exists()
    assert (app_home / "mcp_servers.json").exists()
    assert (app_home / "llm.providers.json").exists()
    assert channels["web"]["enabled"] is True


def test_run_onboard_interactive_persists_external_config_updates(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard, "sync_templates", lambda app_home: [])
    monkeypatch.setattr(onboard, "_require_tty", lambda: None)

    def fake_interactive(config_data: dict):
        updated = json.loads(json.dumps(config_data))
        updated["llm"].setdefault("providers", {})
        updated["llm"]["providers"].setdefault(
            "openai",
            {"api_key": "", "enabled": False, "model": "", "base_url": "https://api.openai.com/v1"},
        )
        updated["llm"]["default"] = "openai"
        updated["llm"]["providers"]["openai"]["enabled"] = True
        updated["llm"]["providers"]["openai"]["model"] = "gpt-4.1-mini"
        updated["llm"]["providers"]["openai"]["api_key"] = "secret-key"
        updated["channels"]["web"]["enabled"] = False
        updated["channels"]["telegram"]["enabled"] = True
        updated["channels"]["telegram"]["token"] = "telegram-secret"
        return updated

    monkeypatch.setattr(onboard, "_run_interactive_setup", fake_interactive)

    result = onboard.run_onboard(interactive=True)

    app_home = tmp_path / ".opensprite"
    config_path = app_home / "opensprite.json"
    main_config = json.loads(config_path.read_text(encoding="utf-8"))
    providers = json.loads((app_home / "llm.providers.json").read_text(encoding="utf-8"))
    channels = json.loads((app_home / "channels.json").read_text(encoding="utf-8"))

    assert result.llm_provider == "openai"
    assert result.llm_model == "gpt-4.1-mini"
    assert result.llm_api_key_configured is True
    assert result.channel_name == "telegram"
    assert result.channel_token_configured is True
    assert main_config["llm"]["providers_file"] == "llm.providers.json"
    assert "providers" not in main_config["llm"]
    assert providers["openai"]["api_key"] == "secret-key"
    assert providers["openai"]["model"] == "gpt-4.1-mini"
    assert channels["telegram"]["enabled"] is True
    assert channels["telegram"]["token"] == "telegram-secret"


def test_run_onboard_refresh_preserves_existing_external_config(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(onboard, "sync_templates", lambda app_home: [])

    app_home = tmp_path / ".opensprite"
    app_home.mkdir(parents=True, exist_ok=True)
    (app_home / "opensprite.json").write_text(
        json.dumps(
            {
                "llm": {
                    "providers_file": "llm.providers.json",
                    "default": "openai",
                    "temperature": 0.7,
                    "max_tokens": 8192,
                },
                "storage": {"type": "sqlite", "path": "~/.opensprite/data/sessions.db"},
                "channels_file": "channels.json",
                "search_file": "search.json",
                "media_file": "media.json",
                "messages_file": "messages.json",
                "log": {"enabled": True, "retention_days": 365, "level": "INFO", "log_system_prompt": True, "log_system_prompt_lines": 0},
                "tools": {"mcp_servers_file": "mcp_servers.json"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (app_home / "llm.providers.json").write_text(
        json.dumps({"openai": {"api_key": "keep-me", "enabled": True, "model": "gpt-4.1", "base_url": "https://api.openai.com/v1"}}, indent=2),
        encoding="utf-8",
    )
    (app_home / "channels.json").write_text(
        json.dumps({"telegram": {"enabled": True, "token": "keep-token"}, "console": {"enabled": True}}, indent=2),
        encoding="utf-8",
    )
    (app_home / "search.json").write_text(json.dumps({"enabled": True, "history_top_k": 9, "knowledge_top_k": 11, "embedding": {"enabled": False, "provider": "openai", "api_key": "", "model": "", "base_url": None, "batch_size": 16, "candidate_count": 20, "candidate_strategy": "vector", "vector_backend": "auto", "vector_candidate_count": 50, "retry_failed_on_startup": False}}, indent=2), encoding="utf-8")
    (app_home / "media.json").write_text(json.dumps({"vision": {"enabled": True, "provider": "minimax", "api_key": "v", "model": "vm", "base_url": None}, "speech": {"enabled": False, "provider": "minimax", "api_key": "", "model": "", "base_url": None}, "video": {"enabled": False, "provider": "minimax", "api_key": "", "model": "", "base_url": None}}, indent=2), encoding="utf-8")
    (app_home / "messages.json").write_text(json.dumps({"agent": {"empty_response_fallback": "fallback", "llm_not_configured": "請先設定"}, "queue": {"stop_cancelled": "stop", "stop_idle": "idle", "reset_done": "reset", "reset_done_with_cancelled": "reset-stop"}, "cron": {"help_text": "help", "unavailable": "unavailable"}, "telegram": {"empty_message_fallback": "tg-fallback"}}, indent=2), encoding="utf-8")
    (app_home / "mcp_servers.json").write_text("{}\n", encoding="utf-8")

    result = onboard.run_onboard(interactive=False)

    providers = json.loads((app_home / "llm.providers.json").read_text(encoding="utf-8"))
    channels = json.loads((app_home / "channels.json").read_text(encoding="utf-8"))
    messages = json.loads((app_home / "messages.json").read_text(encoding="utf-8"))

    assert result.created_config is False
    assert providers["openai"]["api_key"] == "keep-me"
    assert channels["telegram"]["token"] == "keep-token"
    assert messages["agent"]["llm_not_configured"] == "請先設定"
