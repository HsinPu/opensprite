import asyncio
import builtins
import json
import sys
import types

from opensprite.config.schema import WebSearchToolConfig
from opensprite.tools.web_search import WebSearchTool
from opensprite.tools.web_search import (
    _effective_freshness,
    _format_error,
    _format_results,
    _freshness_params,
    _normalize_freshness,
)


class _FakeSearxngResponse:
    def __init__(self, results=None):
        self.results = results or [{"title": "One", "url": "https://example.com/one", "content": "First"}]

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": self.results}


class _FakeSearxngClient:
    def __init__(self):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None, timeout=None):
        self.requests.append((url, params))
        return _FakeSearxngResponse()


class _FakePagedSearxngClient:
    def __init__(self):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None, timeout=None):
        self.requests.append((url, params))
        page = int((params or {}).get("pageno") or 1)
        results_by_page = {
            1: [{"title": "One", "url": "https://example.com/one", "content": "First"}],
            2: [{"title": "Two", "url": "https://example.com/two", "content": "Second"}],
            3: [{"title": "Three", "url": "https://example.com/three", "content": "Third"}],
        }
        return _FakeSearxngResponse(results_by_page.get(page, []))


def _install_fake_ddgs(monkeypatch, *, text_results=None, text_raises=None):
    fake = types.ModuleType("ddgs")
    fake.calls = []

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def text(self, query, **kwargs):
            fake.calls.append((query, kwargs))
            if text_raises is not None:
                raise text_raises
            yield from (text_results or [])

    fake.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake)
    return fake


def _disable_ddgs(monkeypatch):
    monkeypatch.delitem(sys.modules, "ddgs", raising=False)
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "ddgs":
            raise ImportError("blocked for test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)


def test_format_results_returns_structured_json_payload():
    payload = _format_results(
        "sqlite fts5",
        [
            {
                "title": "<b>SQLite FTS5</b>",
                "url": "https://sqlite.org/fts5.html",
                "content": "Official   <em>full text</em> search docs",
            }
        ],
        5,
        provider="duckduckgo",
    )

    parsed = json.loads(payload)

    assert parsed == {
        "type": "web_search",
        "query": "sqlite fts5",
        "url": "",
        "final_url": "",
        "title": "",
        "content": "",
        "summary": "Search results for: sqlite fts5",
        "provider": "duckduckgo",
        "extractor": "search",
        "status": None,
        "truncated": False,
        "content_type": "application/json",
        "items": [
            {
                "title": "SQLite FTS5",
                "url": "https://sqlite.org/fts5.html",
                "content": "Official full text search docs",
            }
        ],
    }


def test_format_results_includes_optional_metadata():
    payload = _format_results(
        "sqlite fts5",
        [{"title": "SQLite", "url": "https://sqlite.org/", "content": ""}],
        1,
        provider="duckduckgo",
        backend="ddgs",
    )

    assert json.loads(payload)["backend"] == "ddgs"


def test_format_error_returns_structured_json_payload():
    payload = _format_error("sqlite fts", "duckduckgo", "DuckDuckGo returned no results")

    parsed = json.loads(payload)

    assert parsed["type"] == "web_search"
    assert parsed["ok"] is False
    assert parsed["query"] == "sqlite fts"
    assert parsed["provider"] == "duckduckgo"
    assert parsed["items"] == []
    assert parsed["error"] == "Error: DuckDuckGo returned no results"


def test_web_search_count_limit_comes_from_config():
    tool = WebSearchTool(config=WebSearchToolConfig(max_results=25))

    count_schema = tool.parameters["properties"]["count"]
    freshness_schema = tool.parameters["properties"]["freshness"]

    assert count_schema["maximum"] == 25
    assert count_schema["default"] == 25
    assert count_schema["description"] == "Results (1-25)"
    assert freshness_schema["default"] == "auto"
    assert freshness_schema["enum"] == ["auto", "none", "day", "week", "month", "year"]


def test_web_search_execute_clamps_to_configured_max_results(monkeypatch):
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=25))
    requested_counts = []

    async def fake_search(query, n, freshness):
        requested_counts.append((n, freshness))
        return _format_results(query, [], n, provider="duckduckgo")

    monkeypatch.setattr(tool, "_search_duckduckgo", fake_search)

    asyncio.run(tool._execute("sqlite", count=50))

    assert requested_counts == [(25, "month")]


