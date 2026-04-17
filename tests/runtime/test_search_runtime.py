import pytest

from opensprite.config.schema import (
    AgentConfig,
    ChannelsConfig,
    Config,
    LLMsConfig,
    SearchEmbeddingConfig,
    SearchConfig,
    StorageConfig,
)
from opensprite.runtime import create_search_embedding_provider, create_search_store


def test_create_search_store_requires_sqlite_storage_when_enabled():
    config = Config(
        llm=LLMsConfig(api_key="key", model="gpt", temperature=0.7, max_tokens=2048),
        agent=AgentConfig(),
        storage=StorageConfig(type="memory", path="memory.db"),
        channels=ChannelsConfig(),
        search=SearchConfig(enabled=True),
    )

    with pytest.raises(ValueError, match="storage.type=sqlite"):
        create_search_store(config)


def test_create_search_embedding_provider_uses_search_or_llm_credentials():
    config = Config(
        llm=LLMsConfig(
            providers={
                "openai": {
                    "api_key": "llm-key",
                    "model": "gpt-4o-mini",
                    "base_url": "https://api.openai.com/v1",
                    "enabled": True,
                }
            },
            default="openai",
            api_key="",
            model="",
            temperature=0.7,
            max_tokens=2048,
        ),
        agent=AgentConfig(),
        storage=StorageConfig(type="sqlite", path="sessions.db"),
        channels=ChannelsConfig(),
        search=SearchConfig(
            enabled=True,
            embedding=SearchEmbeddingConfig(enabled=True, provider="openai", model="text-embedding-3-small"),
        ),
    )

    provider = create_search_embedding_provider(config)

    assert provider is not None
    assert provider.provider_name == "openai"
    assert provider.model_name == "text-embedding-3-small"
    assert provider.batch_size == 16


def test_create_search_store_passes_retry_failed_embedding_setting(tmp_path):
    config = Config(
        llm=LLMsConfig(
            providers={
                "openai": {
                    "api_key": "llm-key",
                    "model": "gpt-4o-mini",
                    "base_url": "https://api.openai.com/v1",
                    "enabled": True,
                }
            },
            default="openai",
            api_key="",
            model="",
            temperature=0.7,
            max_tokens=2048,
        ),
        agent=AgentConfig(),
        storage=StorageConfig(type="sqlite", path=str(tmp_path / "sessions.db")),
        channels=ChannelsConfig(),
        search=SearchConfig(
            enabled=True,
            embedding=SearchEmbeddingConfig(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
                retry_failed_on_startup=True,
            ),
        ),
    )

    store = create_search_store(config)

    assert store is not None
    assert store.retry_failed_on_startup is True
