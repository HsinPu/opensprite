import asyncio
import json
from datetime import datetime

from opensprite.agent.task_artifact import build_task_artifact
from opensprite.config.schema import WebFetchToolConfig, WebSearchToolConfig
from opensprite.search.base import SearchHit
from opensprite.tools.evidence import ToolEvidence, build_tool_evidence
from opensprite.tools.web_search import _format_results
from opensprite.tools.web_research import WebResearchTool


class _FakeSearchTool:
    provider = "duckduckgo"
    backend = "ddgs"

    def __init__(self, items):
        self.items = items
        self.calls = []

    async def _execute(self, query, count=None, freshness=None):
        self.calls.append({"query": query, "count": count, "freshness": freshness})
        return _format_results(query, self.items, count or len(self.items), provider=self.provider, backend=self.backend)


class _FakeSearchToolByQuery:
    provider = "duckduckgo"
    backend = "ddgs"

    def __init__(self, items_by_query):
        self.items_by_query = items_by_query
        self.calls = []

    async def _execute(self, query, count=None, freshness=None):
        self.calls.append({"query": query, "count": count, "freshness": freshness})
        items = self.items_by_query.get(query, [])
        return _format_results(query, items, count or len(items), provider=self.provider, backend=self.backend)


class _FakeFetchTool:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    async def _execute(self, url, max_chars=None):
        self.calls.append({"url": url, "max_chars": max_chars})
        payload = self.payloads[url]
        return json.dumps(payload, ensure_ascii=False)


class _FakeKnowledgeStore:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    async def search_knowledge(self, **kwargs):
        self.calls.append(kwargs)
        return self.hits


def _fetch_payload(url, *, title="Fetched", content="x" * 900, too_short=False, status=200, extractor="trafilatura"):
    return {
        "type": "web_fetch",
        "query": url,
        "url": url,
        "final_url": f"{url}?ref=1",
        "title": title,
        "content": content,
        "summary": title,
        "provider": "web_fetch",
        "extractor": extractor,
        "status": status,
        "content_type": "text/html",
        "truncated": False,
        "content_chars": len(content),
        "has_title": True,
        "is_too_short": too_short,
        "min_content_chars": 800,
        "items": [],
    }


def test_web_research_searches_and_fetches_traceable_sources():
    search = _FakeSearchTool(
        [
            {"title": "One", "url": "https://example.com/one", "content": "First snippet"},
            {"title": "Two", "url": "https://example.com/two", "content": "Second snippet"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/one": _fetch_payload("https://example.com/one", title="Fetched One"),
            "https://example.com/two": _fetch_payload("https://example.com/two", title="Fetched Two"),
        }
    )
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(max_results=10),
        fetch_config=WebFetchToolConfig(max_chars=1234),
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("sqlite fts", count=5, fetch_count=2, freshness="month")))

    assert search.calls == [{"query": "sqlite fts", "count": 5, "freshness": "month"}]
    assert [call["url"] for call in fetch.calls] == ["https://example.com/one", "https://example.com/two"]
    assert all(call["max_chars"] == 1234 for call in fetch.calls)
    assert payload["type"] == "web_research"
    assert payload["provider"] == "duckduckgo"
    assert payload["backend"] == "ddgs"
    assert payload["fetched_count"] == 2
    assert payload["fetched_sources"][0]["source_query"] == "sqlite fts"
    assert payload["fetched_sources"][0]["search_backend"] == "ddgs"
    assert payload["fetched_sources"][0]["search_rank"] == 1
    assert payload["fetched_sources"][0]["has_main_content"] is True
    assert payload["fetched_sources"][0]["blocked_or_challenge"] is False
    assert payload["fetched_sources"][0]["quality_score"] == 1.0
    assert payload["fetched_sources"][0]["fetch_attempts"] == [
        {
            "tool": "web_fetch",
            "extractor": "trafilatura",
            "status": 200,
            "content_chars": 900,
            "is_too_short": False,
            "blocked_or_challenge": False,
            "quality_score": 1.0,
        }
    ]
    assert payload["sources"][1]["tool_name"] == "web_fetch"


