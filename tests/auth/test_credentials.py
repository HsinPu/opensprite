import json

from opensprite.auth.credentials import (
    add_credential,
    list_credentials,
    mark_credential_used,
    remove_credential,
    resolve_credential,
    set_capability_default,
)


def test_credential_store_adds_and_resolves_redacted_credentials(tmp_path):
    credential = add_credential(
        "openrouter",
        "sk-or-secret",
        label="Router",
        base_url="https://openrouter.ai/api/v1",
        app_home=tmp_path,
    )

    listed = list_credentials(app_home=tmp_path)["openrouter"][0]
    resolved = resolve_credential(provider="openrouter", app_home=tmp_path)

    assert credential["id"].startswith("cred_")
    assert listed["secret_preview"] == "sk-o...cret"
    assert "secret" not in listed
    assert resolved.secret == "sk-or-secret"
    assert resolved.base_url == "https://openrouter.ai/api/v1"


def test_credential_store_tracks_usage_and_removes_defaults(tmp_path):
    credential = add_credential("openai", "secret-one", app_home=tmp_path)
    set_capability_default("llm.chat", credential["id"], app_home=tmp_path)

    mark_credential_used("openai", credential["id"], app_home=tmp_path)
    store = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    assert store["credentials"]["openai"][0]["request_count"] == 1
    assert store["credentials"]["openai"][0]["last_used_at"]

    remove_credential("openai", credential["id"], app_home=tmp_path)
    store = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    assert store["credentials"]["openai"] == []
    assert store["defaults"]["providers"] == {}
    assert store["defaults"]["capabilities"] == {}
