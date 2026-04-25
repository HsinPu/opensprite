from opensprite.config.llm_presets import load_llm_presets


def test_load_llm_presets_has_expected_providers():
    presets = load_llm_presets()
    assert presets.version == 1
    assert presets.provider_order == ("openrouter", "openai", "minimax")
    assert set(presets.providers.keys()) == {"openrouter", "openai", "minimax"}
    assert presets.providers["openai"].default_base_url.startswith("https://")
    assert presets.providers["openai"].model_choices[0] == "gpt-4.1"
    assert "gpt-4o-mini" in presets.providers["openai"].model_choices
