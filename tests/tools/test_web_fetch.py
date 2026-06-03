import json
import asyncio
import socket
import gzip

import pytest

from opensprite.tools.web_blocking import looks_blocked_or_challenge
from opensprite.tools.web_fetch import WebFetcher, WebFetchTool, _do_fetch, validate_url


def _public_getaddrinfo(host, port=None, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]


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
        "content_chars": 38,
        "has_title": True,
        "has_main_content": False,
        "is_too_short": True,
        "blocked_or_challenge": False,
        "min_content_chars": 800,
        "items": [],
    }


def test_web_fetch_marks_blocked_challenge_payload(monkeypatch):
    class _BlockedFetcher(_FakeFetcher):
        def fetch(self, url: str):
            result = super().fetch(url)
            result.update(
                {
                    "status": 403,
                    "title": "Access Denied",
                    "text": "Captcha: verify you are human before continuing.",
                }
            )
            return result

    monkeypatch.setattr("opensprite.tools.web_fetch.WebFetcher", lambda *args, **kwargs: _BlockedFetcher())
    tool = WebFetchTool()

    payload = json.loads(asyncio.run(tool._execute("https://example.com/blocked")))

    assert payload["blocked_or_challenge"] is True
    assert payload["has_main_content"] is False
    assert payload["is_too_short"] is True


def test_web_fetch_does_not_treat_rate_limit_topic_as_blocked(monkeypatch):
    class _RateLimitDocsFetcher(_FakeFetcher):
        def fetch(self, url: str):
            result = super().fetch(url)
            result.update(
                {
                    "title": "API Rate Limits",
                    "text": "This documentation explains API rate limits, quotas, and usage controls." * 20,
                }
            )
            return result

    monkeypatch.setattr("opensprite.tools.web_fetch.WebFetcher", lambda *args, **kwargs: _RateLimitDocsFetcher())
    tool = WebFetchTool()

    payload = json.loads(asyncio.run(tool._execute("https://example.com/rate-limits")))

    assert payload["blocked_or_challenge"] is False
    assert payload["has_main_content"] is True


def test_web_blocking_rule_combines_status_and_challenge_text():
    assert looks_blocked_or_challenge(title="Anything", content="Regular page", status=403) is True
    assert (
        looks_blocked_or_challenge(
            title="Security Check",
            content="Please verify you are human before continuing.",
            status=200,
        )
        is True
    )
    assert (
        looks_blocked_or_challenge(
            title="API Rate Limits",
            content="This documentation explains rate limits and quotas.",
            status=200,
        )
        is False
    )


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


def test_web_fetch_execute_runs_fetcher_in_thread(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args):
        calls.append((func, args))
        return func(*args)

    monkeypatch.setattr("opensprite.tools.web_fetch.WebFetcher", lambda *args, **kwargs: _FakeFetcher())
    monkeypatch.setattr("opensprite.tools.web_fetch.asyncio.to_thread", fake_to_thread)
    tool = WebFetchTool()

    payload = json.loads(asyncio.run(tool._execute("https://sqlite.org/fts5.html")))

    assert payload["url"] == "https://sqlite.org/fts5.html"
    assert len(calls) == 1
    assert calls[0][1] == ("https://sqlite.org/fts5.html",)


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


def test_web_fetcher_passes_configured_response_size_to_fetch_layer(monkeypatch):
    captured = {}

    def fake_fetch_url(*args, **kwargs):
        captured["max_response_size"] = args[3]
        return "text/plain", b"ab", 200, "https://example.com"

    monkeypatch.setattr("opensprite.tools.web_fetch.fetch_url", fake_fetch_url)
    fetcher = WebFetcher(max_response_size=2)

    fetcher.fetch("https://example.com")

    assert captured["max_response_size"] == 2


def test_web_fetcher_uses_jina_fallback_for_403(monkeypatch):
    def fake_fetch_url(*args, **kwargs):
        raise Exception("HTTP Error: 403 Forbidden")

    monkeypatch.setattr("opensprite.tools.web_fetch.fetch_url", fake_fetch_url)
    monkeypatch.setattr(
        "opensprite.tools.web_fetch.extract_with_jina",
        lambda url, timeout=20: {"title": "Readable fallback", "text": "Fallback content from reader."},
    )
    fetcher = WebFetcher(max_chars=500)

    result = fetcher.fetch("https://example.com/blocked")

    assert result["status"] == 403
    assert result["extractor"] == "jina"
    assert result["title"] == "Readable fallback"
    assert result["text"] == "Fallback content from reader."


def test_web_fetcher_recovers_openrouter_legacy_docs_markdown_not_found(monkeypatch):
    calls = []

    def fake_fetch_url(url, *args, **kwargs):
        calls.append(url)
        if "/docs/api-reference/" in url:
            return (
                "text/plain; charset=utf-8",
                b"# Page Not Found\n\nThis page does not exist.",
                200,
                f"{url}.md",
            )
        return (
            "text/markdown; charset=utf-8",
            b"# Parameters\n\n## Max Tokens\n\nThis sets the upper limit for generated tokens.",
            200,
            f"{url}.md",
        )

    monkeypatch.setattr("opensprite.tools.web_fetch.fetch_url", fake_fetch_url)
    fetcher = WebFetcher(max_chars=5000)

    result = fetcher.fetch("https://openrouter.ai/docs/api-reference/parameters")

    assert calls == [
        "https://openrouter.ai/docs/api-reference/parameters",
        "https://openrouter.ai/docs/api/reference/parameters",
    ]
    assert result["url"] == "https://openrouter.ai/docs/api/reference/parameters"
    assert result["finalUrl"] == "https://openrouter.ai/docs/api/reference/parameters.md"
    assert "Max Tokens" in result["text"]


