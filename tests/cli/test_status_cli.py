import json

from typer.testing import CliRunner

from opensprite.cli.commands import app
from opensprite.config import Config
from opensprite.config import provider_settings
from opensprite.config.provider_settings import ProviderSettingsService


runner = CliRunner()


def test_status_json_reports_credential_backed_provider_key(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_settings, "fetch_openrouter_models", lambda: [])
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    service = ProviderSettingsService(config_path)
    service.connect_provider("openrouter", api_key="sk-or-v1-test")
    service.select_model("openrouter", "qwen/qwen3.6-27b")

    result = runner.invoke(app, ["status", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["provider"]["name"] == "openrouter"
    assert payload["provider"]["api_key_configured"] is True

