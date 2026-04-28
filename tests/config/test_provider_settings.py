import json

import pytest

from opensprite.config import Config
from opensprite.config.provider_settings import ProviderSettingsConflict, ProviderSettingsService


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
    assert connected["api_key_configured"] is True
    assert "api_key" not in connected
    assert "openai" not in {provider["id"] for provider in listing["available"]}


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
    assert {provider["id"] for provider in listing["available"]} >= {"openai", "openrouter", "minimax"}
    assert models["default_provider"] is None
    assert models["active_model"] == ""
