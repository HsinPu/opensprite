from opensprite.config.llm_presets import (
    get_provider_profile,
    load_llm_presets,
    provider_api_mode,
    provider_auth_type,
    provider_default_base_url,
    provider_profile_defaults,
)


def test_load_llm_presets_has_expected_providers():
    presets = load_llm_presets()
    assert presets.version == 1
    assert presets.provider_order == ("openrouter", "openai", "openai-codex", "copilot", "ollama", "ollama-cloud", "minimax")
    assert set(presets.providers.keys()) == {
        "openrouter",
        "openai",
        "openai-codex",
        "copilot",
        "ollama",
        "ollama-cloud",
        "minimax",
    }
    assert presets.providers["openrouter"].model_choices[:30] == (
        "moonshotai/kimi-k2.6",
        "anthropic/claude-sonnet-4.6",
        "deepseek/deepseek-v3.2",
        "anthropic/claude-opus-4.7",
        "google/gemini-3-flash-preview",
        "tencent/hy3-preview:free",
        "stepfun/step-3.5-flash",
        "minimax/minimax-m2.7",
        "x-ai/grok-4.1-fast",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "qwen/qwen3.6-flash",
        "qwen/qwen3.6-max-preview",
        "qwen/qwen3.6-plus",
        "openai/gpt-5.5",
        "openai/gpt-5.5-pro",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "x-ai/grok-4.20",
        "x-ai/grok-4.20-multi-agent",
        "z-ai/glm-5.1",
        "z-ai/glm-5-turbo",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3.1-flash-lite-preview",
        "qwen/qwen3-coder-next",
        "qwen/qwen3-coder-plus",
        "mistralai/devstral-2512",
        "mistralai/mistral-large-2512",
        "mistralai/mistral-small-2603",
        "minimax/minimax-m2.5:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
    )
    assert presets.providers["openai"].default_base_url.startswith("https://")
    assert presets.providers["openai"].model_choices[:15] == (
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-chat",
        "gpt-5.3-codex",
        "gpt-5.2",
        "gpt-5.2-pro",
        "gpt-5.1",
        "gpt-5.1-codex",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "o3",
        "o4-mini",
    )
    assert presets.providers["openai"].media_model_choices == {
        "vision": ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-chat"),
        "ocr": ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini"),
    }
    assert presets.providers["openai-codex"].auth_type == "openai_codex_oauth"
    assert presets.providers["openai-codex"].api_mode == "responses"
    assert presets.providers["openai-codex"].default_base_url == "https://chatgpt.com/backend-api/codex"
    assert presets.providers["copilot"].display_name == "GitHub Copilot"
    assert presets.providers["copilot"].auth_type == "github_copilot_oauth"
    assert presets.providers["copilot"].default_base_url == "https://api.githubcopilot.com"
    assert presets.providers["copilot"].model_choices[:3] == ("gpt-5.4", "gpt-5.4-mini", "gpt-5-mini")
    assert presets.providers["ollama"].display_name == "Ollama Local"
    assert presets.providers["ollama"].auth_type == "optional_api_key"
    assert presets.providers["ollama"].default_base_url == "http://localhost:11434/v1"
    assert presets.providers["ollama"].model_choices == ()
    assert presets.providers["ollama-cloud"].display_name == "Ollama Cloud"
    assert presets.providers["ollama-cloud"].default_base_url == "https://ollama.com/v1"
    assert presets.providers["openrouter"].media_model_choices == {
        "vision": (
            "google/gemini-3-flash-preview",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.5",
            "qwen/qwen3.6-flash",
        ),
        "ocr": (
            "baidu/qianfan-ocr-fast:free",
            "google/gemini-3-flash-preview",
            "openai/gpt-5.5",
            "qwen/qwen3.6-flash",
        ),
    }
    assert presets.providers["openrouter"].capabilities == (
        "chat",
        "model_discovery",
        "media_discovery",
        "model_metadata",
    )
    assert presets.providers["openrouter"].model_discovery == {"type": "openrouter"}
    assert presets.providers["openrouter"].media_discovery == {"type": "openrouter_image"}
    assert presets.providers["openrouter"].model_metadata_fields == ("context_length",)
    assert presets.providers["openai"].model_discovery == {"type": "openai_compatible"}
    assert presets.providers["openai-codex"].model_discovery == {"type": "codex"}
    assert presets.providers["copilot"].model_discovery == {"type": "copilot"}
    assert presets.providers["openrouter"].model_capabilities["anthropic/claude-sonnet-4.6"] == {
        "reasoning": True,
        "vision": True,
        "tools": True,
    }
    assert presets.providers["openrouter"].model_capabilities["openai/gpt-5.5-pro"]["reasoning"] is True
    assert presets.providers["minimax"].model_choices[:3] == ("MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1")
    assert presets.providers["minimax"].default_base_url == "https://api.minimax.io/anthropic"
    assert presets.providers["minimax"].api_mode == "anthropic_messages"
    assert presets.providers["minimax"].media_model_choices == {
        "vision": ("MiniMax-VL-01",),
        "ocr": ("MiniMax-VL-01",),
    }


def test_provider_profile_accessors_return_known_provider_fields():
    profile = get_provider_profile(" openrouter ")

    assert profile is not None
    assert profile.default_base_url == "https://openrouter.ai/api/v1"
    assert provider_default_base_url("openrouter") == "https://openrouter.ai/api/v1"
    assert provider_auth_type("openai-codex") == "openai_codex_oauth"
    assert provider_api_mode("minimax") == "anthropic_messages"


def test_provider_profile_accessors_fall_back_for_unknown_provider():
    assert get_provider_profile("missing") is None
    assert provider_default_base_url("missing") == ""
    assert provider_auth_type("missing") == "api_key"
    assert provider_api_mode("missing") is None


def test_provider_profile_defaults_overlay_explicit_values():
    codex = provider_profile_defaults("openai-codex")
    assert codex.provider_id == "openai-codex"
    assert codex.auth_type == "openai_codex_oauth"
    assert codex.api_mode == "responses"
    assert codex.default_base_url == "https://chatgpt.com/backend-api/codex"

    copilot = provider_profile_defaults(None, auth_type="github_copilot_oauth")
    assert copilot.provider_id == "copilot"
    assert copilot.auth_type == "github_copilot_oauth"
    assert copilot.default_base_url == "https://api.githubcopilot.com"

    minimax_chat = provider_profile_defaults("minimax", api_mode="chat_completions")
    assert minimax_chat.auth_type == "api_key"
    assert minimax_chat.api_mode == "chat_completions"
    assert minimax_chat.default_base_url == ""
