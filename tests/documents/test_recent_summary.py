import asyncio

from opensprite.documents.recent_summary import (
    RecentSummaryConsolidator,
    RecentSummaryStore,
    consolidate_recent_summary,
)
from opensprite.llms.base import LLMResponse
from opensprite.storage.base import StoredMessage


class FakeProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": messages, "model": model})
        return LLMResponse(content=self.content, model=model or "fake-model")


class FakeStorage:
    def __init__(self, messages):
        self.messages = list(messages)

    async def get_messages(self, chat_id, limit=None):
        return list(self.messages)


def test_consolidate_recent_summary_uses_structured_prompt(tmp_path):
    store = RecentSummaryStore(tmp_path)
    provider = FakeProvider("# Active Threads\n- finishing recent summary")

    result = asyncio.run(
        consolidate_recent_summary(
            summary_store=store,
            chat_id="chat-1",
            messages=[{"role": "user", "content": "We still need the recent summary layer."}],
            provider=provider,
            model="fake-model",
        )
    )

    assert result is True
    assert "# Active Threads" in store.read("chat-1")
    prompt = provider.calls[0]["messages"][1]["content"]
    assert "Focus on medium-term context" in prompt
    assert "# Follow-ups" in prompt


def test_recent_summary_consolidator_leaves_latest_messages_unsummarized(tmp_path):
    store = RecentSummaryStore(tmp_path)
    provider = FakeProvider("# Active Threads\n- done")
    storage = FakeStorage(
        [
            StoredMessage(role="user", content="older one", timestamp=1.0),
            StoredMessage(role="assistant", content="older two", timestamp=2.0),
            StoredMessage(role="user", content="keep raw", timestamp=3.0),
        ]
    )
    consolidator = RecentSummaryConsolidator(
        storage=storage,
        provider=provider,
        model="fake-model",
        summary_store=store,
        threshold=1,
        token_threshold=0,
        lookback_messages=10,
        keep_last_messages=1,
        enabled=True,
    )

    asyncio.run(consolidator.maybe_update("chat-1"))

    assert store.get_processed_index("chat-1") == 2
    prompt = provider.calls[0]["messages"][1]["content"]
    assert "older one" in prompt
    assert "older two" in prompt
    assert "keep raw" not in prompt
