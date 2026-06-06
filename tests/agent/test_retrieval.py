import asyncio

from opensprite.context.message_history import (
    HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON,
    ProactiveRetrievalService,
    history_retrieval_metadata_has_results,
    history_retrieval_metadata_reports_empty,
)
from opensprite.search.base import SearchHit


class _SearchStore:
    def __init__(self):
        self.calls = []

    async def search_history(self, session_id: str, query: str, limit: int = 5):
        self.calls.append((session_id, query, limit))
        return [
            SearchHit(
                id="hit-1",
                session_id=session_id,
                source_type="message",
                role="assistant",
                content="The cleanup fix touched src/cleanup.py.",
                created_at=1_700_000_000,
            )
        ]


def test_proactive_retrieval_requires_structured_decision():
    store = _SearchStore()
    service = ProactiveRetrievalService(search_store=store)

    context = asyncio.run(
        service.build_context(
            session_id="web:room-1",
            current_message="Use the earlier fix again.",
            should_retrieve=None,
        )
    )

    assert context == ""
    assert store.calls == []


def test_proactive_retrieval_formats_history_when_requested():
    store = _SearchStore()
    service = ProactiveRetrievalService(search_store=store)

    context = asyncio.run(
        service.build_context(
            session_id="web:room-1",
            current_message="Use the earlier fix again.",
            should_retrieve=True,
        )
    )

    assert "# Proactive Retrieval Context" in context
    assert "## Retrieved History" in context
    assert "src/cleanup.py" in context
    assert store.calls == [("web:room-1", "Use the earlier fix again.", 3)]


def test_history_recalled_items_insufficient_reason_is_stable():
    assert HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON == "assistant did not provide enough recalled items"


def test_history_retrieval_metadata_reports_explicit_empty_counts():
    assert history_retrieval_metadata_reports_empty({"result_count": 0})
    assert history_retrieval_metadata_reports_empty({"hit_count": "0"})
    assert history_retrieval_metadata_reports_empty({"hits": []})


def test_history_retrieval_metadata_does_not_report_empty_when_hits_exist():
    assert not history_retrieval_metadata_reports_empty({"result_count": 2})
    assert not history_retrieval_metadata_reports_empty({"hits": [{"content": "prior note"}]})


def test_history_retrieval_metadata_reports_hits_separately_from_empty_counts():
    assert history_retrieval_metadata_has_results({"result_count": 2})
    assert history_retrieval_metadata_has_results({"hits": [{"content": "prior note"}]})
    assert not history_retrieval_metadata_has_results({"result_count": 0})


def test_history_retrieval_metadata_requires_explicit_count_field():
    assert not history_retrieval_metadata_reports_empty({})
    assert not history_retrieval_metadata_reports_empty(None)
