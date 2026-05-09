import asyncio
import json

from opensprite.agent.task_artifact import build_task_artifact
from opensprite.config.schema import WebFetchToolConfig, WebSearchToolConfig
from opensprite.tools.evidence import build_tool_evidence
from opensprite.tools.web_search import _format_results
from opensprite.tools.web_research import WebResearchTool


class _FakeSearchTool:
    provider = "duckduckgo"

    def __init__(self, items):
        self.items = items
        self.calls = []

    async def _execute(self, query, count=None, freshness=None):
        self.calls.append({"query": query, "count": count, "freshness": freshness})
        return _format_results(query, self.items, count or len(self.items), provider=self.provider)


class _FakeFetchTool:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    async def _execute(self, url, max_chars=None):
        self.calls.append({"url": url, "max_chars": max_chars})
        payload = self.payloads[url]
        return json.dumps(payload, ensure_ascii=False)


def _fetch_payload(url, *, title="Fetched", content="x" * 900, too_short=False):
    return {
        "type": "web_fetch",
        "query": url,
        "url": url,
        "final_url": f"{url}?ref=1",
        "title": title,
        "content": content,
        "summary": title,
        "provider": "web_fetch",
        "extractor": "trafilatura",
        "status": 200,
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
    assert payload["fetched_count"] == 2
    assert payload["fetched_sources"][0]["source_query"] == "sqlite fts"
    assert payload["fetched_sources"][0]["search_rank"] == 1
    assert payload["sources"][1]["tool_name"] == "web_fetch"


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


def test_web_research_evidence_builds_web_source_artifact_with_fetch_detail():
    result = json.dumps(
        {
            "type": "web_research",
            "query": "sqlite",
            "provider": "duckduckgo",
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "title": "SQLite Docs",
                    "url": "https://sqlite.org/fts5.html",
                    "content": "SQLite FTS5 documentation " * 40,
                    "content_chars": 1000,
                    "is_too_short": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                }
            ],
        },
        ensure_ascii=False,
    )

    evidence = build_tool_evidence("web_research", {"query": "sqlite"}, result, ok=True)
    artifact = build_task_artifact(evidence)

    assert evidence.metadata["source_count"] == 1
    assert evidence.metadata["sources"][0]["tool_name"] == "web_fetch"
    assert artifact is not None
    assert artifact.kind == "web_source"
    assert artifact.source_tool == "web_research"
