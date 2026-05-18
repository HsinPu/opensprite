"""Web search tool - multi-provider support."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from loguru import logger

from ..config.schema import WebSearchToolConfig
from ..utils.url import join_url_path
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
FRESHNESS_VALUES = ("none", "day", "week", "month", "year")
DUCKDUCKGO_FRESHNESS = {"day": "d", "week": "w", "month": "m", "year": "y"}
BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


def _detect_duckduckgo_block(text: str) -> str | None:
    """Detect common DuckDuckGo bot/rate-limit challenge pages."""
    normalized = _normalize(_strip_tags(text)).lower()
    block_markers = (
        "captcha",
        "prove you are not a robot",
        "prove you are human",
        "unusual traffic",
        "rate limit",
    )
    if any(marker in normalized for marker in block_markers):
        return "bot or rate-limit challenge"
    return None


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


def _extract_duckduckgo_url(href: str) -> str:
    """Extract the real result URL from a DuckDuckGo redirect link."""
    if not href:
        return ""

    normalized = f"https:{href}" if href.startswith("//") else href
    parsed = urlparse(normalized)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target else ""
    return normalized


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


def _freshness_params(provider: str, freshness: str) -> dict[str, str]:
    """Return provider-specific recency parameters for supported engines."""
    normalized = _normalize_freshness(freshness, default="none")
    if normalized == "none":
        return {}
    if provider == "duckduckgo":
        return {"df": DUCKDUCKGO_FRESHNESS[normalized]}
    if provider == "brave":
        return {"freshness": BRAVE_FRESHNESS[normalized]}
    if provider in {"tavily", "searxng"}:
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


def _extract_duckduckgo_results(soup: Any) -> list[dict[str, str]]:
    """Parse result rows from DuckDuckGo Lite HTML."""
    results: list[dict[str, str]] = []
    for a in soup.select("a.result-link"):
        row = a.find_parent("tr")
        snippet = ""
        display_url = ""

        if row is not None:
            sibling_rows = row.find_next_siblings("tr")
            if sibling_rows:
                snippet_cell = sibling_rows[0].select_one("td.result-snippet")
                if snippet_cell is not None:
                    snippet = snippet_cell.get_text(" ", strip=True)
            if len(sibling_rows) > 1:
                url_cell = sibling_rows[1].select_one("span.link-text")
                if url_cell is not None:
                    display_url = url_cell.get_text(" ", strip=True)

        results.append({
            "title": a.get_text(strip=True),
            "url": _extract_duckduckgo_url(a.get("href", "")) or display_url,
            "content": snippet,
        })
    return results


def _duckduckgo_next_request(soup: Any, current_url: str) -> tuple[str, str, dict[str, str]] | None:
    """Extract the next-page request from DuckDuckGo Lite HTML."""
    next_label = re.compile(r"next|下一", re.IGNORECASE)

    for form in soup.find_all("form"):
        submit = None
        for control in form.find_all(["input", "button"]):
            label = control.get("value") or control.get_text(" ", strip=True)
            if label and next_label.search(label):
                submit = control
                break

        if submit is None:
            continue

        payload: dict[str, str] = {}
        for field in form.find_all("input"):
            name = field.get("name")
            if not name:
                continue
            field_type = (field.get("type") or "").lower()
            if field_type in {"submit", "button", "image", "reset"}:
                continue
            payload[name] = field.get("value", "")

        submit_name = submit.get("name")
        if submit_name:
            payload[submit_name] = submit.get("value", "")

        method = (form.get("method") or "get").lower()
        action = form.get("action") or current_url
        return method, urljoin(current_url, action), payload

    for link in soup.find_all("a"):
        if next_label.search(link.get_text(" ", strip=True)) and link.get("href"):
            return "get", urljoin(current_url, link["href"]), {}

    return None


def _format_results(query: str, items: list[dict[str, Any]], n: int, *, provider: str) -> str:
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
    return json.dumps(
        {
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
        },
        ensure_ascii=False,
    )


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
    description = "Search the web for new external sources. Defaults to a recent-results filter to avoid stale sources; set freshness='none' for timeless docs. If this chat may already contain earlier research, prefer search_knowledge first. Returns structured JSON with titles, URLs, and snippets. Supports Brave, DuckDuckGo, Tavily, SearXNG, Jina."

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
                    "minimum": 1,
                    "maximum": self.max_results,
                },
                "freshness": {
                    "type": "string",
                    "enum": list(FRESHNESS_VALUES),
                    "description": "Recency filter. Defaults to config (year) to reduce stale results; use none for timeless/reference docs.",
                    "default": self.config.freshness,
                }
            },
            "required": ["query"]
        }

    @property
    def provider(self) -> str:
        return self.config.provider.strip().lower() or "searxng"

    @property
    def brave_api_key(self) -> str:
        return self.config.brave_api_key or os.environ.get("BRAVE_API_KEY", "")

    @property
    def tavily_api_key(self) -> str:
        return self.config.tavily_api_key or os.environ.get("TAVILY_API_KEY", "")

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
        freshness = _normalize_freshness(kwargs.get("freshness"), self.config.freshness)

        provider = self.provider

        if provider == "duckduckgo":
            return await self._search_duckduckgo(query, n, freshness)
        elif provider == "tavily":
            return await self._search_tavily(query, n, freshness)
        elif provider == "searxng":
            return await self._search_searxng(query, n, freshness)
        elif provider == "jina":
            return await self._search_jina(query, n, freshness)
        elif provider == "brave":
            return await self._search_brave(query, n, freshness)
        else:
            return _format_error(query, provider, f"unknown search provider '{provider}'")

    async def _search_brave(self, query: str, n: int, freshness: str) -> str:
        api_key = self.brave_api_key
        if not api_key:
            logger.warning("Brave API key not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n, freshness)
        try:
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": n, **_freshness_params("brave", freshness)},
                    headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                    timeout=10.0
                )
                r.raise_for_status()
            items = [
                {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("description", "")}
                for x in r.json().get("web", {}).get("results", [])
            ]
            return _format_results(query, items, n, provider="brave")
        except Exception as e:
            return _format_error(query, "brave", str(e))

    async def _search_duckduckgo(self, query: str, n: int, freshness: str) -> str:
        try:
            from bs4 import BeautifulSoup

            request_method = "get"
            request_url = "https://lite.duckduckgo.com/lite/"
            freshness_payload = _freshness_params("duckduckgo", freshness)
            request_payload = {"q": query, **freshness_payload}
            visited_requests = set()
            seen_results = set()
            results = []
            pages_fetched = 0

            async with httpx.AsyncClient(
                proxy=self.proxy,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                while len(results) < n and pages_fetched < self.duckduckgo_max_pages:
                    request_key = (
                        request_method,
                        request_url,
                        tuple(sorted(request_payload.items())),
                    )
                    if request_key in visited_requests:
                        break
                    visited_requests.add(request_key)

                    if request_method == "post":
                        r = await client.post(request_url, data=request_payload, timeout=10.0)
                    else:
                        r = await client.get(request_url, params=request_payload, timeout=10.0)
                    r.raise_for_status()
                    pages_fetched += 1

                    block_reason = _detect_duckduckgo_block(r.text)
                    if block_reason:
                        return _format_error(
                            query,
                            "duckduckgo",
                            (
                                f"DuckDuckGo blocked the search for '{query}' with a {block_reason}. "
                                "Try again later or configure another web_search provider."
                            ),
                            block_reason=block_reason,
                        )

                    current_url = str(getattr(r, "url", request_url))
                    soup = BeautifulSoup(r.text, "html.parser")
                    for item in _extract_duckduckgo_results(soup):
                        dedupe_key = item.get("url") or item.get("title")
                        if dedupe_key in seen_results:
                            continue
                        seen_results.add(dedupe_key)
                        results.append(item)
                        if len(results) >= n:
                            break

                    if len(results) >= n:
                        break

                    next_request = _duckduckgo_next_request(soup, current_url)
                    if next_request is None:
                        break
                    request_method, request_url, request_payload = next_request
                    request_payload = {**request_payload, **freshness_payload}

            if not results:
                return _format_error(query, "duckduckgo", f"DuckDuckGo returned no results for '{query}'.")

            return _format_results(query, results, n, provider="duckduckgo")
        except Exception as e:
            return _format_error(query, "duckduckgo", str(e))

    async def _search_tavily(self, query: str, n: int, freshness: str) -> str:
        api_key = self.tavily_api_key
        if not api_key:
            logger.warning("Tavily API key not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n, freshness)
        try:
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"query": query, "max_results": n, **_freshness_params("tavily", freshness)},
                    timeout=15.0
                )
                r.raise_for_status()
            items = [{"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")} 
                     for x in r.json().get("results", [])]
            return _format_results(query, items, n, provider="tavily")
        except Exception as e:
            return _format_error(query, "tavily", str(e))

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
            return _format_results(query, items, n, provider="searxng")
        except Exception as e:
            return _format_error(query, "searxng", str(e))

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
            )
        except Exception as e:
            return _format_error(query, "jina", str(e))
