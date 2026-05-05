import json

import pytest

from opensprite.config import Config
from opensprite.config import media_settings
from opensprite.config.media_settings import MediaSettingsService
from opensprite.config.provider_settings import ProviderSettingsService


@pytest.fixture(autouse=True)
def _disable_live_media_discovery(monkeypatch):
    monkeypatch.setattr(media_settings, "fetch_openrouter_image_models", lambda: [])


def test_media_settings_uses_discovered_openrouter_image_models(tmp_path, monkeypatch):
    config_path = _copy_config(tmp_path)
    monkeypatch.setattr(
        media_settings,
        "fetch_openrouter_image_models",
        lambda: ["google/gemini-live-vision", "anthropic/claude-sonnet-4.6"],
    )
    ProviderSettingsService(config_path).connect_provider("openrouter", api_key="secret-key", name="OpenRouter")

    payload = MediaSettingsService(config_path).list_media()
    provider = next(entry for entry in payload["providers"] if entry["id"] == "openrouter")

    assert provider["media_model_source"] == "live"
    assert provider["media_models"]["vision"][:3] == [
        "google/gemini-live-vision",
        "anthropic/claude-sonnet-4.6",
        "google/gemini-3-flash-preview",
    ]
    assert provider["media_models"]["ocr"][:3] == [
        "baidu/qianfan-ocr-fast:free",
        "google/gemini-3-flash-preview",
        "openai/gpt-5.5",
    ]
    assert "google/gemini-live-vision" in provider["media_models"]["ocr"]


def test_media_settings_falls_back_to_preset_image_models(tmp_path, monkeypatch):
    config_path = _copy_config(tmp_path)
    monkeypatch.setattr(media_settings, "fetch_openrouter_image_models", lambda: [])
    ProviderSettingsService(config_path).connect_provider("openrouter", api_key="secret-key", name="OpenRouter")

    payload = MediaSettingsService(config_path).list_media()
    provider = next(entry for entry in payload["providers"] if entry["id"] == "openrouter")

    assert provider["media_model_source"] == "preset"
    assert provider["media_models"]["vision"][:2] == [
        "google/gemini-3-flash-preview",
        "anthropic/claude-sonnet-4.6",
    ]


def _copy_config(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    return config_path


def test_media_settings_lists_categories_without_secrets(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("openai", api_key="secret-key", name="Vision")

    payload = MediaSettingsService(config_path).list_media()

    assert set(payload["sections"]) == {"vision", "ocr", "speech", "video"}
    assert payload["providers"][0]["id"] == "openai"
    assert "api_key" not in payload["providers"][0]
    assert "api_key" not in payload["sections"]["vision"]


def test_media_settings_updates_media_file_from_provider_connection(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("openai", api_key="secret-key", name="Vision")
    service = MediaSettingsService(config_path)

    result = service.update_media("vision", enabled=True, provider_id="openai", model="gpt-4o-mini")

    media = json.loads((tmp_path / "media.json").read_text(encoding="utf-8"))
    section = result["media"]["sections"]["vision"]

    assert result["restart_required"] is True
    assert media["vision"]["enabled"] is True
    assert media["vision"]["provider"] == "openai"
    assert media["vision"]["api_key"] == "secret-key"
    assert media["vision"]["model"] == "gpt-4o-mini"
    assert section["enabled"] is True
    assert section["provider_id"] == "openai"
    assert section["api_key_configured"] is True


def test_media_settings_lists_minimax_vision_models_separately(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("minimax", api_key="secret-key", name="MiniMax Global")

    payload = MediaSettingsService(config_path).list_media()
    provider = next(entry for entry in payload["providers"] if entry["id"] == "minimax")

    assert provider["models"][:3] == ["MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1"]
    assert provider["media_models"] == {"vision": ["MiniMax-VL-01"], "ocr": ["MiniMax-VL-01"]}


def test_media_settings_lists_openrouter_image_models_separately(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("openrouter", api_key="secret-key", name="OpenRouter")

    payload = MediaSettingsService(config_path).list_media()
    provider = next(entry for entry in payload["providers"] if entry["id"] == "openrouter")

    assert provider["media_models"]["vision"][:2] == [
        "google/gemini-3-flash-preview",
        "anthropic/claude-sonnet-4.6",
    ]
    assert provider["media_models"]["ocr"][:2] == [
        "baidu/qianfan-ocr-fast:free",
        "google/gemini-3-flash-preview",
    ]


def test_media_settings_lists_openai_image_models_separately(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("openai", api_key="secret-key", name="OpenAI")

    payload = MediaSettingsService(config_path).list_media()
    provider = next(entry for entry in payload["providers"] if entry["id"] == "openai")

    assert provider["media_models"] == {
        "vision": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-chat"],
        "ocr": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
    }


def test_media_settings_can_save_minimax_vision_model(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("minimax", api_key="secret-key")
    service = MediaSettingsService(config_path)

    result = service.update_media("vision", enabled=True, provider_id="minimax", model="MiniMax-VL-01")

    media = json.loads((tmp_path / "media.json").read_text(encoding="utf-8"))
    assert media["vision"]["provider"] == "minimax"
    assert media["vision"]["base_url"] == "https://api.minimax.io/v1"
    assert result["media"]["sections"]["vision"]["provider_id"] == "minimax"


def test_media_settings_can_save_ocr_model_separately_from_vision(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("openai", api_key="openai-key")
    ProviderSettingsService(config_path).connect_provider("minimax", api_key="minimax-key")
    service = MediaSettingsService(config_path)

    service.update_media("vision", enabled=True, provider_id="openai", model="gpt-4o-mini")
    result = service.update_media("ocr", enabled=True, provider_id="minimax", model="MiniMax-VL-01")

    media = json.loads((tmp_path / "media.json").read_text(encoding="utf-8"))
    assert media["vision"]["provider"] == "openai"
    assert media["vision"]["model"] == "gpt-4o-mini"
    assert media["ocr"]["provider"] == "minimax"
    assert media["ocr"]["model"] == "MiniMax-VL-01"
    assert result["media"]["sections"]["ocr"]["provider_id"] == "minimax"
