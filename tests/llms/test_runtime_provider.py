import json

import pytest

from opensprite.auth.credentials import add_credential
from opensprite.config import ProviderConfig
from opensprite.llms.anthropic_messages import AnthropicMessagesLLM
from opensprite.llms.minimax import MiniMaxLLM
from opensprite.llms.openai_responses import OpenAIResponsesLLM
from opensprite.llms.registry import create_llm
from opensprite.llms.runtime_provider import ProviderRuntimeError, resolve_provider_runtime


def test_resolve_api_key_provider_runtime_defaults_to_chat_completions():
    runtime = resolve_provider_runtime(
        ProviderConfig(api_key="sk-test", model="gpt-5.5", base_url="https://api.openai.com/v1", enabled=True),
        provider_name="openai",
    )

    assert runtime.provider_name == "openai"
    assert runtime.api_key == "sk-test"
    assert runtime.api_mode == "chat_completions"
    assert runtime.auth_type == "api_key"


def test_resolve_api_key_provider_runtime_reads_credential_store(tmp_path):
    credential = add_credential(
        "openai",
        "vault-secret",
        base_url="https://vault.example/v1",
        app_home=tmp_path,
    )

    runtime = resolve_provider_runtime(
        ProviderConfig(credential_id=credential["id"], model="gpt-4.1-mini", enabled=True),
        provider_name="openai",
        app_home=tmp_path,
    )

    assert runtime.api_key == "vault-secret"
    assert runtime.base_url == "https://vault.example/v1"


def test_resolve_codex_oauth_runtime_reads_auth_store(tmp_path):
    token_path = tmp_path / "auth" / "openai-codex.json"
    token_path.parent.mkdir()
    token_path.write_text(json.dumps({"access_token": "codex-token"}), encoding="utf-8")

    runtime = resolve_provider_runtime(
        ProviderConfig(
            provider="openai-codex",
            auth_type="openai_codex_oauth",
            model="gpt-5.1-codex",
            enabled=True,
        ),
        provider_name="openai-codex",
        app_home=tmp_path,
    )

    assert runtime.provider_name == "openai-codex"
    assert runtime.auth_type == "openai_codex_oauth"
    assert runtime.api_key == "codex-token"
    assert runtime.api_mode == "responses"
    assert runtime.base_url == "https://chatgpt.com/backend-api/codex"


def test_resolve_codex_oauth_runtime_requires_token(tmp_path):
    with pytest.raises(ProviderRuntimeError, match="OpenAI Codex OAuth is selected"):
        resolve_provider_runtime(
            ProviderConfig(provider="openai-codex", auth_type="openai_codex_oauth", model="gpt-5.1-codex"),
            provider_name="openai-codex",
            app_home=tmp_path,
        )


def test_create_llm_uses_responses_provider_for_responses_mode():
    provider = create_llm(
        api_key="codex-token",
        model="gpt-5.1-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        provider_name="openai-codex",
        api_mode="responses",
    )

    assert isinstance(provider, OpenAIResponsesLLM)
    assert provider.default_model == "gpt-5.1-codex"


def test_create_llm_passes_minimax_base_url():
    provider = create_llm(
        api_key="minimax-key",
        model="MiniMax-M2.7",
        base_url="https://api.minimaxi.com/v1",
        provider_name="minimax",
    )

    assert isinstance(provider, MiniMaxLLM)
    assert provider.base_url == "https://api.minimaxi.com/v1"


def test_create_llm_uses_anthropic_messages_provider_for_minimax_mode():
    provider = create_llm(
        api_key="minimax-key",
        model="MiniMax-M2.7",
        base_url="https://api.minimax.io/anthropic",
        provider_name="minimax",
        api_mode="anthropic_messages",
        reasoning_enabled=True,
        reasoning_effort="high",
    )

    assert isinstance(provider, AnthropicMessagesLLM)
    assert provider.base_url == "https://api.minimax.io/anthropic"
    assert provider.reasoning_effort == "high"


def test_resolve_copilot_runtime_exchanges_github_token(monkeypatch):
    monkeypatch.setattr(
        "opensprite.llms.runtime_provider.get_copilot_api_token",
        lambda api_key: "copilot-api-token",
    )

    runtime = resolve_provider_runtime(
        ProviderConfig(provider="copilot", api_key="gho_raw", model="gpt-5.4", enabled=True),
        provider_name="copilot",
    )

    assert runtime.provider_name == "copilot"
    assert runtime.api_key == "copilot-api-token"
    assert runtime.base_url == "https://api.githubcopilot.com"
    assert runtime.api_mode == "chat_completions"


def test_resolve_copilot_oauth_runtime_reads_auth_store(tmp_path, monkeypatch):
    token_path = tmp_path / "auth" / "github-copilot.json"
    token_path.parent.mkdir()
    token_path.write_text(json.dumps({"access_token": "gho_raw"}), encoding="utf-8")
    monkeypatch.setattr(
        "opensprite.llms.runtime_provider.get_copilot_api_token",
        lambda api_key: "copilot-api-token",
    )

    runtime = resolve_provider_runtime(
        ProviderConfig(provider="copilot", auth_type="github_copilot_oauth", model="gpt-5.4", enabled=True),
        provider_name="copilot",
        app_home=tmp_path,
    )

    assert runtime.api_key == "copilot-api-token"
    assert runtime.auth_type == "github_copilot_oauth"


def test_resolve_copilot_runtime_falls_back_to_raw_token_on_exchange_error(monkeypatch):
    from opensprite.auth.copilot import CopilotAuthError

    def fail_exchange(api_key, *, timeout_seconds=10.0):
        raise CopilotAuthError("GitHub Copilot token exchange failed: HTTP Error 404: Not Found")

    monkeypatch.setattr("opensprite.auth.copilot.exchange_copilot_token", fail_exchange)

    runtime = resolve_provider_runtime(
        ProviderConfig(provider="copilot", api_key="gho_raw", model="gpt-5.4", enabled=True),
        provider_name="copilot",
    )

    assert runtime.provider_name == "copilot"
    assert runtime.api_key == "gho_raw"


def test_create_llm_uses_copilot_headers():
    provider = create_llm(
        api_key="copilot-api-token",
        model="gpt-5.4",
        base_url="https://api.githubcopilot.com",
        provider_name="copilot",
    )

    assert provider.default_model == "gpt-5.4"
    assert provider.base_url == "https://api.githubcopilot.com"
    assert provider.default_headers["Copilot-Integration-Id"] == "vscode-chat"
    assert provider.default_headers["Openai-Intent"] == "conversation-edits"