def test_web_search_execute_allows_freshness_override(monkeypatch):
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", freshness="year"))
    requested_freshness = []

    async def fake_search(query, n, freshness):
        requested_freshness.append(freshness)
        return _format_results(query, [], n, provider="duckduckgo")

    monkeypatch.setattr(tool, "_search_duckduckgo", fake_search)

    asyncio.run(tool._execute("sqlite docs", freshness="none"))

    assert requested_freshness == ["none"]


def test_web_search_execute_uses_auto_freshness_default_for_latest_query(monkeypatch):
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", freshness="auto"))
    requested_freshness = []

    async def fake_search(query, n, freshness):
        requested_freshness.append(freshness)
        return _format_results(query, [], n, provider="duckduckgo")

    monkeypatch.setattr(tool, "_search_duckduckgo", fake_search)

    asyncio.run(tool._execute("Qwen latest model 2026"))

    assert requested_freshness == ["month"]


def test_web_search_execute_uses_auto_freshness_default_for_chinese_query(monkeypatch):
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", freshness="auto"))
    requested_freshness = []

    async def fake_search(query, n, freshness):
        requested_freshness.append(freshness)
        return _format_results(query, [], n, provider="duckduckgo")

    monkeypatch.setattr(tool, "_search_duckduckgo", fake_search)

    asyncio.run(tool._execute("現在寫 code 最好的語言模型"))

    assert requested_freshness == ["month"]


def test_web_search_execute_respects_any_time_for_latest_query(monkeypatch):
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", freshness="none"))
    requested_freshness = []

    async def fake_search(query, n, freshness):
        requested_freshness.append(freshness)
        return _format_results(query, [], n, provider="duckduckgo")

    monkeypatch.setattr(tool, "_search_duckduckgo", fake_search)

    asyncio.run(tool._execute("Qwen latest model 2026"))

    assert requested_freshness == ["none"]


def test_web_search_freshness_aliases_and_provider_params():
    assert _normalize_freshness("latest", "year") == "month"
    assert _normalize_freshness("all", "year") == "none"
    assert _effective_freshness(None, "auto", query="latest Qwen models") == "month"
    assert _effective_freshness(None, "auto", query="現在寫 code 最好的語言模型") == "month"
    assert _effective_freshness(None, "auto", query="sqlite docs") == "month"
    assert _effective_freshness("year", "auto", query="latest Qwen models") == "year"
    assert _effective_freshness("day", "auto", query="latest Qwen models") == "day"
    assert _effective_freshness("none", "auto", query="latest Qwen models") == "none"
    assert _effective_freshness("none", "auto", query="sqlite docs") == "none"
    assert _effective_freshness("none", "year", query="sqlite docs") == "none"
    assert _freshness_params("duckduckgo", "week") == {"df": "w"}
    assert _freshness_params("searxng", "year") == {"time_range": "year"}
    assert _freshness_params("searxng", "none") == {}


def test_searxng_search_accepts_search_endpoint_base_url(monkeypatch):
    fake_client = _FakeSearxngClient()
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(config=WebSearchToolConfig(provider="searxng", searxng_url="https://searx.test/search"))

    payload = json.loads(asyncio.run(tool._search_searxng("sqlite", 1, "none")))

    assert payload["items"][0]["title"] == "One"
    assert fake_client.requests[0][0] == "https://searx.test/search"
    assert fake_client.requests[0][1]["pageno"] == 1


