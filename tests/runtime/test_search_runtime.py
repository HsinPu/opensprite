import pytest

from opensprite.config.schema import (
    AgentConfig,
    ChannelsConfig,
    Config,
    LLMsConfig,
    SearchConfig,
    StorageConfig,
)
from opensprite.runtime import create_search_store


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
