import time

import pytest

from opensprite.auth.codex import (
    CodexAuthError,
    CodexToken,
    delete_codex_token,
    get_codex_status,
    load_codex_token,
    save_codex_token,
)


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
