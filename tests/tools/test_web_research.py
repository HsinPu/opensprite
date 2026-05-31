import asyncio
import json
from datetime import datetime

from opensprite.agent.task_artifact import build_task_artifact
from opensprite.config.schema import WebFetchToolConfig, WebSearchToolConfig
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

    payload = json.loads(asyncio.run(tool._execute("Qwen latest model 2026", count=5, fetch_count=1)))

    assert search.calls == [{"query": "Qwen latest model 2026", "count": 5, "freshness": "month"}]
    assert payload["freshness"] == "month"


def test_web_research_prefers_current_year_variant_for_current_stale_query():
    current_year = datetime.now().year
    stale_year = current_year - 1
    current_query = f"台積電 2330 股價 {current_year}年5月28日"
    stale_query = f"台積電 2330 股價 {stale_year}年5月28日"
    search = _FakeSearchToolByQuery(
        {
            current_query: [
                {"title": f"Current {current_year}", "url": "https://example.com/current", "content": "Current price"},
            ],
            stale_query: [
                {"title": f"Stale {stale_year}", "url": "https://example.com/stale", "content": "Old price"},
            ],
            "台積電 2330 今日股價 即時": [],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/current": _fetch_payload("https://example.com/current", title="Current"),
            "https://example.com/stale": _fetch_payload("https://example.com/stale", title="Stale"),
        }
    )
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(freshness="auto"),
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(
        asyncio.run(
            tool._execute(
                stale_query,
                queries=["台積電 2330 今日股價 即時"],
                count=2,
                fetch_count=1,
            )
        )
    )

    assert search.calls[0] == {"query": current_query, "count": 2, "freshness": "day"}
    assert search.calls[1] == {"query": stale_query, "count": 2, "freshness": "day"}
    assert payload["queries"][:2] == [current_query, stale_query]
    assert payload["fetched_sources"][0]["url"].startswith("https://example.com/current")


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


def test_web_research_normalizes_query_objects_before_validation():
    search = _FakeSearchToolByQuery(
        {
            "台積電 2330 目前股價 2026": [
                {"title": "台積電股價", "url": "https://example.com/2330", "content": "台積電 2330 股價"},
            ],
            "台積電 2330 即時報價": [],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/2330": _fetch_payload("https://example.com/2330", title="台積電股價"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(
            tool.execute_validated(
                {
                    "query": {"query": "台積電 2330 目前股價 2026"},
                    "queries": [{"q": "台積電 2330 即時報價"}],
                    "count": 2,
                    "fetch_count": 1,
                }
            )
        )
    )

    assert search.calls[:2] == [
        {"query": "台積電 2330 目前股價 2026", "count": 2, "freshness": "month"},
        {"query": "台積電 2330 即時報價", "count": 2, "freshness": "month"},
    ]
    assert payload["query"] == "台積電 2330 目前股價 2026"
    assert payload["queries"][:2] == ["台積電 2330 目前股價 2026", "台積電 2330 即時報價"]


