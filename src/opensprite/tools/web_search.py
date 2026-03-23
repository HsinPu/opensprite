"""Web search tool - multi-provider support."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from loguru import logger

from .base import Tool

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return text


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    return re.sub(r'\s+', ' ', text).strip()


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
        self.proxy = proxy or self.config.get("proxy", "")

    @property
    def provider(self) -> str:
        return self.config.get("provider", "brave").strip().lower() or "brave"

    @property
    def brave_api_key(self) -> str:
        return self.config.get("brave_api_key", "") or os.environ.get("BRAVE_API_KEY", "")

    @property
    def tavily_api_key(self) -> str:
        return self.config.get("tavily_api_key", "") or os.environ.get("TAVILY_API_KEY", "")

    @property
    def jina_api_key(self) -> str:
        return self.config.get("jina_api_key", "") or os.environ.get("JINA_API_KEY", "")

    @property
    def max_results(self) -> int:
        return self.config.get("max_results", 10)

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        n = min(max(count or self.max_results, 1), 10)

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
        api_key = self.brave_api_key
        if not api_key:
            logger.warning("Brave API key not set, falling back to DuckDuckGo")
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
        api_key = self.tavily_api_key
        if not api_key:
            logger.warning("Tavily API key not set, falling back to DuckDuckGo")
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
            headers = {"User-Agent": USER_AGENT}
            if self.jina_api_key:
                headers["Authorization"] = f"Bearer {self.jina_api_key}"
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    f"https://s.jina.ai/http://duckduckgo.com/?q={query}&format=json",
                    headers=headers,
                    timeout=10.0
                )
            # Jina AI summarization endpoint
            return r.text
        except Exception as e:
            return f"Error: {e}"