def test_searxng_search_sends_configured_engines_and_categories(monkeypatch):
    fake_client = _FakeSearxngClient()
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(
        config=WebSearchToolConfig(
            provider="searxng",
            searxng_engines=["google", "bing"],
            searxng_categories=["general", "news"],
        )
    )

    json.loads(asyncio.run(tool._search_searxng("sqlite", 1, "none")))

    assert fake_client.requests[0][1]["engines"] == "google,bing"
    assert fake_client.requests[0][1]["categories"] == "general,news"


def test_searxng_search_fetches_multiple_pages(monkeypatch):
    fake_client = _FakePagedSearxngClient()
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(config=WebSearchToolConfig(provider="searxng", max_results=3, searxng_max_pages=3))

    payload = json.loads(asyncio.run(tool._search_searxng("sqlite", 3, "week")))

    assert payload["freshness"] == "week"
    assert [item["title"] for item in payload["items"]] == ["One", "Two", "Three"]
    assert [request[1]["pageno"] for request in fake_client.requests] == [1, 2, 3]
    assert all(request[1]["time_range"] == "week" for request in fake_client.requests)


def test_searxng_search_respects_configured_page_limit(monkeypatch):
    fake_client = _FakePagedSearxngClient()
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(config=WebSearchToolConfig(provider="searxng", max_results=3, searxng_max_pages=1))

    payload = json.loads(asyncio.run(tool._search_searxng("sqlite", 3, "none")))

    assert [item["title"] for item in payload["items"]] == ["One"]
    assert [request[1]["pageno"] for request in fake_client.requests] == [1]


def test_duckduckgo_search_prefers_ddgs_package(monkeypatch):
    fake = _install_fake_ddgs(
        monkeypatch,
        text_results=[
            {"title": "Qwen latest", "href": "https://qwen.ai/blog/", "body": "Recent Qwen updates"},
            {"title": "Qwen models", "url": "https://huggingface.co/Qwen", "body": "Model releases"},
        ],
    )

    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=2))

    payload = json.loads(asyncio.run(tool._search_duckduckgo("Qwen latest model", 2, "month")))

    assert payload["provider"] == "duckduckgo"
    assert payload["backend"] == "ddgs"
    assert payload["freshness"] == "month"
    assert [item["url"] for item in payload["items"]] == [
        "https://qwen.ai/blog/",
        "https://huggingface.co/Qwen",
    ]
    assert fake.calls == [("Qwen latest model", {"max_results": 2, "timelimit": "m"})]


def test_duckduckgo_search_reports_missing_ddgs(monkeypatch):
    _disable_ddgs(monkeypatch)
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=1))

    payload = json.loads(asyncio.run(tool._search_duckduckgo("sqlite", 1, "none")))

    assert payload["ok"] is False
    assert payload["provider"] == "duckduckgo"
    assert payload["backend"] == "ddgs"
    assert payload["freshness"] == "none"
    assert payload["items"] == []
    assert "ddgs package is not installed" in payload["error"]


def test_duckduckgo_search_reports_ddgs_no_results(monkeypatch):
    fake = _install_fake_ddgs(monkeypatch, text_results=[])
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=1))

    payload = json.loads(asyncio.run(tool._search_duckduckgo("sqlite", 1, "none")))

    assert payload["ok"] is False
    assert payload["provider"] == "duckduckgo"
    assert payload["backend"] == "ddgs"
    assert payload["freshness"] == "none"
    assert payload["items"] == []
    assert payload["error"] == "Error: DDGS returned no results for 'sqlite'."
    assert fake.calls == [("sqlite", {"max_results": 1})]


def test_duckduckgo_search_reports_ddgs_runtime_error(monkeypatch):
    _install_fake_ddgs(monkeypatch, text_raises=RuntimeError("rate limited 202"))
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=1))

    payload = json.loads(asyncio.run(tool._search_duckduckgo("sqlite", 1, "week")))

    assert payload["ok"] is False
    assert payload["provider"] == "duckduckgo"
    assert payload["backend"] == "ddgs"
    assert payload["freshness"] == "week"
    assert "rate limited 202" in payload["error"]