def test_web_research_expands_and_prioritizes_market_quote_queries():
    search = _FakeSearchToolByQuery(
        {
            "台積電 2330 目前股價 2026": [
                {"title": "General news", "url": "https://news.example.com/tsmc", "content": "台積電新聞"},
            ],
            "台積電 2330 目前股價 2026 Yahoo Finance": [
                {"title": "台積電(2330.TW) 股價 - Yahoo股市", "url": "https://tw.stock.yahoo.com/quote/2330.TW", "content": "台積電 2330 股價 2355"},
            ],
            "台積電 2330 目前股價 2026 Yahoo 股市": [],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://news.example.com/tsmc": _fetch_payload("https://news.example.com/tsmc", title="General news"),
            "https://tw.stock.yahoo.com/quote/2330.TW": _fetch_payload("https://tw.stock.yahoo.com/quote/2330.TW", title="台積電(2330.TW) 股價 - Yahoo股市"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("台積電 2330 目前股價 2026", count=2, fetch_count=1)))

    assert [call["query"] for call in search.calls[:3]] == [
        "台積電 2330 目前股價 2026",
        "台積電 2330 目前股價 2026 Yahoo Finance",
        "台積電 2330 目前股價 2026 Yahoo 股市",
    ]
    assert fetch.calls[0]["url"] == "https://tw.stock.yahoo.com/quote/2330.TW"
    assert payload["fetched_sources"][0]["domain"] == "tw.stock.yahoo.com"


def test_web_research_penalizes_market_quote_candidates_for_other_symbols():
    search = _FakeSearchTool(
        [
            {
                "title": "TAL - Tal Education Group Adr Stock Price Forecast",
                "url": "https://stockscan.io/stocks/TAL/forecast",
                "content": "TAL stock price forecast and analyst price target.",
            },
            {
                "title": "Taiwan Semiconductor Manufacturing Company Limited (TSM)",
                "url": "https://finance.yahoo.com/quote/TSM/",
                "content": "Find the latest Taiwan Semiconductor Manufacturing Company Limited stock quote.",
            },
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://stockscan.io/stocks/TAL/forecast": _fetch_payload(
                "https://stockscan.io/stocks/TAL/forecast",
                title="TAL forecast",
            ),
            "https://finance.yahoo.com/quote/TSM/": _fetch_payload(
                "https://finance.yahoo.com/quote/TSM/",
                title="Taiwan Semiconductor Manufacturing Company Limited (TSM)",
            ),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("TSMC ADR stock price 2026", count=2, fetch_count=1)))

    assert fetch.calls[0]["url"] == "https://finance.yahoo.com/quote/TSM/"
    assert payload["fetched_sources"][0]["domain"] == "finance.yahoo.com"


def test_web_research_prioritizes_quote_pages_over_forums_and_forecasts():
    search = _FakeSearchTool(
        [
            {
                "title": "[閒聊] 2026/05/28 盤中閒聊 - 看板 Stock",
                "url": "https://www.ptt.best/bbs/Stock/M.1779928206.A.EA7.html",
                "content": "台積電盤中閒聊與推文。",
            },
            {
                "title": "TSMC STOCK PRICE PREDICTION 2026, 2027, 2028-2030",
                "url": "https://longforecast.com/tsm-stock",
                "content": "TSMC stock price prediction and forecast.",
            },
            {
                "title": "Taiwan Semiconductor Manufacturing Company Limited (TSM)",
                "url": "https://finance.yahoo.com/quote/TSM/",
                "content": "Find the latest Taiwan Semiconductor Manufacturing Company Limited stock quote.",
            },
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://www.ptt.best/bbs/Stock/M.1779928206.A.EA7.html": _fetch_payload(
                "https://www.ptt.best/bbs/Stock/M.1779928206.A.EA7.html",
                title="PTT discussion",
            ),
            "https://longforecast.com/tsm-stock": _fetch_payload(
                "https://longforecast.com/tsm-stock",
                title="TSMC stock forecast",
            ),
            "https://finance.yahoo.com/quote/TSM/": _fetch_payload(
                "https://finance.yahoo.com/quote/TSM/",
                title="Taiwan Semiconductor Manufacturing Company Limited (TSM)",
            ),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("台積電 TSMC 股票股價 最新報價 2026", count=3, fetch_count=1)))

    assert fetch.calls[0]["url"] == "https://finance.yahoo.com/quote/TSM/"
    assert payload["fetched_sources"][0]["domain"] == "finance.yahoo.com"


def test_web_research_prioritizes_quote_pages_over_stock_articles():
    search = _FakeSearchTool(
        [
            {
                "title": "3 Core AI Stocks to Buy With $1,000 and Hold",
                "url": "https://www.fool.com/investing/2026/05/31/3-core-ai-stocks-to-buy/",
                "content": "Taiwan Semiconductor Manufacturing is mentioned in this investing article.",
            },
            {
                "title": "TSM Stock Price - Taiwan Semiconductor Chart",
                "url": "https://www.tradingview.com/symbols/BCBA-TSM/",
                "content": "TSM stock price chart for Taiwan Semiconductor Manufacturing.",
            },
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://www.fool.com/investing/2026/05/31/3-core-ai-stocks-to-buy/": _fetch_payload(
                "https://www.fool.com/investing/2026/05/31/3-core-ai-stocks-to-buy/",
                title="Investing article",
            ),
            "https://www.tradingview.com/symbols/BCBA-TSM/": _fetch_payload(
                "https://www.tradingview.com/symbols/BCBA-TSM/",
                title="TSM Stock Price - Taiwan Semiconductor Chart",
            ),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("TSMC TSM stock price today 2026", count=2, fetch_count=1)))

    assert fetch.calls[0]["url"] == "https://www.tradingview.com/symbols/BCBA-TSM/"
    assert payload["fetched_sources"][0]["domain"] == "www.tradingview.com"


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