def test_web_research_defaults_count_to_configured_max_results():
    search = _FakeSearchTool(
        [
            {"title": "One", "url": "https://example.com/one", "content": "First snippet"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/one": _fetch_payload("https://example.com/one", title="Fetched One"),
        }
    )
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(max_results=25),
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("coding model comparison", fetch_count=1)))

    assert tool.parameters["properties"]["count"]["default"] == 25
    assert search.calls == [{"query": "coding model comparison", "count": 25, "freshness": "month"}]
    assert payload["coverage"]["search_result_count"] == 1


def test_web_research_infers_freshness_for_latest_query_when_config_is_auto():
    search = _FakeSearchTool(
        [
            {"title": "Qwen Release", "url": "https://example.com/qwen", "content": "Recent Qwen release"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/qwen": _fetch_payload("https://example.com/qwen", title="Qwen Release"),
        }
    )
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(freshness="auto"),
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("Qwen 最新模型 2026", count=5, fetch_count=1)))

    assert search.calls == [{"query": "Qwen 最新模型 2026", "count": 5, "freshness": "month"}]
    assert payload["freshness"] == "month"


def test_web_research_respects_any_time_for_latest_query():
    search = _FakeSearchTool(
        [
            {"title": "Qwen Release", "url": "https://example.com/qwen", "content": "Recent Qwen release"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/qwen": _fetch_payload("https://example.com/qwen", title="Qwen Release"),
        }
    )
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(freshness="none"),
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("Qwen latest model 2026", count=5, fetch_count=1)))

    assert search.calls == [{"query": "Qwen latest model 2026", "count": 5, "freshness": "none"}]
    assert payload["freshness"] == "none"


