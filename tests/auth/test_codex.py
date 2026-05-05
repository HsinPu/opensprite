import time
import base64
import json

import pytest

import opensprite.auth.codex as codex_module
from opensprite.auth.codex import (
    CodexAuthError,
    CodexToken,
    access_token_is_expiring,
    codex_device_login,
    delete_codex_token,
    get_codex_status,
    load_codex_token,
    load_or_refresh_codex_token,
    refresh_codex_token,
    save_codex_token,
)


def _jwt_with_exp(expires_at: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expires_at}).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    responses = []
    calls = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_codex_token_store_roundtrip(tmp_path):
    expires_at = int(time.time()) + 3600
    path = save_codex_token(
        CodexToken(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=expires_at,
            account_id="acct-1",
            scopes=("model.completion",),
        ),
        tmp_path,
    )

    token = load_codex_token(tmp_path)
    status = get_codex_status(tmp_path)

    assert path == tmp_path / "auth" / "openai-codex.json"
    assert token.access_token == "access-token"
    assert token.refresh_token == "refresh-token"
    assert token.scopes == ("model.completion",)
    assert status.configured is True
    assert status.expired is False
    assert status.account_id == "acct-1"


def test_codex_status_reports_missing_token(tmp_path):
    status = get_codex_status(tmp_path)

    assert status.configured is False
    assert status.path == tmp_path / "auth" / "openai-codex.json"


def test_codex_token_store_rejects_missing_access_token(tmp_path):
    path = tmp_path / "auth" / "openai-codex.json"
    path.parent.mkdir()
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(CodexAuthError, match="missing access_token"):
        load_codex_token(tmp_path)


def test_codex_logout_removes_token(tmp_path):
    save_codex_token(CodexToken(access_token="access-token"), tmp_path)

    assert delete_codex_token(tmp_path) is True
    assert delete_codex_token(tmp_path) is False


def test_access_token_expiry_detects_jwt_expiration():
    assert access_token_is_expiring(_jwt_with_exp(int(time.time()) + 10), 120) is True
    assert access_token_is_expiring(_jwt_with_exp(int(time.time()) + 3600), 120) is False


def test_refresh_codex_token_rotates_and_saves_token(tmp_path, monkeypatch):
    refreshed_access = _jwt_with_exp(int(time.time()) + 3600)
    _FakeClient.responses = [_Response(200, {"access_token": refreshed_access, "refresh_token": "refresh-2"})]
    _FakeClient.calls = []
    monkeypatch.setattr(codex_module.httpx, "Client", _FakeClient)

    token = refresh_codex_token(CodexToken(access_token="old", refresh_token="refresh-1"), app_home=tmp_path)

    assert token.access_token == refreshed_access
    assert token.refresh_token == "refresh-2"
    assert load_codex_token(tmp_path).access_token == refreshed_access
    assert _FakeClient.calls[0][1]["data"]["grant_type"] == "refresh_token"


def test_load_or_refresh_codex_token_refreshes_expiring_token(tmp_path, monkeypatch):
    expiring_access = _jwt_with_exp(int(time.time()) + 10)
    refreshed_access = _jwt_with_exp(int(time.time()) + 3600)
    save_codex_token(CodexToken(access_token=expiring_access, refresh_token="refresh-1"), tmp_path)
    _FakeClient.responses = [_Response(200, {"access_token": refreshed_access, "refresh_token": "refresh-2"})]
    _FakeClient.calls = []
    monkeypatch.setattr(codex_module.httpx, "Client", _FakeClient)

    token = load_or_refresh_codex_token(tmp_path)

    assert token.access_token == refreshed_access


def test_codex_device_login_saves_exchanged_tokens(tmp_path, monkeypatch):
    access_token = _jwt_with_exp(int(time.time()) + 3600)
    _FakeClient.responses = [
        _Response(200, {"user_code": "ABCD", "device_auth_id": "device-1", "interval": 3}),
        _Response(200, {"authorization_code": "auth-code", "code_verifier": "verifier"}),
        _Response(200, {"access_token": access_token, "refresh_token": "refresh-token"}),
    ]
    _FakeClient.calls = []
    messages = []
    monkeypatch.setattr(codex_module.httpx, "Client", _FakeClient)
    monkeypatch.setattr(codex_module.time, "sleep", lambda seconds: None)

    token = codex_device_login(tmp_path, announce=messages.append)

    assert token.access_token == access_token
    assert load_codex_token(tmp_path).refresh_token == "refresh-token"
    assert any("ABCD" in message for message in messages)
    assert _FakeClient.calls[2][1]["data"]["grant_type"] == "authorization_code"
