import asyncio
import json

from opensprite.config.schema import WebSearchToolConfig
from opensprite.tools.web_search import WebSearchTool
from opensprite.tools.web_search import _format_results


class _FakeDuckDuckGoResponse:
    def __init__(self, text: str, url: str):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


class _FakeDuckDuckGoClient:
    def __init__(self):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None, timeout=None):
        self.requests.append(("get", url, params))
        return _FakeDuckDuckGoResponse(
            """
            <html><body><table>
              <tr><td><a class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fone">One</a></td></tr>
              <tr><td class="result-snippet">First snippet</td></tr>
              <tr><td><span class="link-text">example.com/one</span></td></tr>
            </table>
            <form action="/lite/" method="post">
              <input type="hidden" name="q" value="sqlite">
              <input type="hidden" name="s" value="1">
              <input type="submit" value="Next Page &gt;">
            </form></body></html>
            """,
            f"{url}?q={params['q']}",
        )

    async def post(self, url, data=None, timeout=None):
        self.requests.append(("post", url, data))
        return _FakeDuckDuckGoResponse(
            """
            <html><body><table>
              <tr><td><a class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com%2Ftwo">Two</a></td></tr>
              <tr><td class="result-snippet">Second snippet</td></tr>
              <tr><td><span class="link-text">example.com/two</span></td></tr>
            </table></body></html>
            """,
            url,
        )


class _StaticDuckDuckGoClient:
    def __init__(self, text: str):
        self.text = text
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None, timeout=None):
        self.requests.append(("get", url, params))
        return _FakeDuckDuckGoResponse(self.text, url)

    async def post(self, url, data=None, timeout=None):
        self.requests.append(("post", url, data))
        return _FakeDuckDuckGoResponse(self.text, url)


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


def test_web_search_count_limit_comes_from_config():
    tool = WebSearchTool(config=WebSearchToolConfig(max_results=25))

    count_schema = tool.parameters["properties"]["count"]

    assert count_schema["maximum"] == 25
    assert count_schema["description"] == "Results (1-25)"


def test_web_search_execute_clamps_to_configured_max_results(monkeypatch):
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=25))
    requested_counts = []

    async def fake_search(query, n):
        requested_counts.append(n)
        return _format_results(query, [], n, provider="duckduckgo")

    monkeypatch.setattr(tool, "_search_duckduckgo", fake_search)

    asyncio.run(tool._execute("sqlite", count=50))

    assert requested_counts == [25]


def test_duckduckgo_search_follows_next_page(monkeypatch):
    fake_client = _FakeDuckDuckGoClient()
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=2))

    payload = json.loads(asyncio.run(tool._search_duckduckgo("sqlite", 2)))

    assert [item["title"] for item in payload["items"]] == ["One", "Two"]
    assert [item["content"] for item in payload["items"]] == ["First snippet", "Second snippet"]
    assert [request[0] for request in fake_client.requests] == ["get", "post"]
    assert fake_client.requests[1][2]["s"] == "1"


def test_duckduckgo_search_respects_configured_page_limit(monkeypatch):
    fake_client = _FakeDuckDuckGoClient()
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(
        config=WebSearchToolConfig(provider="duckduckgo", max_results=2, duckduckgo_max_pages=1)
    )

    payload = json.loads(asyncio.run(tool._search_duckduckgo("sqlite", 2)))

    assert [item["title"] for item in payload["items"]] == ["One"]
    assert [request[0] for request in fake_client.requests] == ["get"]


def test_duckduckgo_search_reports_block_page(monkeypatch):
    fake_client = _StaticDuckDuckGoClient("<html><body>Captcha: prove you are human</body></html>")
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo"))

    result = asyncio.run(tool._search_duckduckgo("sqlite", 2))

    assert result.startswith("Error: DuckDuckGo blocked the search for 'sqlite'")
    assert "configure another web_search provider" in result


def test_duckduckgo_search_reports_no_results(monkeypatch):
    fake_client = _StaticDuckDuckGoClient("<html><body>No matching results.</body></html>")
    monkeypatch.setattr(
        "opensprite.tools.web_search.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo"))

    result = asyncio.run(tool._search_duckduckgo("sqlite", 2))

    assert result == "Error: DuckDuckGo returned no results for 'sqlite'."
