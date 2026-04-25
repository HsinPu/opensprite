import pytest
import asyncio

from opensprite.config.schema import (
    AgentConfig,
    ChannelsConfig,
    Config,
    LLMsConfig,
    SearchEmbeddingConfig,
    SearchConfig,
    StorageConfig,
)
from opensprite.runtime import (
    create_search_embedding_provider,
    create_search_store,
    should_start_search_queue_worker,
    start_search_queue_worker,
    stop_background_task,
)


class FakeSearchStore:
    def __init__(self, embedding_provider=None):
        self.embedding_provider = embedding_provider
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run_queue(self, once=False):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def test_create_search_store_requires_sqlite_storage_when_enabled():
    config = Config(
        llm=LLMsConfig(**{**Config.packaged_llm_flat_dict(), "api_key": "key", "model": "gpt"}),
        agent=Config.load_agent_template_config(),
        storage=StorageConfig(type="memory", path="memory.db"),
        channels=ChannelsConfig(),
        search=SearchConfig(enabled=True),
    )

    with pytest.raises(ValueError, match='search.backend="sqlite" requires storage.type="sqlite"'):
        create_search_store(config)


def test_create_search_embedding_provider_uses_search_or_llm_credentials():
    config = Config(
        llm=LLMsConfig(
            **{
                **Config.packaged_llm_flat_dict(),
                "providers": {
                    "openai": {
                        "api_key": "llm-key",
                        "model": "gpt-4o-mini",
                        "base_url": "https://api.openai.com/v1",
                        "enabled": True,
                    }
                },
                "default": "openai",
                "api_key": "",
                "model": "",
            }
        ),
        agent=Config.load_agent_template_config(),
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
            **{
                **Config.packaged_llm_flat_dict(),
                "providers": {
                    "openai": {
                        "api_key": "llm-key",
                        "model": "gpt-4o-mini",
                        "base_url": "https://api.openai.com/v1",
                        "enabled": True,
                    }
                },
                "default": "openai",
                "api_key": "",
                "model": "",
            }
        ),
        agent=Config.load_agent_template_config(),
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


def test_create_search_store_passes_embedding_candidate_strategy(tmp_path):
    config = Config(
        llm=LLMsConfig(
            **{
                **Config.packaged_llm_flat_dict(),
                "providers": {
                    "openai": {
                        "api_key": "llm-key",
                        "model": "gpt-4o-mini",
                        "base_url": "https://api.openai.com/v1",
                        "enabled": True,
                    }
                },
                "default": "openai",
                "api_key": "",
                "model": "",
            }
        ),
        agent=Config.load_agent_template_config(),
        storage=StorageConfig(type="sqlite", path=str(tmp_path / "sessions.db")),
        channels=ChannelsConfig(),
        search=SearchConfig(
            enabled=True,
            embedding=SearchEmbeddingConfig(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
                candidate_strategy="vector",
                vector_backend="sqlite_vec",
                vector_candidate_count=80,
            ),
        ),
    )

    store = create_search_store(config)

    assert store is not None
    assert store.embedding_candidate_strategy == "vector"
    assert store.vector_backend_requested == "sqlite_vec"
    assert store.vector_candidate_count == 80


def test_should_start_search_queue_worker_requires_embeddings():
    assert should_start_search_queue_worker(None) is False
    assert should_start_search_queue_worker(FakeSearchStore()) is False
    assert should_start_search_queue_worker(FakeSearchStore(embedding_provider=object())) is True


def test_start_and_stop_search_queue_worker_lifecycle():
    async def scenario():
        store = FakeSearchStore(embedding_provider=object())
        task = start_search_queue_worker(store)
        assert task is not None
        await asyncio.wait_for(store.started.wait(), timeout=1.0)
        await stop_background_task(task, name="test queue worker")
        await asyncio.wait_for(store.cancelled.wait(), timeout=1.0)

    asyncio.run(scenario())
