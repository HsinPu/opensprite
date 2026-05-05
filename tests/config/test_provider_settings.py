import json
from types import SimpleNamespace

import pytest

from opensprite.config import Config
from opensprite.config import provider_settings
from opensprite.config.provider_settings import (
    ProviderSettingsConflict,
    ProviderSettingsService,
    ProviderSettingsValidationError,
)

_ORIGINAL_FETCH_OPENAI_COMPATIBLE_MODELS = provider_settings.fetch_openai_compatible_models
_ORIGINAL_FETCH_CODEX_MODELS = provider_settings.fetch_codex_models
_ORIGINAL_FETCH_OPENROUTER_IMAGE_MODELS = provider_settings.fetch_openrouter_image_models


@pytest.fixture(autouse=True)
def _disable_live_model_discovery(monkeypatch):
    monkeypatch.setattr(provider_settings, "fetch_openai_compatible_models", lambda _api_key, _base_url: [])
    monkeypatch.setattr(provider_settings, "fetch_openrouter_models", lambda: [])
    monkeypatch.setattr(provider_settings, "fetch_codex_models", lambda _app_home=None: [])


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


def test_provider_settings_connects_codex_without_api_key(tmp_path):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)

    result = service.connect_provider("openai-codex", api_key=None)
    service.select_model("openai-codex", "gpt-5.1-codex")

    providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
    listing = service.list_providers()
    models = service.list_models()
    connected = listing["connected"][0]

    assert result["provider"]["api_key_configured"] is False
    assert result["provider"]["requires_api_key"] is False
    assert providers["openai-codex"]["auth_type"] == "openai_codex_oauth"
    assert providers["openai-codex"].get("api_key", "") == ""
    assert providers["openai-codex"]["enabled"] is True
    assert connected["id"] == "openai-codex"
    assert connected["auth_type"] == "openai_codex_oauth"
    assert connected["requires_api_key"] is False
    assert models["default_provider"] == "openai-codex"
    assert models["providers"][0]["provider"] == "openai-codex"


def test_provider_settings_uses_discovered_provider_models(tmp_path, monkeypatch):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)
    monkeypatch.setattr(
        provider_settings,
        "fetch_openai_compatible_models",
        lambda api_key, base_url: ["live-model", "gpt-4.1-mini", "live-model"],
    )

    service.connect_provider("openai", api_key="openai-key")
    service.select_model("openai", "custom-selected-model")
    models = service.list_models()

    provider = models["providers"][0]
    assert provider["model_source"] == "live"
    assert provider["models"][:3] == ["custom-selected-model", "live-model", "gpt-4.1-mini"]


def test_provider_settings_falls_back_to_preset_models(tmp_path, monkeypatch):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)
    monkeypatch.setattr(provider_settings, "fetch_openrouter_models", lambda: [])

    service.connect_provider("openrouter", api_key="router-key")
    models = service.list_models()

    provider = models["providers"][0]
    assert provider["model_source"] == "preset"
    assert "openai/gpt-5.5" in provider["models"]


def test_provider_settings_uses_discovered_codex_models(tmp_path, monkeypatch):
    config_path = _copy_config(tmp_path)
    service = ProviderSettingsService(config_path)
    seen_app_homes = []

    def fake_fetch_codex_models(app_home=None):
        seen_app_homes.append(app_home)
        return ["gpt-5.1-codex-live", "gpt-5.1-codex"]

    monkeypatch.setattr(provider_settings, "fetch_codex_models", fake_fetch_codex_models)

    service.connect_provider("openai-codex", api_key=None)
    models = service.list_models()

    provider = models["providers"][0]
    assert seen_app_homes == [tmp_path]
    assert provider["model_source"] == "live"
    assert provider["models"][:2] == ["gpt-5.1-codex-live", "gpt-5.1-codex"]


def test_fetch_openai_compatible_models_probes_v1_fallback(monkeypatch):
    seen_urls = []

    def fake_read_json_url(url, *, headers=None):
        seen_urls.append((url, headers))
        if url == "https://example.test/v1/models":
            return {"data": [{"id": "first-live"}, {"id": ""}, {"id": "first-live"}, {"id": "second-live"}]}
        return {"data": []}

    monkeypatch.setattr(provider_settings, "fetch_openai_compatible_models", _ORIGINAL_FETCH_OPENAI_COMPATIBLE_MODELS)
    monkeypatch.setattr(provider_settings, "_read_json_url", fake_read_json_url)

    models = provider_settings.fetch_openai_compatible_models("secret", "https://example.test")

    assert models == ["first-live", "second-live"]
    assert seen_urls == [
        ("https://example.test/models", {"Accept": "application/json", "Authorization": "Bearer secret"}),
        ("https://example.test/v1/models", {"Accept": "application/json", "Authorization": "Bearer secret"}),
    ]


def test_fetch_codex_models_filters_and_sorts(monkeypatch):
    def fake_read_json_url(url, *, headers=None):
        return {
            "models": [
                {"slug": "hidden", "visibility": "hide", "priority": 1},
                {"slug": "unsupported", "supported_in_api": False, "priority": 2},
                {"slug": "later", "priority": 20},
                {"slug": "earlier", "priority": 10},
                {"slug": "earlier", "priority": 10},
            ]
        }

    monkeypatch.setattr(provider_settings, "fetch_codex_models", _ORIGINAL_FETCH_CODEX_MODELS)
    monkeypatch.setattr(provider_settings, "_read_json_url", fake_read_json_url)
    monkeypatch.setattr(
        "opensprite.auth.codex.load_or_refresh_codex_token",
        lambda app_home=None: SimpleNamespace(access_token="codex-token"),
    )

    assert provider_settings.fetch_codex_models(object()) == ["earlier", "later"]


def test_fetch_openrouter_image_models_filters_by_modality(monkeypatch):
    def fake_read_json_url(url, *, headers=None):
        return {
            "data": [
                {"id": "text-only", "architecture": {"input_modalities": ["text"]}},
                {"id": "vision-one", "architecture": {"input_modalities": ["text", "image"]}},
                {"id": "vision-one", "architecture": {"input_modalities": ["image"]}},
                {"id": "missing-modalities", "architecture": {}},
                {"id": "vision-two", "architecture": {"input_modalities": ["IMAGE", "text"]}},
            ]
        }

    monkeypatch.setattr(provider_settings, "fetch_openrouter_image_models", _ORIGINAL_FETCH_OPENROUTER_IMAGE_MODELS)
    monkeypatch.setattr(provider_settings, "_read_json_url", fake_read_json_url)

    assert provider_settings.fetch_openrouter_image_models() == ["vision-one", "vision-two"]


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
    assert models["providers"][0]["model_capabilities"]["openai/gpt-5.5"]["reasoning"] is True
    assert models["providers"][0]["model_capabilities"]["openai/gpt-5.5"]["recommended_options"] == {
        "reasoning_enabled": True,
        "reasoning_effort": "medium",
    }
    assert models["providers"][0]["options"] == {
        "reasoning_enabled": True,
        "reasoning_effort": "medium",
        "reasoning_max_tokens": None,
        "reasoning_exclude": False,
        "provider_sort": None,
        "require_parameters": False,
    }


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
    assert {provider["id"] for provider in listing["available"]} >= {"openai", "openrouter", "minimax"}
    assert "minimax-cn" not in {provider["id"] for provider in listing["available"]}
    assert models["default_provider"] is None
    assert models["active_model"] == ""
