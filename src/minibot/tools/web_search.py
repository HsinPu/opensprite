"""Web search and fetch tools - multi-provider support."""

from __future__ import annotations

import html
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from minibot.tools.base import Tool

# Shared constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return text


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    return re.sub(r'\s+', ' ', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL format."""
    try:
        result = urlparse(url)
        if not result.scheme or not result.netloc:
            return False, "Missing scheme or netloc"
        if result.scheme not in ("http", "https"):
            return False, f"Unsupported scheme: {result.scheme}"
        return True, ""
    except Exception as e:
        return False, str(e)


def _format_results(query: str, items: list[dict[str, Any]], n: int) -> str:
    """Format search results into plaintext."""
    if not items:
        return f"No results for: {query}"
    lines = [f"Results for: {query}\n"]
    for i, item in enumerate(items[:n], 1):
        title = _normalize(_strip_tags(item.get("title", "")))
        snippet = _normalize(_strip_tags(item.get("content", "")))
        lines.append(f"{i}. {title}\n   {item.get('url', '')}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


class WebSearchTool(Tool):
    """Search the web using configured provider."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets. Supports Brave, DuckDuckGo, Tavily, SearXNG, Jina."

    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Results (1-10)", "minimum": 1, "maximum": 10}
        },
        "required": ["query"]
    }

    def __init__(self, config: dict | None = None, proxy: str | None = None):
        self.config = config or {}
        self.proxy = proxy

    @property
    def provider(self) -> str:
        return self.config.get("provider", "brave").strip().lower() or "brave"

    @property
    def api_key(self) -> str:
        return self.config.get("api_key", "") or os.environ.get("BRAVE_API_KEY", "")

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        n = min(max(count or 10, 1), 10)

        provider = self.provider

        if provider == "duckduckgo":
            return await self._search_duckduckgo(query, n)
        elif provider == "tavily":
            return await self._search_tavily(query, n)
        elif provider == "searxng":
            return await self._search_searxng(query, n)
        elif provider == "jina":
            return await self._search_jina(query, n)
        elif provider == "brave":
            return await self._search_brave(query, n)
        else:
            return f"Error: unknown search provider '{provider}'"

    async def _search_brave(self, query: str, n: int) -> str:
        api_key = self.api_key
        if not api_key:
            logger.warning("BRAVE_API_KEY not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        try:
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": n},
                    headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                    timeout=10.0
                )
                r.raise_for_status()
            items = [
                {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("description", "")}
                for x in r.json().get("web", {}).get("results", [])
            ]
            return _format_results(query, items, n)
        except Exception as e:
            return f"Error: {e}"

    async def _search_duckduckgo(self, query: str, n: int) -> str:
        try:
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    "https://duckduckgo.com/",
                    params={"q": query, "format": "json"},
                    timeout=10.0
                )
            # DuckDuckGo HTML parsing
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for i, a in enumerate(soup.select("a.result__a")):
                if i >= n:
                    break
                results.append({
                    "title": a.get_text(strip=True),
                    "url": a.get("href", ""),
                    "content": ""
                })
            return _format_results(query, results, n)
        except Exception as e:
            return f"Error: {e}"

    async def _search_tavily(self, query: str, n: int) -> str:
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TAVILY_API_KEY not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        try:
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"query": query, "max_results": n},
                    timeout=15.0
                )
                r.raise_for_status()
            items = [{"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")} 
                     for x in r.json().get("results", [])]
            return _format_results(query, items, n)
        except Exception as e:
            return f"Error: {e}"

    async def _search_searxng(self, query: str, n: int) -> str:
        base_url = self.config.get("searxng_url", "https://searx.be")
        try:
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    f"{base_url}/search",
                    params={"q": query, "format": "json"},
                    timeout=10.0
                )
            items = [{"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")}
                     for x in r.json().get("results", [])[:n]]
            return _format_results(query, items, n)
        except Exception as e:
            return f"Error: {e}"

    async def _search_jina(self, query: str, n: int) -> str:
        try:
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    f"https://s.jina.ai/http://duckduckgo.com/?q={query}&format=json",
                    timeout=10.0
                )
            # Jina AI summarization endpoint
            return r.text
        except Exception as e:
            return f"Error: {e}"


class WebFetchTool(Tool):
    """Fetch and extract readable content from URLs."""

    name = "web_fetch"
    description = "Fetch a URL and extract readable content. Returns title, text, and metadata."

    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "max_chars": {"type": "integer", "description": "Max characters to return", "default": 5000}
        },
        "required": ["url"]
    }

    def __init__(self, config: dict | None = None, proxy: str | None = None):
        self.config = config or {}
        self.proxy = proxy

    async def execute(self, url: str, max_chars: int = 5000, **kwargs: Any) -> str:
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url})

        # Try Jina Reader first
        result = await self._fetch_jina(url, max_chars)
        if result is not None:
            return result

        # Fallback to readability
        return await self._fetch_readability(url, max_chars)

    async def _fetch_jina(self, url: str, max_chars: int) -> str | None:
        """Try fetching via Jina Reader API."""
        try:
            headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
            jina_key = os.environ.get("JINA_API_KEY", "")
            if jina_key:
                headers["Authorization"] = f"Bearer {jina_key}"

            async with httpx.AsyncClient(proxy=self.proxy, timeout=20.0) as client:
                r = await client.get(f"https://r.jina.ai/{url}", headers=headers)
                if r.status_code == 429:
                    logger.debug("Jina Reader rate limited")
                    return None
                r.raise_for_status()

            data = r.json().get("data", {})
            title = data.get("title", "")
            text = data.get("content", "")
            if not text:
                return None

            if title:
                text = f"# {title}\n\n{text}"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps({
                "url": url, "finalUrl": data.get("url", url), "status": r.status_code,
                "extractor": "jina", "truncated": truncated, "length": len(text), "text": text
            }, ensure_ascii=False)
        except Exception as e:
            logger.debug("Jina Reader failed: {}", e)
            return None

    async def _fetch_readability(self, url: str, max_chars: int) -> str:
        """Fallback using readability-lxml."""
        from readability import Document

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=30.0,
                proxy=self.proxy
            ) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()

            ctype = r.headers.get("content-type", "")

            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2, ensure_ascii=False), "json"
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = _strip_tags(doc.summary())
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text[:max_chars], "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps({
                "url": url, "finalUrl": str(r.url), "status": r.status_code,
                "extractor": extractor, "truncated": truncated, "length": len(text), "text": text
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})
