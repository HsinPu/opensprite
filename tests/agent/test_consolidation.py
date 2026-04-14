import asyncio

from opensprite.agent import consolidation as consolidation_module
from opensprite.agent.consolidation import MemoryConsolidationService
from opensprite.storage.base import StoredMessage


class FakeStorage:
    def __init__(self, messages, consolidated_index=0):
        self.messages = messages
        self.consolidated_index = consolidated_index
        self.updated_index = None

    async def get_messages(self, chat_id, limit=None):
        return list(self.messages)

    async def get_consolidated_index(self, chat_id):
        return self.consolidated_index

    async def set_consolidated_index(self, chat_id, index):
        self.updated_index = index


class FakeProvider:
    def get_default_model(self):
        return "fake-model"


def test_memory_consolidation_skips_when_threshold_not_reached(monkeypatch):
    called = False

    async def fake_consolidate(**kwargs):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(consolidation_module, "consolidate", fake_consolidate)

    storage = FakeStorage(
        [
            StoredMessage(role="user", content="one", timestamp=1.0),
            StoredMessage(role="assistant", content="two", timestamp=2.0),
        ],
        consolidated_index=0,
    )
    service = MemoryConsolidationService(
        storage=storage,
        memory_store=object(),
        provider=FakeProvider(),
        threshold=3,
        token_threshold=100,
    )

    asyncio.run(service.maybe_consolidate("chat-1"))

    assert called is False
    assert storage.updated_index is None


def test_memory_consolidation_updates_index_after_success(monkeypatch):
    captured = {}

    async def fake_consolidate(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(consolidation_module, "consolidate", fake_consolidate)

    storage = FakeStorage(
        [
            StoredMessage(role="user", content="first", timestamp=1.0),
            {"role": "assistant", "content": "second"},
            StoredMessage(role="tool", content="third", timestamp=3.0),
        ],
        consolidated_index=1,
    )
    service = MemoryConsolidationService(
        storage=storage,
        memory_store=object(),
        provider=FakeProvider(),
        threshold=2,
        token_threshold=100,
    )

    asyncio.run(service.maybe_consolidate("chat-1"))

    assert captured["chat_id"] == "chat-1"
    assert captured["model"] == "fake-model"
    assert captured["messages"] == [
        {"role": "assistant", "content": "second"},
        {"role": "tool", "content": "third"},
    ]
    assert storage.updated_index == 3


def test_memory_consolidation_triggers_when_token_threshold_reached(monkeypatch):
    captured = {}

    async def fake_consolidate(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(consolidation_module, "consolidate", fake_consolidate)
    monkeypatch.setattr(consolidation_module, "count_messages_tokens", lambda messages, model=None, encoding_name=None: 250)

    storage = FakeStorage(
        [
            StoredMessage(role="user", content="very long message", timestamp=1.0),
            StoredMessage(role="assistant", content="another long message", timestamp=2.0),
        ],
        consolidated_index=0,
    )
    service = MemoryConsolidationService(
        storage=storage,
        memory_store=object(),
        provider=FakeProvider(),
        threshold=10,
        token_threshold=200,
    )

    asyncio.run(service.maybe_consolidate("chat-1"))

    assert captured["chat_id"] == "chat-1"
    assert storage.updated_index == 2


def test_memory_consolidation_uses_full_history_for_processed_index(monkeypatch):
    captured = {}

    async def fake_consolidate(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(consolidation_module, "consolidate", fake_consolidate)

    messages = [StoredMessage(role="user", content=f"m{i}", timestamp=float(i)) for i in range(1005)]
    storage = FakeStorage(messages, consolidated_index=1000)
    service = MemoryConsolidationService(
        storage=storage,
        memory_store=object(),
        provider=FakeProvider(),
        threshold=1,
        token_threshold=0,
    )

    asyncio.run(service.maybe_consolidate("chat-1"))

    assert [message["content"] for message in captured["messages"]] == [f"m{i}" for i in range(1000, 1005)]
    assert storage.updated_index == 1005