def test_web_research_prioritizes_official_domain_for_official_docs_query():
    search = _FakeSearchTool(
        [
            {
                "title": "Third Party OpenRouter Guide",
                "url": "https://example.com/openrouter-rate-limits",
                "content": "OpenRouter rate limits explained by a third party",
            },
            {
                "title": "OpenRouter Rate Limits",
                "url": "https://openrouter.ai/docs/api/reference/limits",
                "content": "Official OpenRouter API rate limits documentation",
            },
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/openrouter-rate-limits": _fetch_payload("https://example.com/openrouter-rate-limits"),
            "https://openrouter.ai/docs/api/reference/limits": _fetch_payload(
                "https://openrouter.ai/docs/api/reference/limits",
                title="OpenRouter Rate Limits",
            ),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(
            tool._execute(
                "OpenRouter rate limits official documentation",
                count=2,
                fetch_count=1,
                freshness="month",
            )
        )
    )

    assert [call["url"] for call in fetch.calls] == ["https://openrouter.ai/docs/api/reference/limits"]
    assert payload["fetched_sources"][0]["domain"] == "openrouter.ai"


def test_web_research_adds_official_site_query_for_official_docs_query():
    search = _FakeSearchToolByQuery(
        {
            "OpenRouter rate limits official documentation": [
                {
                    "title": "Third Party OpenRouter Guide",
                    "url": "https://example.com/openrouter-rate-limits",
                    "content": "OpenRouter rate limits explained by a third party",
                },
                {
                    "title": "OpenRouter Docs",
                    "url": "https://openrouter.ai/docs",
                    "content": "Official OpenRouter documentation index",
                },
            ],
            "site:openrouter.ai OpenRouter rate limits official documentation": [
                {
                    "title": "API Rate Limits",
                    "url": "https://openrouter.ai/docs/api/reference/limits",
                    "content": "Learn about OpenRouter API rate limits and quotas",
                },
            ],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://openrouter.ai/docs": _fetch_payload("https://openrouter.ai/docs", title="OpenRouter Docs"),
            "https://openrouter.ai/docs/api/reference/limits": _fetch_payload(
                "https://openrouter.ai/docs/api/reference/limits",
                title="API Rate Limits",
            ),
            "https://example.com/openrouter-rate-limits": _fetch_payload("https://example.com/openrouter-rate-limits"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(
            tool._execute(
                "OpenRouter rate limits official documentation",
                count=3,
                fetch_count=1,
                freshness="month",
            )
        )
    )

    assert [call["query"] for call in search.calls] == [
        "OpenRouter rate limits official documentation",
        "site:openrouter.ai OpenRouter rate limits official documentation",
    ]
    assert [call["url"] for call in fetch.calls] == ["https://openrouter.ai/docs/api/reference/limits"]
    assert payload["queries"] == [
        "OpenRouter rate limits official documentation",
        "site:openrouter.ai OpenRouter rate limits official documentation",
    ]


def test_web_research_fetches_official_domain_results_before_domain_diversity():
    search = _FakeSearchToolByQuery(
        {
            "OpenRouter official API base URL documentation": [
                {
                    "title": "Third Party OpenRouter Base URL",
                    "url": "https://example.com/openrouter-base-url",
                    "content": "Third-party OpenRouter base URL guide.",
                },
            ],
            "site:openrouter.ai OpenRouter official API base URL documentation": [
                {
                    "title": "API Reference Overview",
                    "url": "https://openrouter.ai/docs/api/reference/overview",
                    "content": "Official OpenRouter API reference overview.",
                },
                {
                    "title": "Quickstart",
                    "url": "https://openrouter.ai/docs/quickstart",
                    "content": "Official OpenRouter quickstart.",
                },
                {
                    "title": "Authentication",
                    "url": "https://openrouter.ai/docs/api/reference/authentication",
                    "content": "Official OpenRouter authentication docs.",
                },
            ],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://example.com/openrouter-base-url": _fetch_payload("https://example.com/openrouter-base-url"),
            "https://openrouter.ai/docs/api/reference/overview": _fetch_payload(
                "https://openrouter.ai/docs/api/reference/overview",
                title="API Reference Overview",
            ),
            "https://openrouter.ai/docs/quickstart": _fetch_payload(
                "https://openrouter.ai/docs/quickstart",
                title="Quickstart",
            ),
            "https://openrouter.ai/docs/api/reference/authentication": _fetch_payload(
                "https://openrouter.ai/docs/api/reference/authentication",
                title="Authentication",
            ),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(
            tool._execute(
                "OpenRouter official API base URL documentation",
                queries=["site:openrouter.ai OpenRouter official API base URL documentation"],
                count=4,
                fetch_count=3,
                freshness="month",
            )
        )
    )

    assert [call["url"] for call in fetch.calls[:3]] == [
        "https://openrouter.ai/docs/api/reference/overview",
        "https://openrouter.ai/docs/quickstart",
        "https://openrouter.ai/docs/api/reference/authentication",
    ]
    assert payload["coverage"]["fetched_domains"] == ["openrouter.ai"]


def test_web_research_does_not_treat_mirror_subdomains_as_official_docs():
    search = _FakeSearchToolByQuery(
        {
            "OpenRouter API request body parameters official docs": [
                {
                    "title": "OpenRouter API Mirror",
                    "url": "https://openrouter-api.yestool.org/docs/api/reference/overview",
                    "content": "Mirrored OpenRouter docs.",
                },
                {
                    "title": "OpenRouter hosted copy",
                    "url": "https://openrouter-docs.vercel.app/request-schema",
                    "content": "Hosted copy of OpenRouter docs.",
                },
                {
                    "title": "OpenRouter API Parameters",
                    "url": "https://openrouter.ai/docs/api/reference/parameters",
                    "content": "Official OpenRouter API parameters documentation.",
                },
            ],
            "site:openrouter.ai OpenRouter API request body parameters official docs": [
                {
                    "title": "Parameters",
                    "url": "https://openrouter.ai/docs/api/reference/parameters",
                    "content": "OpenRouter official parameters page.",
                },
            ],
        }
    )
    fetch = _FakeFetchTool(
        {
            "https://openrouter-api.yestool.org/docs/api/reference/overview": _fetch_payload(
                "https://openrouter-api.yestool.org/docs/api/reference/overview"
            ),
            "https://openrouter-docs.vercel.app/request-schema": _fetch_payload(
                "https://openrouter-docs.vercel.app/request-schema"
            ),
            "https://openrouter.ai/docs/api/reference/parameters": _fetch_payload(
                "https://openrouter.ai/docs/api/reference/parameters",
                title="Parameters",
            ),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(
        asyncio.run(
            tool._execute(
                "OpenRouter API request body parameters official docs",
                count=3,
                fetch_count=1,
                freshness="month",
            )
        )
    )

    assert [call["query"] for call in search.calls] == [
        "OpenRouter API request body parameters official docs",
        "site:openrouter.ai OpenRouter API request body parameters official docs",
    ]
    assert [call["url"] for call in fetch.calls] == ["https://openrouter.ai/docs/api/reference/parameters"]
    assert payload["fetched_sources"][0]["domain"] == "openrouter.ai"


def test_web_research_deprioritizes_platform_sources_for_fetching():
    search = _FakeSearchTool(
        [
            {"title": "Video Overview", "url": "https://www.youtube.com/watch?v=abc", "content": "Video snippet"},
            {"title": "Official Release Notes", "url": "https://vendor.test/release-notes", "content": "Release notes"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://vendor.test/release-notes": _fetch_payload("https://vendor.test/release-notes", title="Official"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute("latest release notes", count=2, fetch_count=1, freshness="month")))

    assert [call["url"] for call in fetch.calls] == ["https://vendor.test/release-notes"]
    assert payload["fetched_sources"][0]["domain"] == "vendor.test"


def test_web_research_deprioritizes_old_year_for_current_queries():
    current_year = datetime.now().year
    previous_year = current_year - 1
    search = _FakeSearchTool(
        [
            {"title": f"Market guide {previous_year}", "url": "https://old.test/guide", "content": "Old report"},
            {"title": "Current market guide", "url": "https://current.test/guide", "content": f"Updated {current_year} report"},
        ]
    )
    fetch = _FakeFetchTool(
        {
            "https://current.test/guide": _fetch_payload("https://current.test/guide", title="Current"),
        }
    )
    tool = WebResearchTool(search_tool=search, fetch_tool=fetch)

    payload = json.loads(asyncio.run(tool._execute(f"latest market guide {current_year}", count=2, fetch_count=1, freshness="month")))

    assert [call["url"] for call in fetch.calls] == ["https://current.test/guide"]
    assert payload["fetched_sources"][0]["domain"] == "current.test"


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


def test_web_research_always_searches_and_fetches_without_cached_knowledge_reuse():
    search = _FakeSearchTool([{"title": "SQLite Docs", "url": "https://sqlite.org/fts5.html", "content": "Official docs"}])
    fetch = _FakeFetchTool({"https://sqlite.org/fts5.html": _fetch_payload("https://sqlite.org/fts5.html")})
    tool = WebResearchTool(
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("sqlite fts", fetch_count=1, freshness="none")))

    assert search.calls == [{"query": "sqlite fts", "count": 25, "freshness": "none"}]
    assert [call["url"] for call in fetch.calls] == ["https://sqlite.org/fts5.html"]
    assert payload["provider"] == "duckduckgo"
    assert payload["fetched_count"] == 1
    assert "reused_count" not in payload
    assert "reuse_attempt" not in payload
    assert "reuse_source" not in payload["fetched_sources"][0]
    assert payload["fetched_sources"][0]["has_main_content"] is True
    assert payload["freshness"] == "none"
    assert payload["coverage"]["target_met"] is True
    assert payload["coverage"]["fetched_domains"] == ["sqlite.org"]


def test_web_research_uses_network_search_for_recent_queries():
    search = _FakeSearchTool(
        [{"title": "Fresh Qwen", "url": "https://example.com/fresh-qwen", "content": "Fresh snippet"}]
    )
    fetch = _FakeFetchTool({"https://example.com/fresh-qwen": _fetch_payload("https://example.com/fresh-qwen")})
    tool = WebResearchTool(
        search_config=WebSearchToolConfig(freshness="auto"),
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("Qwen latest model 2026", fetch_count=1)))

    assert search.calls == [{"query": "Qwen latest model 2026", "count": 25, "freshness": "month"}]
    assert "reuse_attempt" not in payload
    assert payload["fetched_sources"][0]["url"] == "https://example.com/fresh-qwen?ref=1"


def test_web_research_fetches_new_source_when_search_returns_fetchable_candidate():
    search = _FakeSearchTool(
        [{"title": "Fresh", "url": "https://example.com/fresh", "content": "Fresh snippet"}]
    )
    fetch = _FakeFetchTool({"https://example.com/fresh": _fetch_payload("https://example.com/fresh")})
    tool = WebResearchTool(
        search_tool=search,
        fetch_tool=fetch,
    )

    payload = json.loads(asyncio.run(tool._execute("sqlite fts", fetch_count=1)))

    assert search.calls == [{"query": "sqlite fts", "count": 25, "freshness": "month"}]
    assert [call["url"] for call in fetch.calls] == ["https://example.com/fresh"]
    assert "reused_count" not in payload
    assert "reuse_attempt" not in payload
    assert "reused" not in payload["fetched_sources"][0]


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
