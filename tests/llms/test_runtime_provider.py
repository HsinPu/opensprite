import json

import pytest

from opensprite.config import ProviderConfig
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