def test_web_fetcher_recovers_openrouter_legacy_docs_after_extraction(monkeypatch):
    calls = []

    def fake_fetch_url(url, *args, **kwargs):
        calls.append(url)
        if "/docs/api-reference/" in url:
            return (
                "text/html; charset=utf-8",
                b"<html><body><main><h1>Page Not Found</h1><p>This page does not exist.</p></main></body></html>",
                200,
                f"{url}.md",
            )
        return (
            "text/markdown; charset=utf-8",
            b"# API Reference Overview\n\nThe API base URL is https://openrouter.ai/api/v1.",
            200,
            f"{url}.md",
        )

    def fake_extract_with_trafilatura(*args, **kwargs):
        return {"text": "# Page Not Found\n\nThis page does not exist.", "extractor": "trafilatura"}

    monkeypatch.setattr("opensprite.tools.web_fetch.fetch_url", fake_fetch_url)
    monkeypatch.setattr("opensprite.tools.web_fetch.extract_with_trafilatura", fake_extract_with_trafilatura)
    fetcher = WebFetcher(max_chars=5000)

    result = fetcher.fetch("https://openrouter.ai/docs/api-reference/overview")

    assert calls == [
        "https://openrouter.ai/docs/api-reference/overview",
        "https://openrouter.ai/docs/api/reference/overview",
    ]
    assert result["url"] == "https://openrouter.ai/docs/api/reference/overview"
    assert result["finalUrl"] == "https://openrouter.ai/docs/api/reference/overview.md"
    assert "https://openrouter.ai/api/v1" in result["text"]


def test_web_fetcher_uses_openrouter_full_docs_when_docs_index_is_shell(monkeypatch):
    calls = []

    def fake_fetch_url(url, *args, **kwargs):
        calls.append(url)
        if url.endswith("/docs/llms.txt"):
            return (
                "text/plain; charset=utf-8",
                (
                    "# OpenRouter documentation index\n\n"
                    "Authentication uses the Authorization header with a Bearer token, "
                    "for example Authorization: Bearer $OPENROUTER_API_KEY.\n"
                ).encode(),
                200,
                url,
            )
        return (
            "text/html; charset=utf-8",
            b"<html><body>No models found Models Fusion Chat Rankings Apps Enterprise Pricing Docs</body></html>",
            200,
            "https://openrouter.ai/docs.md",
        )

    monkeypatch.setattr("opensprite.tools.web_fetch.fetch_url", fake_fetch_url)
    fetcher = WebFetcher(max_chars=5000)

    result = fetcher.fetch("https://openrouter.ai/docs")

    assert calls == [
        "https://openrouter.ai/docs",
        "https://openrouter.ai/docs/llms.txt",
    ]
    assert result["url"] == "https://openrouter.ai/docs/llms.txt"
    assert result["finalUrl"] == "https://openrouter.ai/docs/llms.txt"
    assert "Authorization: Bearer" in result["text"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765",
        "http://localhost",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_validate_url_blocks_private_targets(url):
    with pytest.raises(Exception, match="blocked non-public IP address"):
        validate_url(url)


def test_validate_url_blocks_hosts_that_resolve_private(monkeypatch):
    monkeypatch.setattr(
        "opensprite.tools.web_fetch.socket.getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )

    with pytest.raises(Exception, match="blocked non-public IP address"):
        validate_url("https://example.com")


def test_do_fetch_blocks_private_final_url(monkeypatch):
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/plain"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "http://127.0.0.1/private"

        def read(self, size=-1):
            return b"ok"

    class FakeOpener:
        def open(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("opensprite.tools.web_fetch.socket.getaddrinfo", _public_getaddrinfo)
    monkeypatch.setattr("opensprite.tools.web_fetch.build_opener", lambda *args, **kwargs: FakeOpener())

    with pytest.raises(Exception, match="blocked non-public IP address"):
        _do_fetch("https://example.com", 30, "test-agent", 1024)


def test_do_fetch_stops_reading_when_response_exceeds_limit(monkeypatch):
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/plain"}

        def __init__(self):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "https://example.com/large"

        def read(self, size=-1):
            self.reads += 1
            return b"abc" if self.reads == 1 else b"def"

    class FakeOpener:
        def open(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("opensprite.tools.web_fetch.socket.getaddrinfo", _public_getaddrinfo)
    monkeypatch.setattr("opensprite.tools.web_fetch.build_opener", lambda *args, **kwargs: FakeOpener())

    with pytest.raises(Exception, match="exceeds 5 bytes limit"):
        _do_fetch("https://example.com/large", 30, "test-agent", 5)


def test_do_fetch_decompresses_gzip_response(monkeypatch):
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"}

        def __init__(self):
            self._content = gzip.compress(b"<html><body>Readable quote page</body></html>")
            self._read = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "https://example.com/quote"

        def read(self, size=-1):
            if self._read:
                return b""
            self._read = True
            return self._content

    class FakeOpener:
        def open(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("opensprite.tools.web_fetch.socket.getaddrinfo", _public_getaddrinfo)
    monkeypatch.setattr("opensprite.tools.web_fetch.build_opener", lambda *args, **kwargs: FakeOpener())

    content, status, headers, final_url = _do_fetch("https://example.com/quote", 30, "test-agent", 1024)

    assert content == b"<html><body>Readable quote page</body></html>"
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert final_url == "https://example.com/quote"
