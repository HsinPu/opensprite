import json

from opensprite.config import Config
from opensprite.config.media_settings import MediaSettingsService
from opensprite.config.provider_settings import ProviderSettingsService


def _copy_config(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    return config_path


def test_media_settings_lists_categories_without_secrets(tmp_path):
    config_path = _copy_config(tmp_path)
    ProviderSettingsService(config_path).connect_provider("openai", api_key="secret-key", name="Vision")

    payload = MediaSettingsService(config_path).list_media()

    assert set(payload["sections"]) == {"vision", "speech", "video"}
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
