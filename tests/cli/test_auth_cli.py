import json

from typer.testing import CliRunner

from opensprite.auth.codex import CodexToken, save_codex_token
from opensprite.cli.commands import app


runner = CliRunner()


def test_auth_status_json_reports_missing_codex_token(tmp_path):
    config_path = tmp_path / "opensprite.json"

    result = runner.invoke(app, ["auth", "status", "openai-codex", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["provider"] == "openai-codex"
    assert payload["configured"] is False
    assert payload["path"] == str(tmp_path / "auth" / "openai-codex.json")


def test_auth_status_json_reports_codex_token(tmp_path):
    config_path = tmp_path / "opensprite.json"
    save_codex_token(CodexToken(access_token="access-token", account_id="acct-1"), tmp_path)

    result = runner.invoke(app, ["auth", "status", "openai-codex", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["configured"] is True
    assert payload["account_id"] == "acct-1"


def test_auth_logout_removes_codex_token(tmp_path):
    config_path = tmp_path / "opensprite.json"
    token_path = save_codex_token(CodexToken(access_token="access-token"), tmp_path)

    result = runner.invoke(app, ["auth", "logout", "openai-codex", "--config", str(config_path)])

    assert result.exit_code == 0
    assert not token_path.exists()
    assert "Removed OpenAI Codex credentials" in result.stdout


def test_auth_login_placeholder_fails_clearly():
    result = runner.invoke(app, ["auth", "login", "openai-codex"])

    assert result.exit_code == 1
    assert "not implemented yet" in result.stderr
