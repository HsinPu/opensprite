import json
import asyncio

import pytest

from opensprite.tools.web_fetch import WebFetcher, WebFetchTool


class _FakeFetcher:
    def __init__(
        self,
        max_chars=50000,
        max_response_size=5242880,
        timeout=30,
        prefer_trafilatura=True,
        firecrawl_api_key=None,
    ):
        self.max_chars = max_chars
        self.max_response_size = max_response_size
        self.timeout = timeout
        self.prefer_trafilatura = prefer_trafilatura
        self.firecrawl_api_key = firecrawl_api_key

    def fetch(self, url: str):
        return {
            "url": url,
            "finalUrl": f"{url}?ref=1",
            "status": 200,
            "title": "SQLite FTS5",
            "extractor": "trafilatura",
            "contentType": "text/html",
            "truncated": False,
            "text": "SQLite FTS5 supports full text search.",
        }


def test_web_fetch_returns_unified_web_payload(monkeypatch):
    monkeypatch.setattr("opensprite.tools.web_fetch.WebFetcher", lambda *args, **kwargs: _FakeFetcher())
    tool = WebFetchTool()

    payload = json.loads(asyncio.run(tool._execute("https://sqlite.org/fts5.html")))

    assert payload == {
        "type": "web_fetch",
        "query": "https://sqlite.org/fts5.html",
        "url": "https://sqlite.org/fts5.html",
        "final_url": "https://sqlite.org/fts5.html?ref=1",
        "title": "SQLite FTS5",
        "content": "SQLite FTS5 supports full text search.",
        "summary": "SQLite FTS5",
        "provider": "web_fetch",
        "extractor": "trafilatura",
        "status": 200,
        "content_type": "text/html",
        "truncated": False,
        "items": [],
    }


def test_web_fetch_parameter_default_uses_configured_max_chars():
    tool = WebFetchTool(max_chars=1234)

    max_chars_schema = tool.parameters["properties"]["max_chars"]

    assert max_chars_schema["default"] == 1234
    assert max_chars_schema["minimum"] == 1


def test_web_fetch_execute_uses_configured_max_chars_by_default(monkeypatch):
    created_fetchers = []

    def fake_fetcher(*args, **kwargs):
        fetcher = _FakeFetcher(**kwargs)
        created_fetchers.append(fetcher)
        return fetcher

    monkeypatch.setattr("opensprite.tools.web_fetch.WebFetcher", fake_fetcher)
    tool = WebFetchTool(max_chars=1234)

    asyncio.run(tool._execute("https://sqlite.org/fts5.html"))

    assert created_fetchers[-1].max_chars == 1234


def test_web_fetch_execute_uses_configured_max_response_size(monkeypatch):
    created_fetchers = []

    def fake_fetcher(*args, **kwargs):
        fetcher = _FakeFetcher(**kwargs)
        created_fetchers.append(fetcher)
        return fetcher

    monkeypatch.setattr("opensprite.tools.web_fetch.WebFetcher", fake_fetcher)
    tool = WebFetchTool(max_response_size=2048)

    asyncio.run(tool._execute("https://sqlite.org/fts5.html"))

    assert created_fetchers[-1].max_response_size == 2048


def test_web_fetch_execute_allows_max_chars_override(monkeypatch):
    created_fetchers = []

    def fake_fetcher(*args, **kwargs):
        fetcher = _FakeFetcher(**kwargs)
        created_fetchers.append(fetcher)
        return fetcher

    monkeypatch.setattr("opensprite.tools.web_fetch.WebFetcher", fake_fetcher)
    tool = WebFetchTool(max_chars=1234)

    asyncio.run(tool._execute("https://sqlite.org/fts5.html", max_chars=4321))

    assert created_fetchers[-1].max_chars == 4321


def test_web_fetcher_enforces_configured_response_size(monkeypatch):
    monkeypatch.setattr(
        "opensprite.tools.web_fetch.fetch_url",
        lambda *args, **kwargs: ("text/plain", b"abc", 200),
    )
    fetcher = WebFetcher(max_response_size=2)

    with pytest.raises(Exception, match="exceeds 2 bytes limit"):
        fetcher.fetch("https://example.com")
