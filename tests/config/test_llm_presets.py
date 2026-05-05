from opensprite.config.llm_presets import load_llm_presets


def test_load_llm_presets_has_expected_providers():
    presets = load_llm_presets()
    assert presets.version == 1
    assert presets.provider_order == ("openrouter", "openai", "openai-codex", "minimax")
    assert set(presets.providers.keys()) == {"openrouter", "openai", "openai-codex", "minimax"}
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
    assert presets.providers["openrouter"].model_capabilities["anthropic/claude-sonnet-4.6"] == {
        "reasoning": True,
        "vision": True,
        "tools": True,
        "recommended_options": {
            "reasoning_enabled": True,
            "reasoning_effort": "medium",
        },
    }
    assert presets.providers["openrouter"].model_capabilities["openai/gpt-5.5-pro"]["recommended_options"] == {
        "reasoning_enabled": True,
        "reasoning_effort": "high",
    }
    assert presets.providers["minimax"].model_choices[:3] == ("MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1")
    assert presets.providers["minimax"].media_model_choices == {
        "vision": ("MiniMax-VL-01",),
        "ocr": ("MiniMax-VL-01",),
    }