def test_web_research_runs_manual_queries_and_dedupes_fetches():
    search = _FakeSearchToolByQuery(
        {
            "sqlite fts": [
                {"title": "Shared", "url": "https://example.com/shared", "content": "Shared snippet"},
                {"title": "Primary", "url": "https://example.com/primary", "content": "Primary snippet"},
            ],
            "sqlite benchmark": [
                {"title": "Shared Duplicate", "url": "https://example.com/shared/", "content": "Duplicate snippet"},
                {"title": "Benchmark", "url": "https://example.com/benchmark", "content": "Benchmark snippet"},
            ],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/shared": _fetch_payload("https://example.com/shared", title="Shared"),
            "https://example.com/primary": _fetch_payload("https://example.com/primary", title="Primary"),
            "https://example.com/benchmark": _fetch_payload("https://example.com/benchmark", title="Benchmark"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(tool._execute("sqlite fts", queries=["sqlite benchmark"], count=3, fetch_count=3))
    )

    assert search.calls == [
        {"query": "sqlite fts", "count": 3, "freshness": "month"},
        {"query": "sqlite benchmark", "count": 3, "freshness": "month"},
    ]
    assert [call["url"] for call in fetch.calls] == [
        "https://example.com/shared",
        "https://example.com/primary",
        "https://example.com/benchmark",
    ]
    assert payload["queries"] == ["sqlite fts", "sqlite benchmark"]
    assert [item["url"] for item in payload["items"]] == [
        "https://example.com/shared",
        "https://example.com/primary",
        "https://example.com/benchmark",
    ]
    assert [item["source_query"] for item in payload["fetched_sources"]] == [
        "sqlite fts",
        "sqlite fts",
        "sqlite benchmark",
    ]
    assert [attempt["query"] for attempt in payload["query_attempts"]] == ["sqlite fts", "sqlite benchmark"]
    assert all(attempt["ok"] is True for attempt in payload["query_attempts"])
    assert [attempt["result_count"] for attempt in payload["query_attempts"]] == [2, 2]
    assert all(attempt["backend"] == "ddgs" for attempt in payload["query_attempts"])


def test_web_research_searches_manual_queries_concurrently():
    class _ConcurrentSearchToolByQuery(_FakeSearchToolByQuery):
        def __init__(self, items_by_query):
            super().__init__(items_by_query)
            self.started = []
            self.ready = None

        async def _execute(self, query, count=None, freshness=None):
            if self.ready is None:
                self.ready = asyncio.Event()
            self.calls.append({"query": query, "count": count, "freshness": freshness})
            self.started.append(query)
            if len(self.started) >= 2:
                self.ready.set()
            await self.ready.wait()
            items = self.items_by_query.get(query, [])
            return _format_results(query, items, count or len(items), provider=self.provider, backend=self.backend)

    search = _ConcurrentSearchToolByQuery(
        {
            "sqlite fts": [{"title": "One", "url": "https://example.com/one", "content": "One snippet"}],
            "sqlite benchmark": [
                {"title": "Two", "url": "https://example.com/two", "content": "Two snippet"}
            ],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/one": _fetch_payload("https://example.com/one", title="One"),
            "https://example.com/two": _fetch_payload("https://example.com/two", title="Two"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(
            asyncio.wait_for(
                tool._execute("sqlite fts", queries=["sqlite benchmark"], count=2, fetch_count=2),
                timeout=1,
            )
        )
    )

    assert search.started == ["sqlite fts", "sqlite benchmark"]
    assert payload["fetched_count"] == 2


def test_web_research_fetches_candidate_batch_concurrently():
    class _ConcurrentFetchTool(_FakeFetchTool):
        def __init__(self, payloads):
            super().__init__(payloads)
            self.ready = None

        async def _execute(self, url, max_chars=None):
            if self.ready is None:
                self.ready = asyncio.Event()
            self.calls.append({"url": url, "max_chars": max_chars})
            if len(self.calls) >= 2:
                self.ready.set()
            await self.ready.wait()
            return json.dumps(self.payloads[url], ensure_ascii=False)

    search = _FakeSearchTool(
        [
            {"title": "One", "url": "https://example.com/one", "content": "One snippet"},
            {"title": "Two", "url": "https://example.com/two", "content": "Two snippet"},
        ]
    )
    fetch = _ConcurrentFetchTool(
        {
            "https://example.com/one": _fetch_payload("https://example.com/one", title="One"),
            "https://example.com/two": _fetch_payload("https://example.com/two", title="Two"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(asyncio.wait_for(tool._execute("sqlite fts", count=2, fetch_count=2), timeout=1))
    )

    assert [call["url"] for call in fetch.calls] == ["https://example.com/one", "https://example.com/two"]
    assert payload["fetched_count"] == 2


def test_web_research_prioritizes_current_year_candidates_for_recent_searches():
    current_year = datetime.now().year
    search = _FakeSearchTool(
        [
            {"title": f"Older guide 2025", "url": "https://example.com/old", "content": "Old snippet"},
            {
                "title": f"Release notes {current_year}",
                "url": "https://example.com/current",
                "content": "Latest release notes",
            },
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/current": _fetch_payload("https://example.com/current", title="Current"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("release notes", count=2, fetch_count=1, freshness="month")))

    assert [call["url"] for call in fetch.calls] == ["https://example.com/current"]
    assert payload["fetched_sources"][0]["search_freshness"] == "month"


def test_web_research_diversifies_fetch_candidates_across_queries_and_domains():
    search = _FakeSearchToolByQuery(
        {
            "vector database": [
                {"title": "Vendor Overview", "url": "https://vendor.test/overview", "content": "Overview snippet"},
                {"title": "Vendor Docs", "url": "https://vendor.test/docs", "content": "Docs snippet"},
            ],
            "vector database benchmark": [
                {"title": "Independent Benchmark", "url": "https://bench.test/vector", "content": "Benchmark snippet"},
                {"title": "Vendor Benchmark", "url": "https://vendor.test/benchmark", "content": "Vendor benchmark snippet"},
            ],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://vendor.test/overview": _fetch_payload("https://vendor.test/overview", title="Vendor Overview"),
            "https://vendor.test/docs": _fetch_payload("https://vendor.test/docs", title="Vendor Docs"),
            "https://bench.test/vector": _fetch_payload("https://bench.test/vector", title="Independent Benchmark"),
            "https://vendor.test/benchmark": _fetch_payload("https://vendor.test/benchmark", title="Vendor Benchmark"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(tool._execute("vector database", queries=["vector database benchmark"], count=3, fetch_count=2))
    )

    assert [item["url"] for item in payload["items"]] == [
        "https://vendor.test/overview",
        "https://vendor.test/docs",
        "https://bench.test/vector",
        "https://vendor.test/benchmark",
    ]
    assert [call["url"] for call in fetch.calls] == [
        "https://vendor.test/overview",
        "https://bench.test/vector",
    ]
    assert [source["source_query"] for source in payload["fetched_sources"]] == [
        "vector database",
        "vector database benchmark",
    ]
    assert [source["domain"] for source in payload["fetched_sources"]] == ["vendor.test", "bench.test"]


def test_web_research_preserves_single_query_fetch_order_across_domains():
    search = _FakeSearchTool(
        [
            {"title": "Vendor Overview", "url": "https://vendor.test/overview", "content": "Overview snippet"},
            {"title": "Vendor Docs", "url": "https://vendor.test/docs", "content": "Docs snippet"},
            {"title": "Independent Benchmark", "url": "https://bench.test/vector", "content": "Benchmark snippet"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://vendor.test/overview": _fetch_payload("https://vendor.test/overview", title="Vendor Overview"),
            "https://vendor.test/docs": _fetch_payload("https://vendor.test/docs", title="Vendor Docs"),
            "https://bench.test/vector": _fetch_payload("https://bench.test/vector", title="Independent Benchmark"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    json.loads(asyncio.run(tool._execute("vector database", count=3, fetch_count=2)))

    assert [call["url"] for call in fetch.calls] == [
        "https://vendor.test/overview",
        "https://vendor.test/docs",
    ]


def test_web_research_reports_source_coverage_and_gaps():
    search = _FakeSearchToolByQuery(
        {
            "ai browser": [
                {"title": "Official Docs", "url": "https://docs.test/browser", "content": "Docs snippet"},
            ],
            "ai browser pricing": [
                {"title": "Pricing", "url": "https://pricing.test/browser", "content": "Pricing snippet"},
            ],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://docs.test/browser": _fetch_payload("https://docs.test/browser", title="Official Docs"),
            "https://pricing.test/browser": _fetch_payload(
                "https://pricing.test/browser",
                title="Pricing",
                content="short",
                too_short=True,
            ),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(tool._execute("ai browser", queries=["ai browser pricing"], count=2, fetch_count=2))
    )

    assert payload["fetched_count"] == 1
    assert payload["failed_sources"][0]["reason"] == "fetched content was too short"
    assert payload["coverage"] == {
        "target_fetch_count": 2,
        "target_met": False,
        "search_result_count": 2,
        "fetched_count": 1,
        "failed_count": 1,
        "too_short_count": 1,
        "blocked_count": 0,
        "missing_url_count": 0,
        "fetched_domains": ["docs.test"],
        "fetched_domain_count": 1,
        "fetched_queries": ["ai browser"],
        "fetched_query_count": 1,
        "queries_with_search_results": ["ai browser", "ai browser pricing"],
        "queries_without_successful_fetch": ["ai browser pricing"],
    }


def test_web_research_reuses_existing_high_quality_fetch_without_network_search():
    knowledge = _FakeKnowledgeStore(
        [
            SearchHit(
                id="1",
                session_id="chat-1",
                source_type="web_fetch",
                content="SQLite FTS5 documentation " * 40,
                created_at=123.0,
                title="SQLite Docs",
                url="https://sqlite.org/fts5.html",
                query="sqlite fts",
                summary="Official docs",
                provider="web_fetch",
                extractor="trafilatura",
                status=200,
                content_type="text/html",
                truncated=False,
            )
        ]
    )
    search = _FakeSearchTool([{"title": "Should not run", "url": "https://example.com", "content": "unused"}])
    fetch = _FakeFetchTool({})
    tool = WebResearchTool(
        search_tool=search,
        fetch_tool=fetch,
        knowledge_store=knowledge,
        get_session_id=lambda: "chat-1",
    )

    payload = json.loads(asyncio.run(tool._execute("sqlite fts", fetch_count=1, freshness="none")))

    assert knowledge.calls == [
        {
            "session_id": "chat-1",
            "query": "sqlite fts",
            "limit": 5,
            "source_type": "web_fetch",
        }
    ]
    assert search.calls == []
    assert fetch.calls == []
    assert payload["provider"] == "search_knowledge"
    assert payload["fetched_count"] == 1
    assert payload["reused_count"] == 1
    assert payload["reuse_attempt"] == {
        "source": "search_knowledge",
        "ok": True,
        "result_count": 1,
        "reused_count": 1,
    }
    assert payload["fetched_sources"][0]["reused"] is True
    assert payload["fetched_sources"][0]["reuse_source"] == "search_knowledge"
    assert payload["fetched_sources"][0]["has_main_content"] is True
    assert payload["freshness"] == "none"
    assert payload["coverage"]["target_met"] is True
    assert payload["coverage"]["fetched_domains"] == ["sqlite.org"]


def test_web_research_skips_knowledge_reuse_for_recent_queries():
    knowledge = _FakeKnowledgeStore(
        [
            SearchHit(
                id="old",
                session_id="chat-1",
                source_type="web_fetch",
                content="Old Qwen model notes " * 80,
                created_at=123.0,
                title="Old Qwen Notes",
                url="https://example.com/old-qwen",
                query="Qwen 最新模型",
                summary="Old source",
                provider="web_fetch",
                extractor="trafilatura",
                status=200,
                content_type="text/html",
                truncated=False,
            )
        ]
    )
    search = _FakeSearchTool(
        [{"title": "Fresh Qwen", "url": "https://example.com/fresh-qwen", "content": "Fresh snippet"}]
    )
    fetch = _FakeFetchTool({"https://example.com/fresh-qwen": _fetch_payload("https://example.com/fresh-qwen")})
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(freshness="auto"),
        search_tool=search,
        fetch_tool=fetch,
        knowledge_store=knowledge,
        get_session_id=lambda: "chat-1",
    )

    payload = json.loads(asyncio.run(tool._execute("Qwen 最新模型 2026", fetch_count=1)))

    assert knowledge.calls == []
    assert search.calls == [{"query": "Qwen 最新模型 2026", "count": 25, "freshness": "month"}]
    assert payload["reuse_attempt"] == {
        "source": "search_knowledge",
        "ok": False,
        "reason": "skipped for recent query",
    }
    assert payload["fetched_sources"][0]["url"] == "https://example.com/fresh-qwen?ref=1"


def test_web_research_ignores_low_quality_knowledge_and_fetches_new_source():
    knowledge = _FakeKnowledgeStore(
        [
            SearchHit(
                id="1",
                session_id="chat-1",
                source_type="web_fetch",
                content="short",
                created_at=123.0,
                title="Short Docs",
                url="https://sqlite.org/short.html",
                extractor="trafilatura",
                status=200,
                content_type="text/html",
                truncated=False,
            )
        ]
    )
    search = _FakeSearchTool(
        [{"title": "Fresh", "url": "https://example.com/fresh", "content": "Fresh snippet"}]
    )
    fetch = _FakeFetchTool({"https://example.com/fresh": _fetch_payload("https://example.com/fresh")})
    tool = WebResearchTool(
        search_tool=search,
        fetch_tool=fetch,
        knowledge_store=knowledge,
        get_session_id=lambda: "chat-1",
    )

    payload = json.loads(asyncio.run(tool._execute("sqlite fts", fetch_count=1)))

    assert search.calls == [{"query": "sqlite fts", "count": 25, "freshness": "month"}]
    assert [call["url"] for call in fetch.calls] == ["https://example.com/fresh"]
    assert payload["reused_count"] == 0
    assert knowledge.calls == []
    assert payload["reuse_attempt"] == {
        "source": "search_knowledge",
        "ok": False,
        "reason": "skipped for recent query",
    }
    assert payload["fetched_sources"][0]["reused"] is False


def test_web_research_dedupes_urls_and_skips_too_short_fetches():
    search = _FakeSearchTool(
        [
            {"title": "One", "url": "https://example.com/one", "content": "First snippet"},
            {"title": "Duplicate", "url": "https://example.com/one/", "content": "Duplicate snippet"},
            {"title": "Two", "url": "https://example.com/two", "content": "Second snippet"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/one": _fetch_payload("https://example.com/one", content="short", too_short=True),
            "https://example.com/two": _fetch_payload("https://example.com/two", title="Fetched Two"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("sqlite fts", fetch_count=1)))

    assert [call["url"] for call in fetch.calls] == ["https://example.com/one", "https://example.com/two"]
    assert [item["url"] for item in payload["items"]] == ["https://example.com/one", "https://example.com/two"]
    assert payload["fetched_count"] == 1
    assert payload["fetched_sources"][0]["url"] == "https://example.com/two?ref=1"
    assert payload["failed_sources"][0]["reason"] == "fetched content was too short"
    assert payload["failed_sources"][0]["has_main_content"] is False


def test_web_research_falls_back_to_next_search_provider(monkeypatch):
    calls = []

    async def fake_search(self, query, count=None, freshness=None):
        calls.append(self.provider)
        if self.provider == "duckduckgo":
            return json.dumps(
                {
                    "type": "web_search",
                    "ok": False,
                    "query": query,
                    "provider": "duckduckgo",
                    "backend": "ddgs",
                    "items": [],
                    "error": "Error: DuckDuckGo blocked the search for 'sqlite' with a bot challenge.",
                }
            )
        return _format_results(
            query,
            [{"title": "SearXNG Result", "url": "https://example.com/searx", "content": "Fallback snippet"}],
            count or 1,
            provider=self.provider,
            backend="searxng",
        )

    monkeypatch.setattr("opensprite.tools.web_research.WebSearchTool._execute", fake_search)
    fetch = _FakeFetchTool({"https://example.com/searx": _fetch_payload("https://example.com/searx")})
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(provider="duckduckgo", searxng_url="https://searx.test"),
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("sqlite", fetch_count=1)))

    assert calls == ["duckduckgo", "searxng"]
    assert payload["provider"] == "searxng"
    assert payload["search_attempts"] == [
        {
            "provider": "duckduckgo",
            "configured_provider": "duckduckgo",
            "backend": "ddgs",
            "ok": False,
            "result_count": 0,
            "fetchable_count": 0,
            "error": "Error: DuckDuckGo blocked the search for 'sqlite' with a bot challenge.",
        },
        {
            "provider": "searxng",
            "configured_provider": "searxng",
            "backend": "searxng",
            "ok": True,
            "result_count": 1,
            "fetchable_count": 1,
            "error": "",
        },
    ]
    assert payload["fetched_sources"][0]["search_provider"] == "searxng"
    assert payload["fetched_sources"][0]["search_backend"] == "searxng"


def test_web_research_marks_blocked_fetches_as_low_quality():
    search = _FakeSearchTool(
        [{"title": "Blocked", "url": "https://example.com/blocked", "content": "Blocked snippet"}]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/blocked": _fetch_payload(
                "https://example.com/blocked",
                title="Access Denied",
                content="Captcha: verify you are human " * 80,
                status=403,
            )
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("sqlite", fetch_count=1)))

    assert payload["fetched_count"] == 0
    assert payload["failed_sources"][0]["reason"] == "fetched content looked blocked or challenged"
    assert payload["failed_sources"][0]["blocked_or_challenge"] is True
    assert payload["failed_sources"][0]["has_main_content"] is False
    assert payload["failed_sources"][0]["quality_score"] <= 0.35


def test_web_research_evidence_builds_web_source_artifact_with_fetch_detail():
    result = json.dumps(
        {
            "type": "web_research",
            "query": "sqlite",
            "provider": "duckduckgo",
            "backend": "ddgs",
            "coverage": {
                "target_fetch_count": 2,
                "target_met": False,
                "queries_without_successful_fetch": ["sqlite pricing"],
            },
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "title": "SQLite Docs",
                    "url": "https://sqlite.org/fts5.html",
                    "content": "SQLite FTS5 documentation " * 40,
                    "content_chars": 1000,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "quality_score": 0.95,
                    "is_too_short": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "search_backend": "ddgs",
                }
            ],
        },
        ensure_ascii=False,
    )

    evidence = build_tool_evidence("web_research", {"query": "sqlite"}, result, ok=True)
    artifact = build_task_artifact(evidence)

    assert evidence.metadata["source_count"] == 1
    assert evidence.metadata["sources"][0]["tool_name"] == "web_fetch"
    assert evidence.metadata["sources"][0]["quality_score"] == 0.95
    assert evidence.metadata["sources"][0]["search_backend"] == "ddgs"
    assert artifact is not None
    assert artifact.kind == "web_source"
    assert artifact.source_tool == "web_research"
    assert artifact.metadata["coverage"]["target_met"] is False
    assert artifact.metadata["coverage"]["queries_without_successful_fetch"] == ["sqlite pricing"]


def test_web_research_evidence_without_sources_builds_no_web_source_artifact():
    result = json.dumps(
        {
            "type": "web_research",
            "query": "sqlite",
            "sources": [],
            "fetched_sources": [],
            "source_count": 0,
            "fetched_count": 0,
            "coverage": {"target_fetch_count": 2, "target_met": False, "fetched_count": 0},
        },
        ensure_ascii=False,
    )

    evidence = build_tool_evidence("web_research", {"query": "sqlite"}, result, ok=True)

    assert evidence.ok is False
    assert build_task_artifact(evidence) is None


def test_web_source_artifact_requires_traceable_source_metadata():
    evidence = ToolEvidence(name="web_research", ok=True, metadata={"source_count": 0})

    assert build_task_artifact(evidence) is None
