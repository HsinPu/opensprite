"""Web search tool - multi-provider support."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import quote_plus

import httpx
from loguru import logger

from ..config.defaults import DEFAULT_WEB_SEARCH_PROVIDER, WEB_SEARCH_FRESHNESS_OPTIONS
from ..config.schema import WebSearchToolConfig
from ..utils.url import join_url_path
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
FRESHNESS_VALUES = WEB_SEARCH_FRESHNESS_OPTIONS
DUCKDUCKGO_FRESHNESS = {"day": "d", "week": "w", "month": "m", "year": "y"}
AUTO_FRESHNESS = "month"


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return text


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    return re.sub(r'\s+', ' ', text).strip()


def _normalize_proxy(proxy: Any) -> str | None:
    """Normalize optional proxy config for httpx."""
    if proxy is None:
        return None
    if isinstance(proxy, str):
        proxy = proxy.strip()
        return proxy or None
    return str(proxy)


def _normalize_freshness(value: Any, default: str = "year") -> str:
    """Normalize tool/config freshness into a provider-agnostic value."""
    raw = str(value if value is not None else default).strip().lower()
    aliases = {
        "": default,
        "all": "none",
        "any": "none",
        "off": "none",
        "false": "none",
        "today": "day",
        "d": "day",
        "daily": "day",
        "w": "week",
        "weekly": "week",
        "m": "month",
        "monthly": "month",
        "recent": "month",
        "latest": "month",
        "current": "month",
        "y": "year",
        "yearly": "year",
        "past_year": "year",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in FRESHNESS_VALUES else default


def _effective_freshness(value: Any, default: str = "year", *, query: Any = None) -> str:
    """Resolve auto freshness while respecting explicit tool/config settings."""
    normalized = _normalize_freshness(value, default)
    default_normalized = _normalize_freshness(default, "year")
    if value is not None and normalized != "auto":
        return normalized
    if default_normalized != "auto":
        return normalized
    return AUTO_FRESHNESS


def _freshness_params(provider: str, freshness: str) -> dict[str, str]:
    """Return provider-specific recency parameters for supported engines."""
    normalized = _normalize_freshness(freshness, default="none")
    if normalized in {"auto", "none"}:
        return {}
    if provider == "duckduckgo":
        return {"df": DUCKDUCKGO_FRESHNESS[normalized]}
    if provider == "searxng":
        return {"time_range": normalized}
    if provider == "jina":
        return {"df": DUCKDUCKGO_FRESHNESS[normalized]}
    return {}


def _clean_text_values(values: Any) -> list[str]:
    out: list[str] = []
    if isinstance(values, str):
        candidates = values.replace("\n", ",").split(",")
    elif isinstance(values, (list, tuple, set)):
        candidates = values
    else:
        candidates = []
    for value in candidates:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _searxng_scope_params(engines: Any, categories: Any) -> dict[str, str]:
    params: dict[str, str] = {}
    engine_values = _clean_text_values(engines)
    category_values = _clean_text_values(categories)
    if engine_values:
        params["engines"] = ",".join(engine_values)
    if category_values:
        params["categories"] = ",".join(category_values)
    return params


def _format_results(query: str, items: list[dict[str, Any]], n: int, *, provider: str, **metadata: Any) -> str:
    """Format search results into the shared web payload schema."""
    normalized_items: list[dict[str, str]] = []
    for item in items[:n]:
        normalized_items.append(
            {
                "title": _normalize(_strip_tags(str(item.get("title", "") or ""))),
                "url": str(item.get("url", "") or ""),
                "content": _normalize(_strip_tags(str(item.get("content", "") or ""))),
            }
        )
    payload = {
            "type": "web_search",
            "query": query,
            "url": "",
            "final_url": "",
            "title": "",
            "content": "",
            "summary": f"Search results for: {query}",
            "provider": provider,
            "extractor": "search",
            "status": None,
            "truncated": False,
            "content_type": "application/json",
            "items": normalized_items,
    }
    payload.update({key: value for key, value in metadata.items() if value is not None})
    return json.dumps(payload, ensure_ascii=False)


def _format_error(query: str, provider: str, error: str, **metadata: Any) -> str:
    """Format provider failures into the shared web payload schema."""
    payload = {
        "type": "web_search",
        "ok": False,
        "query": query,
        "url": "",
        "final_url": "",
        "title": "",
        "content": "",
        "summary": f"Search failed for: {query}",
        "provider": provider,
        "extractor": "search",
        "status": metadata.pop("status", None),
        "truncated": False,
        "content_type": "application/json",
        "items": [],
        "error": f"Error: {error}" if not str(error or "").startswith("Error:") else str(error),
    }
    payload.update({key: value for key, value in metadata.items() if value is not None})
    return json.dumps(payload, ensure_ascii=False)


class WebSearchTool(Tool):
    """Search the web using configured provider."""

    name = "web_search"
    description = "Search the web for external sources. The freshness setting controls recency: auto uses the configured default recent window, none searches all time, and fixed windows are respected. Returns structured JSON with titles, URLs, and snippets. Supports DuckDuckGo, SearXNG, Jina."

    def __init__(self, config: WebSearchToolConfig | None = None, proxy: str | None = None):
        self.config = config or WebSearchToolConfig()
        raw_proxy = proxy if proxy is not None else self.config.proxy
        self.proxy = _normalize_proxy(raw_proxy)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query", "pattern": NON_EMPTY_STRING_PATTERN},
                "count": {
                    "type": "integer",
                    "description": f"Results (1-{self.max_results})",
                    "default": self.max_results,
                    "minimum": 1,
                    "maximum": self.max_results,
                },
                "freshness": {
                    "type": "string",
                    "enum": list(FRESHNESS_VALUES),
                    "description": "Recency filter. auto uses the configured default recent window; none searches all time; fixed windows are respected.",
                    "default": self.config.freshness,
                }
            },
            "required": ["query"]
        }

    @property
    def provider(self) -> str:
        return self.config.provider.strip().lower() or DEFAULT_WEB_SEARCH_PROVIDER

    @property
    def jina_api_key(self) -> str:
        return self.config.jina_api_key or os.environ.get("JINA_API_KEY", "")

    @property
    def max_results(self) -> int:
        return self.config.max_results

    @property
    def duckduckgo_max_pages(self) -> int:
        return self.config.duckduckgo_max_pages

    @property
    def searxng_max_pages(self) -> int:
        return self.config.searxng_max_pages

    @property
    def searxng_engines(self) -> list[str]:
        return _clean_text_values(self.config.searxng_engines)

    @property
    def searxng_categories(self) -> list[str]:
        return _clean_text_values(self.config.searxng_categories)

    async def _execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        n = min(max(count or self.max_results, 1), self.max_results)
        freshness = _effective_freshness(kwargs.get("freshness"), self.config.freshness, query=query)

        provider = self.provider

        searcher = self._provider_searcher(provider)
        if searcher is None:
            return _format_error(query, provider, f"unknown search provider '{provider}'")
        return await searcher(query, n, freshness)

    def _provider_searcher(self, provider: str):
        searchers = {
            "duckduckgo": self._search_duckduckgo,
            "searxng": self._search_searxng,
            "jina": self._search_jina,
        }
        return searchers.get(provider)

    async def _search_duckduckgo(self, query: str, n: int, freshness: str) -> str:
        """Search DuckDuckGo through the ddgs package."""
        try:
            from ddgs import DDGS  # type: ignore
        except ImportError:
            return _format_error(
                query,
                "duckduckgo",
                "ddgs package is not installed. Install OpenSprite dependencies and retry.",
                backend="ddgs",
                freshness=freshness,
            )

        safe_limit = max(1, int(n))
        timelimit = DUCKDUCKGO_FRESHNESS.get(_normalize_freshness(freshness, default="none"))

        def _run_ddgs_search() -> list[dict[str, str]]:
            results: list[dict[str, str]] = []
            search_kwargs: dict[str, Any] = {"max_results": safe_limit}
            if timelimit:
                search_kwargs["timelimit"] = timelimit
            with DDGS() as client:
                for i, hit in enumerate(client.text(query, **search_kwargs)):
                    if i >= safe_limit:
                        break
                    url = str(hit.get("href") or hit.get("url") or "")
                    title = str(hit.get("title") or "")
                    if not title or not url:
                        continue
                    results.append(
                        {
                            "title": title,
                            "url": url,
                            "content": str(hit.get("body") or hit.get("content") or ""),
                        }
                    )
            return results

        try:
            items = await asyncio.to_thread(_run_ddgs_search)
        except TypeError as exc:
            if timelimit and "timelimit" in str(exc):
                logger.warning("DDGS does not accept timelimit, retrying without freshness filter")
                timelimit = None
                try:
                    items = await asyncio.to_thread(_run_ddgs_search)
                except Exception as retry_exc:
                    logger.warning("DDGS search failed: %s", retry_exc)
                    return _format_error(
                        query,
                        "duckduckgo",
                        f"DDGS search failed: {retry_exc}",
                        backend="ddgs",
                        freshness=freshness,
                    )
            else:
                logger.warning("DDGS search failed: %s", exc)
                return _format_error(
                    query,
                    "duckduckgo",
                    f"DDGS search failed: {exc}",
                    backend="ddgs",
                    freshness=freshness,
                )
        except Exception as exc:
            logger.warning("DDGS search failed: %s", exc)
            return _format_error(
                query,
                "duckduckgo",
                f"DDGS search failed: {exc}",
                backend="ddgs",
                freshness=freshness,
            )

        if not items:
            logger.warning("DDGS returned no results for query: %s", query)
            return _format_error(
                query,
                "duckduckgo",
                f"DDGS returned no results for '{query}'.",
                backend="ddgs",
                freshness=freshness,
            )
        return _format_results(query, items, n, provider="duckduckgo", backend="ddgs", freshness=freshness)

    async def _search_searxng(self, query: str, n: int, freshness: str) -> str:
        base_url = self.config.searxng_url
        try:
            seen_results = set()
            items: list[dict[str, str]] = []
            scope_params = _searxng_scope_params(self.searxng_engines, self.searxng_categories)
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                for page in range(1, self.searxng_max_pages + 1):
                    r = await client.get(
                        join_url_path(base_url, "/search"),
                        params={
                            "q": query,
                            "format": "json",
                            "pageno": page,
                            **scope_params,
                            **_freshness_params("searxng", freshness),
                        },
                        timeout=10.0
                    )
                    r.raise_for_status()
                    page_results = r.json().get("results", [])
                    if not page_results:
                        break
                    for item in page_results:
                        normalized = {
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "content": item.get("content", ""),
                        }
                        dedupe_key = normalized.get("url") or normalized.get("title")
                        if dedupe_key in seen_results:
                            continue
                        seen_results.add(dedupe_key)
                        items.append(normalized)
                        if len(items) >= n:
                            break
                    if len(items) >= n:
                        break
            return _format_results(query, items, n, provider="searxng", freshness=freshness)
        except Exception as e:
            return _format_error(query, "searxng", str(e), freshness=freshness)

    async def _search_jina(self, query: str, n: int, freshness: str) -> str:
        try:
            headers = {"User-Agent": USER_AGENT}
            if self.jina_api_key:
                headers["Authorization"] = f"Bearer {self.jina_api_key}"
            params = _freshness_params("jina", freshness)
            query_string = f"q={quote_plus(query)}&format=json"
            if params:
                query_string += "&" + "&".join(f"{key}={quote_plus(value)}" for key, value in params.items())
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    f"https://s.jina.ai/http://duckduckgo.com/?{query_string}",
                    headers=headers,
                    timeout=10.0
                )
                r.raise_for_status()
            return _format_results(
                query,
                [{
                    "title": f"Jina summary for {query}",
                    "url": "",
                    "content": r.text,
                }],
                n,
                provider="jina",
                freshness=freshness,
            )
        except Exception as e:
            return _format_error(query, "jina", str(e), freshness=freshness)
