import json

import pytest

from opensprite.config import Config
from opensprite.config.provider_settings import (
    ProviderSettingsConflict,
    ProviderSettingsService,
    ProviderSettingsValidationError,
)


def _copy_config(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    return config_path


def test_provider_settings_connects_provider_without_leaking_api_key(tmp_path):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)

    result = service.connect_provider("openai", api_key="secret-key")

    providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
    listing = service.list_providers()
    connected = listing["connected"][0]

    assert result["provider"]["api_key_configured"] is True
    assert providers["openai"]["api_key"] == "secret-key"
    assert providers["openai"]["enabled"] is False
    assert providers["openai"]["model"] == ""
    assert connected["id"] == "openai"
    assert connected["provider"] == "openai"
    assert connected["api_key_configured"] is True
    assert "api_key" not in connected
    assert "openai" in {provider["id"] for provider in listing["available"]}


def test_provider_settings_allows_multiple_connections_for_same_provider(tmp_path):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)

    first = service.connect_provider("openai", api_key="first-key", name="Work")
    second = service.connect_provider("openai", api_key="second-key", name="Personal")
    service.select_model(second["provider"]["id"], "gpt-4.1-mini")

    providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
    models = service.list_models()

    assert first["provider"]["id"] == "openai"
    assert second["provider"]["id"] == "openai_personal"
    assert providers["openai"]["provider"] == "openai"
    assert providers["openai_personal"]["provider"] == "openai"
    assert providers["openai_personal"]["name"] == "Personal"
    assert providers["openai_personal"]["enabled"] is True
    assert providers["openai"]["enabled"] is False
    assert models["default_provider"] == "openai_personal"
    assert {provider["id"] for provider in models["providers"]} >= {"openai", "openai_personal"}


def test_provider_settings_select_model_updates_default_and_enabled_flags(tmp_path):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)

    service.connect_provider("openai", api_key="openai-key")
    service.connect_provider("openrouter", api_key="router-key")
    result = service.select_model("openrouter", "openai/gpt-4o-mini")

    main_config = json.loads(config_path.read_text(encoding="utf-8"))
    providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
    models = service.list_models()

    assert result == {
        "ok": True,
        "provider_id": "openrouter",
        "model": "openai/gpt-4o-mini",
        "restart_required": True,
    }
    assert main_config["llm"]["default"] == "openrouter"
    assert providers["openrouter"]["enabled"] is True
    assert providers["openrouter"]["model"] == "openai/gpt-4o-mini"
    assert providers["openai"]["enabled"] is False
    assert models["default_provider"] == "openrouter"
    assert models["active_model"] == "openai/gpt-4o-mini"


def test_provider_settings_updates_openrouter_request_options(tmp_path):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)

    service.connect_provider("openrouter", api_key="router-key")
    service.select_model("openrouter", "anthropic/claude-sonnet-4.6")
    result = service.update_provider_options(
        "openrouter",
        {
            "reasoning_enabled": True,
            "reasoning_effort": "high",
            "reasoning_max_tokens": 1024,
            "reasoning_exclude": True,
            "provider_sort": "throughput",
            "require_parameters": True,
        },
    )

    providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
    listing = service.list_providers()
    options = listing["connected"][0]["options"]

    assert result == {
        "ok": True,
        "provider_id": "openrouter",
        "options": {
            "reasoning_enabled": True,
            "reasoning_effort": "high",
            "reasoning_max_tokens": 1024,
            "reasoning_exclude": True,
            "provider_sort": "throughput",
            "require_parameters": True,
        },
        "restart_required": True,
    }
    assert providers["openrouter"]["reasoning_enabled"] is True
    assert providers["openrouter"]["provider_sort"] == "throughput"
    assert options == result["options"]


def test_provider_settings_rejects_openrouter_options_for_other_providers(tmp_path):
    service = ProviderSettingsService(_copy_config(tmp_path))

    service.connect_provider("openai", api_key="openai-key")

    with pytest.raises(ProviderSettingsValidationError):
        service.update_provider_options("openai", {"reasoning_enabled": True})


def test_provider_settings_rejects_unconnected_model_selection(tmp_path):
    service = ProviderSettingsService(_copy_config(tmp_path))

    with pytest.raises(ProviderSettingsConflict):
        service.select_model("openai", "gpt-4.1-mini")


def test_provider_settings_disconnects_active_provider_and_clears_default(tmp_path):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)

    service.connect_provider("openai", api_key="secret-key")
    service.select_model("openai", "gpt-4.1-mini")
    result = service.disconnect_provider("openai")

    main_config = json.loads(config_path.read_text(encoding="utf-8"))
    providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
    listing = service.list_providers()
    models = service.list_models()

    assert result == {"ok": True, "provider_id": "openai", "restart_required": True}
    assert main_config["llm"]["default"] is None
    assert providers == {}
    assert listing["connected"] == []
    assert {provider["id"] for provider in listing["available"]} >= {"openai", "openrouter", "minimax", "minimax-cn"}
    assert models["default_provider"] is None
    assert models["active_model"] == ""
