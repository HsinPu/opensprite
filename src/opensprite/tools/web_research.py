"""High-level web research orchestration tool."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..config.defaults import DEFAULT_WEB_SEARCH_PROVIDER
from ..config.schema import WebFetchToolConfig, WebSearchToolConfig
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN
from .web_blocking import looks_blocked_or_challenge
from .web_fetch import WEB_FETCH_MIN_CONTENT_CHARS, WebFetchTool
from .web_search import FRESHNESS_VALUES, WebSearchTool, _effective_freshness

_WEB_RESEARCH_FETCH_CONCURRENCY = 4
_WEB_RESEARCH_SEARCH_CONCURRENCY = 3
_RECENT_FRESHNESS_VALUES = {"day", "week", "month"}
_YEAR_RE = re.compile(r"(?<!\d)20\d{2}(?!\d)")
_LOW_SIGNAL_DOMAIN_SUFFIXES = (
    "youtube.com",
    "youtu.be",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "medium.com",
    "substack.com",
    "pinterest.com",
)
_OFFICIAL_DOMAIN_STOPWORDS = {
    "api",
    "docs",
    "documentation",
    "official",
    "rate",
    "rates",
    "limit",
    "limits",
    "pricing",
    "tier",
    "tiers",
    "free",
    "paid",
}
_MARKET_QUOTE_QUERY_RE = re.compile(
    r"\b(?:stock price|share price|quote|quotes|market price|ticker)\b"
    r"|(?:\u80a1\u50f9|\u5831\u50f9|\u5373\u6642\u884c\u60c5|\u76ee\u524d\u80a1\u50f9|\u4eca\u65e5\u80a1\u50f9)",
    re.IGNORECASE,
)
_MARKET_QUOTE_DOMAINS = (
    "stock.yahoo.com",
    "finance.yahoo.com",
    "google.com",
    "cnyes.com",
    "wantgoo.com",
    "goodinfo.tw",
    "sinotrade.com.tw",
    "macromicro.me",
    "tradingview.com",
    "cnbc.com",
    "stockscan.io",
    "indmoney.com",
)
_MARKET_QUOTE_DISCUSSION_DOMAINS = (
    "ptt.cc",
    "ptt.best",
    "reddit.com",
    "threads.com",
)
_MARKET_QUOTE_FORECAST_MARKERS = (
    "forecast",
    "prediction",
    "price target",
    "analyst target",
    "預測",
    "目標價",
)
_MARKET_QUOTE_PAGE_MARKERS = (
    "/quote/",
    "/quotes/",
    "/stocks/",
    "stock price",
    "share price",
    "live share price",
    "stock quote",
    "stock chart",
    "股市",
    "報價",
)
_MARKET_QUOTE_GENERIC_TEXT_MARKERS = ("quote", "stock price", "\u80a1\u50f9", "\u5831\u50f9", "\u884c\u60c5")
_MARKET_QUOTE_QUERY_STOPWORDS = {
    "adr",
    "finance",
    "latest",
    "market",
    "price",
    "quote",
    "quotes",
    "share",
    "stock",
    "today",
    "yahoo",
    "最新",
    "目前",
    "今日",
    "即時",
    "股價",
    "股票",
    "報價",
    "行情報價",
}


class WebResearchTool(Tool):
    """Search, dedupe, fetch, and return source material in one structured payload."""

    name = "web_research"
    description = (
        "Run a compact research pass for external information: search the web, dedupe/rank candidate URLs, "
        "fetch the most promising pages, skip too-short fetches when possible, and return traceable sources. "
        "Use this instead of separate web_search + web_fetch when the user asks for current web research."
    )

    def __init__(
        self,
        *,
        search_config: WebSearchToolConfig | None = None,
        fetch_config: WebFetchToolConfig | None = None,
        search_tool: WebSearchTool | None = None,
        fetch_tool: WebFetchTool | None = None,
    ):
        self.search_config = search_config or WebSearchToolConfig()
        self.fetch_config = fetch_config or WebFetchToolConfig()
        self._custom_search_tool = search_tool is not None
        self.search_tool = search_tool or WebSearchTool(config=self.search_config)
        self.fetch_tool = fetch_tool or WebFetchTool(
            max_chars=self.fetch_config.max_chars,
            max_response_size=self.fetch_config.max_response_size,
            timeout=self.fetch_config.timeout,
            prefer_trafilatura=self.fetch_config.prefer_trafilatura,
            firecrawl_api_key=self.fetch_config.firecrawl_api_key,
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research query", "pattern": NON_EMPTY_STRING_PATTERN},
                "queries": {
                    "type": "array",
                    "description": "Optional additional search queries to run and merge for broader research coverage",
                    "items": {"type": "string", "pattern": NON_EMPTY_STRING_PATTERN},
                    "maxItems": 5,
                },
                "count": {
                    "type": "integer",
                    "description": "Search candidates to inspect before dedupe; defaults to the configured max_results",
                    "default": self.search_config.max_results,
                    "minimum": 1,
                    "maximum": self.search_config.max_results,
                },
                "fetch_count": {
                    "type": "integer",
                    "description": "Number of substantive pages to fetch",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 5,
                },
                "freshness": {
                    "type": "string",
                    "enum": list(FRESHNESS_VALUES),
                    "description": "Recency filter passed through to web_search; auto uses the configured default recent window",
                    "default": self.search_config.freshness,
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters per fetched page",
                    "default": self.fetch_config.max_chars,
                    "minimum": 1,
                },
            },
            "required": ["query"],
        }

    async def execute_validated(self, params: Any) -> str:
        """Normalize common LLM query-object shapes before schema validation."""
        return await super().execute_validated(_normalize_research_params(params))

    async def _execute(
        self,
        query: str,
        count: int | None = None,
        fetch_count: int | None = None,
        freshness: str | None = None,
        max_chars: int | None = None,
        queries: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        search_count = min(max(int(count or self.search_config.max_results), 1), self.search_config.max_results)
        target_fetches = min(max(int(fetch_count or 2), 1), 5)
        research_queries = _research_queries(query, queries)
        effective_freshness = _effective_freshness(
            freshness,
            self.search_config.freshness,
            query=" ".join(research_queries),
        )
        research_queries = _research_queries(query, queries, freshness=effective_freshness)
        effective_max_chars = max_chars if max_chars is not None else self.fetch_config.max_chars
        fetched_sources: list[dict[str, Any]] = []
        failed_sources: list[dict[str, Any]] = []
        source_records: list[dict[str, Any]] = []
        fetched_urls: set[str] = set()

        search_items, search_provider, search_backend, search_attempts, query_attempts = await self._search_queries_with_fallback(
            queries=research_queries,
            count=search_count,
            freshness=effective_freshness,
        )
        official_domains = _official_domain_hints(" ".join(research_queries), search_items) | _site_domain_hints(
            research_queries
        )
        official_site_queries = _official_site_queries(query, official_domains, existing_queries=research_queries)
        if official_site_queries:
            site_items, site_provider, site_backend, site_attempts, site_query_attempts = await self._search_queries_with_fallback(
                queries=official_site_queries,
                count=search_count,
                freshness=effective_freshness,
            )
            search_attempts.extend(site_attempts)
            query_attempts.extend(site_query_attempts)
            research_queries = _dedupe_query_strings([*research_queries, *official_site_queries])
            if site_items:
                search_items = _dedupe_search_items(
                    [*site_items, *search_items],
                    limit=max(search_count * max(len(research_queries), 1), search_count),
                )
                official_domains.update(_official_domain_hints(" ".join(research_queries), search_items))
                official_domains.update(_site_domain_hints(research_queries))
                search_provider = site_provider or search_provider
                search_backend = site_backend or search_backend
        if not search_items:
            return _research_payload(
                query=query,
                freshness=effective_freshness,
                search_provider=search_provider,
                search_backend=search_backend,
                search_items=[],
                fetched_sources=fetched_sources,
                failed_sources=[{"reason": "web_search returned no structured result with fetchable URLs"}],
                sources=source_records,
                target_fetch_count=target_fetches,
                search_attempts=search_attempts,
                query_attempts=query_attempts,
                queries=research_queries,
            )

        fetched_by_candidate_url: dict[str, dict[str, Any]] = {}
        fetch_candidates = _prioritize_research_candidates(
            search_items,
            existing_sources=fetched_sources,
            freshness=effective_freshness,
            official_domains=official_domains,
        )
        fetched_by_candidate_url = await self._fetch_research_candidates(
            candidates=fetch_candidates,
            fetched_sources=fetched_sources,
            failed_sources=failed_sources,
            fetched_urls=fetched_urls,
            target_fetches=target_fetches,
            max_chars=effective_max_chars,
            query=query,
            search_provider=search_provider,
            search_backend=search_backend,
        )

        for item in search_items:
            item_search_provider = str(item.get("search_provider") or search_provider)
            item_search_backend = str(item.get("search_backend") or search_backend)
            source_records.append(
                {
                    **item,
                    "tool_name": "web_search",
                    "fetched": False,
                    "search_provider": item_search_provider,
                    "search_backend": item_search_backend,
                }
            )
            fetched = fetched_by_candidate_url.get(_candidate_url_key(item))
            if fetched is not None:
                source_records.append({**fetched, "tool_name": "web_fetch", "fetched": True})

        return _research_payload(
            query=query,
            freshness=effective_freshness,
            search_provider=search_provider,
            search_backend=search_backend,
            search_items=search_items,
            fetched_sources=fetched_sources,
            failed_sources=failed_sources,
            sources=source_records,
            target_fetch_count=target_fetches,
            search_attempts=search_attempts,
            query_attempts=query_attempts,
            queries=research_queries,
        )

    async def _fetch_research_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        fetched_sources: list[dict[str, Any]],
        failed_sources: list[dict[str, Any]],
        fetched_urls: set[str],
        target_fetches: int,
        max_chars: int,
        query: str,
        search_provider: str,
        search_backend: str,
    ) -> dict[str, dict[str, Any]]:
        fetched_by_candidate_url: dict[str, dict[str, Any]] = {}
        cursor = 0
        while len(fetched_sources) < target_fetches and cursor < len(candidates):
            remaining_needed = max(target_fetches - len(fetched_sources), 1)
            batch_size = min(
                len(candidates) - cursor,
                max(remaining_needed, min(_WEB_RESEARCH_FETCH_CONCURRENCY, max(target_fetches, 1))),
            )
            batch = candidates[cursor : cursor + batch_size]
            cursor += batch_size

            tasks: list[Any] = []
            for item in batch:
                url = _clean_text(item.get("url"))
                if not url:
                    failed_sources.append({**item, "reason": "missing url"})
                    continue
                if not _is_fetchable_url(url):
                    failed_sources.append({**item, "reason": "unsupported url"})
                    continue
                canonical_url = _candidate_url_key(item)
                if canonical_url in fetched_urls:
                    continue
                tasks.append(
                    self._fetch_single_candidate(
                        item,
                        max_chars=max_chars,
                        query=query,
                        search_provider=search_provider,
                        search_backend=search_backend,
                    )
                )

            if not tasks:
                continue

            for canonical_url, fetched, failed in await asyncio.gather(*tasks):
                if failed is not None:
                    failed_sources.append(failed)
                    continue
                if fetched is None:
                    continue
                final_url_key = str(fetched.get("canonical_url") or fetched.get("url") or "")
                if final_url_key and final_url_key in fetched_urls and final_url_key != canonical_url:
                    failed_sources.append({**fetched, "reason": "duplicate final url"})
                    continue
                if fetched.get("blocked_or_challenge"):
                    failed_sources.append({**fetched, "reason": "fetched content looked blocked or challenged"})
                    continue
                if fetched.get("is_too_short") or not fetched.get("has_main_content"):
                    failed_sources.append({**fetched, "reason": "fetched content was too short"})
                    continue

                fetched_sources.append(fetched)
                fetched_by_candidate_url[canonical_url] = fetched
                fetched_urls.add(canonical_url)
                if final_url_key:
                    fetched_urls.add(final_url_key)
                if len(fetched_sources) >= target_fetches:
                    break

        return fetched_by_candidate_url

    async def _fetch_single_candidate(
        self,
        item: dict[str, Any],
        *,
        max_chars: int,
        query: str,
        search_provider: str,
        search_backend: str,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        canonical_url = _candidate_url_key(item)
        url = _clean_text(item.get("url"))
        item_search_provider = str(item.get("search_provider") or search_provider)
        item_search_backend = str(item.get("search_backend") or search_backend)
        try:
            fetch_result = await self.fetch_tool._execute(url=url, max_chars=max_chars)
        except Exception as exc:
            return canonical_url, None, {**item, "reason": f"web_fetch failed: {exc}"[:500]}
        fetch_payload = _parse_json_object(fetch_result)
        if fetch_payload is None:
            return canonical_url, None, {
                **item,
                "reason": str(fetch_result or "web_fetch returned no structured result")[:500],
            }

        return canonical_url, _merge_fetch_source(
            item,
            fetch_payload,
            query=str(item.get("source_query") or query),
            search_provider=item_search_provider,
            search_backend=item_search_backend,
        ), None

    async def _search_queries_with_fallback(
        self,
        *,
        queries: list[str],
        count: int,
        freshness: str,
    ) -> tuple[list[dict[str, Any]], str, str, list[dict[str, Any]], list[dict[str, Any]]]:
        all_items: list[dict[str, Any]] = []
        all_attempts: list[dict[str, Any]] = []
        query_attempts: list[dict[str, Any]] = []
        selected_provider = ""
        selected_backend = ""
        fallback_provider = ""
        fallback_backend = ""
        semaphore = asyncio.Semaphore(_WEB_RESEARCH_SEARCH_CONCURRENCY)

        async def run_query(current_query: str):
            async with semaphore:
                return await self._search_with_fallback(
                    query=current_query,
                    count=count,
                    freshness=freshness,
                )

        results = await asyncio.gather(
            *(run_query(current_query) for current_query in queries)
        )
        for current_query, result in zip(queries, results):
            payload, items, provider, backend, attempts = result
            all_attempts.extend(attempts)
            query_attempts.append(_query_attempt_payload(current_query, provider, backend, payload, items, attempts))
            if not fallback_provider and provider:
                fallback_provider = provider
            if not fallback_backend and backend:
                fallback_backend = backend
            if items and not selected_provider and provider:
                selected_provider = provider
            if items and not selected_backend and backend:
                selected_backend = backend
            all_items.extend({**item, "source_query": current_query} for item in items)

        return (
            _dedupe_search_items(all_items, limit=max(count * max(len(queries), 1), count)),
            selected_provider or fallback_provider,
            selected_backend or fallback_backend,
            all_attempts,
            query_attempts,
        )

    async def _search_with_fallback(
        self,
        *,
        query: str,
        count: int,
        freshness: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str, str, list[dict[str, Any]]]:
        attempts: list[dict[str, Any]] = []
        last_provider = getattr(self.search_tool, "provider", self.search_config.provider)
        last_backend = ""
        if self._custom_search_tool:
            providers = [str(last_provider or self.search_config.provider or DEFAULT_WEB_SEARCH_PROVIDER)]
        else:
            providers = _search_provider_order(
                self.search_config,
                configured_provider=str(last_provider or ""),
            )
        for provider in providers:
            tool = self._search_tool_for_provider(provider)
            result = await tool._execute(query=query, count=count, freshness=freshness)
            payload = _parse_json_object(result)
            provider_name = str((payload or {}).get("provider") or getattr(tool, "provider", provider) or provider)
            backend_name = str((payload or {}).get("backend") or "")
            last_provider = provider_name
            last_backend = backend_name
            items = _dedupe_search_items(_coerce_search_items(payload or {}), limit=count) if payload else []
            fetchable_count = sum(1 for item in items if _is_fetchable_url(item.get("url")))
            attempts.append(
                _search_attempt_payload(
                    configured_provider=provider,
                    provider=provider_name,
                    backend=backend_name,
                    payload=payload,
                    items=items,
                    raw_result=result,
                    fetchable_count=fetchable_count,
                )
            )
            if payload is not None and fetchable_count > 0:
                return (
                    payload,
                    [
                        {
                            **item,
                            "search_provider": provider_name,
                            "search_backend": backend_name,
                            "search_freshness": str((payload or {}).get("freshness") or freshness),
                            "source_query": query,
                        }
                        for item in items
                    ],
                    provider_name,
                    backend_name,
                    attempts,
                )
        return None, [], str(last_provider or ""), str(last_backend or ""), attempts

    def _search_tool_for_provider(self, provider: str) -> WebSearchTool:
        if self._custom_search_tool:
            return self.search_tool
        if provider == self.search_tool.provider:
            return self.search_tool
        return WebSearchTool(config=self.search_config.model_copy(update={"provider": provider}))


def _research_payload(
    *,
    query: str,
    freshness: str,
    search_provider: str,
    search_backend: str,
    search_items: list[dict[str, Any]],
    fetched_sources: list[dict[str, Any]],
    failed_sources: list[dict[str, Any]],
    sources: list[dict[str, Any]] | None = None,
    queries: list[str] | None = None,
    target_fetch_count: int | None = None,
    search_attempts: list[dict[str, Any]] | None = None,
    query_attempts: list[dict[str, Any]] | None = None,
) -> str:
    research_queries = queries or [query]
    coverage = _research_coverage(
        queries=research_queries,
        target_fetch_count=target_fetch_count or len(fetched_sources),
        search_items=search_items,
        fetched_sources=fetched_sources,
        failed_sources=failed_sources,
    )
    return json.dumps(
        {
            "type": "web_research",
            "query": query,
            "queries": research_queries,
            "url": "",
            "final_url": "",
            "title": "",
            "content": "\n\n".join(str(item.get("content") or "") for item in fetched_sources if item.get("content")),
            "summary": f"Web research for: {query}",
            "provider": search_provider,
            "backend": search_backend,
            "extractor": "web_research",
            "status": None,
            "truncated": any(bool(item.get("truncated")) for item in fetched_sources),
            "content_type": "application/json",
            "freshness": freshness,
            "items": search_items,
            "fetched_sources": fetched_sources,
            "failed_sources": failed_sources,
            "sources": sources if sources is not None else fetched_sources,
            "source_count": len(sources if sources is not None else fetched_sources),
            "fetched_count": len(fetched_sources),
            "search_attempts": search_attempts or [],
            "query_attempts": query_attempts or [],
            "coverage": coverage,
        },
        ensure_ascii=False,
    )


def _research_queries(query: str, queries: list[str] | None, *, freshness: str | None = None) -> list[str]:
    values = [_clean_text(query)]
    if isinstance(queries, list):
        values.extend(_clean_text(value) for value in queries[:5])
    elif queries is not None:
        values.append(_clean_text(queries))
    values.extend(_market_quote_queries(query))

    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    out = out or [_clean_text(query)]
    return _prefer_current_year_queries(out, freshness=freshness)


def _prefer_current_year_queries(queries: list[str], *, freshness: str | None) -> list[str]:
    if freshness not in _RECENT_FRESHNESS_VALUES:
        return queries
    combined = " ".join(_clean_text(value).lower() for value in queries)
    current_year = datetime.now().year
    stale_years = sorted(
        {
            int(match)
            for match in _YEAR_RE.findall(combined)
            if int(match) < current_year
        }
    )
    if not stale_years:
        return queries

    corrected: list[str] = []
    for query in queries:
        updated = query
        for year in stale_years:
            updated = re.sub(rf"(?<!\d){year}(?!\d)", str(current_year), updated)
        if updated != query:
            corrected.append(updated)

    seen: set[str] = set()
    out: list[str] = []
    for value in [*corrected, *queries]:
        key = value.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out

def _query_attempt_payload(
    query: str,
    provider: str,
    backend: str,
    payload: dict[str, Any] | None,
    items: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "query": query,
        "provider": provider,
        "backend": backend,
        "ok": payload is not None and bool(items),
        "result_count": len(items),
        "search_attempts": attempts,
    }


def _search_attempt_payload(
    *,
    configured_provider: str,
    provider: str,
    backend: str,
    payload: dict[str, Any] | None,
    items: list[dict[str, Any]],
    raw_result: str,
    fetchable_count: int,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "configured_provider": configured_provider,
        "backend": backend,
        "ok": payload is not None and fetchable_count > 0,
        "result_count": len(items),
        "fetchable_count": fetchable_count,
        "error": str((payload or {}).get("error") or ("" if payload is not None else raw_result or ""))[:500],
    }


def _research_coverage(
    *,
    queries: list[str],
    target_fetch_count: int,
    search_items: list[dict[str, Any]],
    fetched_sources: list[dict[str, Any]],
    failed_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    fetched_queries = _ordered_clean_values(_candidate_query_label(source) for source in fetched_sources)
    fetched_domains = _ordered_clean_values(_candidate_domain(source) for source in fetched_sources)
    queries_with_search_results = _ordered_clean_values(_candidate_query_label(item) for item in search_items)
    fetched_query_keys = {query.lower() for query in fetched_queries}
    queries_without_successful_fetch = [
        query
        for query in queries_with_search_results
        if query.lower() not in fetched_query_keys
    ]
    too_short_count = sum(
        1
        for source in failed_sources
        if bool(source.get("is_too_short")) or str(source.get("reason") or "") == "fetched content was too short"
    )
    blocked_count = sum(
        1
        for source in failed_sources
        if bool(source.get("blocked_or_challenge"))
        or str(source.get("reason") or "") == "fetched content looked blocked or challenged"
    )
    missing_url_count = sum(1 for source in failed_sources if str(source.get("reason") or "") == "missing url")
    return {
        "target_fetch_count": max(int(target_fetch_count or 0), 0),
        "target_met": len(fetched_sources) >= max(int(target_fetch_count or 0), 0),
        "search_result_count": len(search_items),
        "fetched_count": len(fetched_sources),
        "failed_count": len(failed_sources),
        "too_short_count": too_short_count,
        "blocked_count": blocked_count,
        "missing_url_count": missing_url_count,
        "fetched_domains": fetched_domains,
        "fetched_domain_count": len(fetched_domains),
        "fetched_queries": fetched_queries,
        "fetched_query_count": len(fetched_queries),
        "queries_with_search_results": queries_with_search_results,
        "queries_without_successful_fetch": queries_without_successful_fetch,
    }


def _ordered_clean_values(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _clean_text(value)
        key = text.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _search_provider_order(config: WebSearchToolConfig, *, configured_provider: str) -> list[str]:
    configured = (configured_provider or config.provider or DEFAULT_WEB_SEARCH_PROVIDER).strip().lower() or DEFAULT_WEB_SEARCH_PROVIDER
    candidates = [configured]
    probe_tool = WebSearchTool(config=config)
    if str(config.searxng_url or "").strip():
        candidates.append("searxng")
    candidates.append("duckduckgo")
    if configured == "jina" or probe_tool.jina_api_key:
        candidates.append("jina")
    return _dedupe_strings(candidates)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(str(value or ""))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_search_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("items", payload.get("results", []))
    if not isinstance(raw_items, list):
        return []
    out: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items, 1):
        if not isinstance(raw_item, dict):
            continue
        url = _clean_text(raw_item.get("url"))
        title = _clean_text(raw_item.get("title"))
        snippet = _clean_text(raw_item.get("content") or raw_item.get("snippet") or raw_item.get("summary"))
        canonical_url = _canonicalize_url(url)
        out.append(
            {
                "rank": index,
                "title": title,
                "url": url,
                "canonical_url": canonical_url,
                "domain": _domain_from_url(url),
                "content": snippet,
            }
        )
    return out


def _dedupe_search_items(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("canonical_url") or item.get("url") or item.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _prioritize_research_candidates(
    items: list[dict[str, Any]],
    *,
    existing_sources: list[dict[str, Any]],
    freshness: str,
    official_domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    if len(items) <= 1:
        return items
    official_domains = set(official_domains or set())
    ordered_items = sorted(
        enumerate(items),
        key=lambda pair: (_candidate_priority(pair[1], freshness, official_domains=official_domains), pair[0]),
    )
    ordered = [item for _, item in ordered_items]
    if official_domains:
        official = [item for item in ordered if _candidate_official_penalty(item, official_domains) == 0]
        non_official = [item for item in ordered if _candidate_official_penalty(item, official_domains) != 0]
        return [*official, *non_official]
    item_queries = {_candidate_query(item) for item in items}
    item_queries.discard("")
    if len(item_queries) <= 1:
        return ordered

    selected: list[dict[str, Any]] = []
    remaining = list(ordered)
    covered_domains = {_candidate_domain(source) for source in existing_sources}
    covered_domains.discard("")
    covered_queries = {_candidate_query(source) for source in existing_sources}
    covered_queries.discard("")

    def take_candidates(*, require_new_query: bool, require_new_domain: bool) -> None:
        nonlocal remaining
        next_remaining: list[dict[str, Any]] = []
        for item in remaining:
            query = _candidate_query(item)
            domain = _candidate_domain(item)
            query_is_new = bool(query) and query not in covered_queries
            domain_is_new = bool(domain) and domain not in covered_domains
            if (not require_new_query or query_is_new) and (not require_new_domain or domain_is_new):
                selected.append(item)
                if query:
                    covered_queries.add(query)
                if domain:
                    covered_domains.add(domain)
                continue
            next_remaining.append(item)
        remaining = next_remaining

    take_candidates(require_new_query=True, require_new_domain=True)
    take_candidates(require_new_query=False, require_new_domain=True)
    selected.extend(remaining)
    return selected


def _candidate_priority(item: dict[str, Any], freshness: str, *, official_domains: set[str] | None = None) -> tuple[int, int, int, int, int, int, int]:
    fetchable_penalty = 0 if _is_fetchable_url(item.get("url")) else 1
    quote_penalty = _candidate_market_quote_penalty(item)
    official_penalty = _candidate_official_penalty(item, official_domains or set())
    low_signal_penalty = _candidate_low_signal_penalty(item)
    stale_penalty = _candidate_staleness_penalty(item, freshness)
    recent_bonus = _candidate_recent_score(item) if freshness in _RECENT_FRESHNESS_VALUES else 0
    rank = _coerce_int(item.get("rank"), default=9999)
    return (fetchable_penalty, quote_penalty, official_penalty, low_signal_penalty, stale_penalty, -recent_bonus, rank)


def _market_quote_queries(query: str) -> list[str]:
    text = _clean_text(query)
    if not text or not _MARKET_QUOTE_QUERY_RE.search(text):
        return []
    queries = [f"{text} Yahoo Finance", f"{text} Yahoo \u80a1\u5e02"]
    terms = _market_quote_entity_terms(text)
    if {"tsmc", "台積電", "2330"} & terms:
        queries.extend(
            [
                "TSM quote Yahoo Finance",
                "2330.TW quote Yahoo Finance",
                "2330 台積電 Yahoo 股市",
            ]
        )
    return queries


def _candidate_market_quote_penalty(item: dict[str, Any]) -> int:
    query = _candidate_query(item)
    if not _MARKET_QUOTE_QUERY_RE.search(query):
        return 0
    domain = _candidate_domain(item)
    text = " ".join(_clean_text(item.get(key)).lower() for key in ("title", "content", "url", "domain"))
    query_terms = _market_quote_entity_terms(query)
    if query_terms and not any(term in text for term in query_terms):
        return 2
    return {
        "preferred": 0,
        "quote_page": 0,
        "generic_quote": 1,
        "other": 1,
        "forecast": 3,
        "discussion": 4,
    }[_market_quote_candidate_kind(domain=domain, text=text)]


def _market_quote_candidate_kind(*, domain: str, text: str) -> str:
    """Classify quote-query candidates behind one searchable heuristic boundary."""
    if any(domain == blocked or domain.endswith(f".{blocked}") for blocked in _MARKET_QUOTE_DISCUSSION_DOMAINS):
        return "discussion"
    if any(marker in text for marker in _MARKET_QUOTE_FORECAST_MARKERS):
        return "forecast"
    if "blogspot." in domain or domain.endswith(".blogspot.com"):
        return "forecast"
    if any(domain == preferred or domain.endswith(f".{preferred}") for preferred in _MARKET_QUOTE_DOMAINS):
        return "preferred"
    if any(marker in text for marker in _MARKET_QUOTE_PAGE_MARKERS):
        return "quote_page"
    if any(marker in text for marker in _MARKET_QUOTE_GENERIC_TEXT_MARKERS):
        return "generic_quote"
    return "other"


def _market_quote_entity_terms(query: str) -> set[str]:
    text = _clean_text(query).lower()
    terms: set[str] = set()
    for token in re.findall(r"\b[a-z][a-z0-9.:-]{1,}\b", text):
        if token in _MARKET_QUOTE_QUERY_STOPWORDS or token.isdigit() or _YEAR_RE.fullmatch(token):
            continue
        terms.add(token)
    for token in re.findall(r"\b\d{3,6}(?:\.[a-z]{1,4})?\b", text):
        if not _YEAR_RE.fullmatch(token):
            terms.add(token)
    for token in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if token not in _MARKET_QUOTE_QUERY_STOPWORDS:
            terms.add(token)
    if "tsmc" in terms or "台積電" in terms or "2330" in terms:
        terms.update({"tsm", "tsmc", "taiwan semiconductor", "台積電", "2330", "2330.tw"})
    return terms


def _official_domain_hints(query: str, items: list[dict[str, Any]]) -> set[str]:
    brand_tokens = {
        token.lower()
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9]{2,}\b", str(query or ""))
        if token.lower() not in _OFFICIAL_DOMAIN_STOPWORDS
    }
    if not brand_tokens:
        return set()

    hints: set[str] = set()
    for item in items:
        domain = _candidate_domain(item)
        brand_label = _domain_brand_label(domain)
        if any(token == brand_label for token in brand_tokens):
            hints.add(domain)
    return hints


def _site_domain_hints(queries: list[str]) -> set[str]:
    hints: set[str] = set()
    for query in queries:
        for match in re.findall(r"\bsite:([A-Za-z0-9.-]+\.[A-Za-z]{2,})", str(query or ""), flags=re.IGNORECASE):
            domain = _clean_text(match).lower().strip(".")
            if domain:
                hints.add(domain)
    return hints


def _domain_brand_label(domain: str) -> str:
    labels = _clean_text(domain).lower().removeprefix("www.").split(".")
    labels = [label for label in labels if label]
    if len(labels) < 2:
        return ""
    return labels[-2].replace("-", "")


def _official_site_queries(query: str, official_domains: set[str], *, existing_queries: list[str]) -> list[str]:
    if not official_domains:
        return []
    existing = {value.lower() for value in existing_queries}
    out: list[str] = []
    for domain in sorted(official_domains)[:2]:
        site_query = f"site:{domain} {_clean_text(query)}"
        key = site_query.lower()
        if key not in existing:
            out.append(site_query)
    return out


def _dedupe_query_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _clean_text(value)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _normalize_research_params(params: Any) -> Any:
    if not isinstance(params, dict):
        return params
    normalized = dict(params)
    query = _coerce_query_text(normalized.get("query"))
    raw_queries = normalized.get("queries")
    queries = [_coerce_query_text(item) for item in raw_queries] if isinstance(raw_queries, list) else []
    queries = [item for item in queries if item]
    if query:
        normalized["query"] = query
    elif queries:
        normalized["query"] = queries[0]
        queries = queries[1:]
    if isinstance(raw_queries, list):
        normalized["queries"] = queries
    return normalized


def _coerce_query_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        for key in ("query", "q", "text", "title"):
            text = _clean_text(value.get(key))
            if text:
                return text
    return ""


def _candidate_official_penalty(item: dict[str, Any], official_domains: set[str]) -> int:
    if not official_domains:
        return 0
    domain = _candidate_domain(item)
    if any(domain == official or domain.endswith(f".{official}") for official in official_domains):
        return 0
    return 1


def _candidate_recent_score(item: dict[str, Any]) -> int:
    text = " ".join(
        _clean_text(item.get(key)).lower()
        for key in ("title", "content", "snippet", "url")
    )
    if not text:
        return 0
    current_year = str(datetime.now().year)
    score = 0
    if current_year in text:
        score += 4
    if re.search(r"\b20\d{2}[-/.](0?[1-9]|1[0-2])([-/.](0?[1-9]|[12]\d|3[01]))?\b", text):
        score += 2
    return score


def _candidate_low_signal_penalty(item: dict[str, Any]) -> int:
    domain = _candidate_domain(item)
    if not domain:
        return 0
    return 1 if any(domain == suffix or domain.endswith(f".{suffix}") for suffix in _LOW_SIGNAL_DOMAIN_SUFFIXES) else 0


def _candidate_staleness_penalty(item: dict[str, Any], freshness: str) -> int:
    if freshness not in _RECENT_FRESHNESS_VALUES:
        return 0
    text = " ".join(
        _clean_text(item.get(key)).lower()
        for key in ("title", "content", "snippet", "url", "source_query", "query")
    )
    if not text:
        return 0
    current_year = datetime.now().year
    years = [int(match) for match in _YEAR_RE.findall(text)]
    if not years or current_year in years:
        return 0
    if max(years) < current_year:
        return 1
    return 0


def _is_fetchable_url(url: Any) -> bool:
    parsed = urlsplit(_clean_text(url))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _candidate_url_key(item: dict[str, Any]) -> str:
    return str(item.get("canonical_url") or _canonicalize_url(str(item.get("url") or "")))


def _candidate_domain(item: dict[str, Any]) -> str:
    return _clean_text(item.get("domain") or _domain_from_url(str(item.get("url") or ""))).lower()


def _candidate_query(item: dict[str, Any]) -> str:
    return _clean_text(item.get("source_query") or item.get("query")).lower()


def _candidate_query_label(item: dict[str, Any]) -> str:
    return _clean_text(item.get("source_query") or item.get("query"))


def _merge_fetch_source(
    item: dict[str, Any],
    fetch_payload: dict[str, Any],
    *,
    query: str,
    search_provider: str,
    search_backend: str,
) -> dict[str, Any]:
    url = _clean_text(fetch_payload.get("final_url") or fetch_payload.get("finalUrl") or fetch_payload.get("url") or item.get("url"))
    content = str(fetch_payload.get("content") or fetch_payload.get("text") or "")
    content_chars = _coerce_int(fetch_payload.get("content_chars"), default=len(content.strip()))
    min_content_chars = _coerce_int(fetch_payload.get("min_content_chars"), default=WEB_FETCH_MIN_CONTENT_CHARS)
    title = _clean_text(fetch_payload.get("title") or item.get("title"))
    status = fetch_payload.get("status")
    extractor = _clean_text(fetch_payload.get("extractor"))
    truncated = bool(fetch_payload.get("truncated"))
    blocked_or_challenge = looks_blocked_or_challenge(title=title, content=content, status=status)
    is_too_short = bool(fetch_payload.get("is_too_short")) or content_chars < min_content_chars
    has_main_content = bool(content.strip()) and not is_too_short and not blocked_or_challenge
    quality_score = _quality_score(
        content_chars=content_chars,
        min_content_chars=min_content_chars,
        has_title=bool(title),
        blocked_or_challenge=blocked_or_challenge,
        truncated=truncated,
        extractor=extractor,
    )
    return {
        "rank": item.get("rank"),
        "title": title,
        "url": url,
        "canonical_url": _canonicalize_url(url),
        "domain": _domain_from_url(url),
        "snippet": _clean_text(item.get("content")),
        "content": content,
        "content_chars": content_chars,
        "has_title": bool(title),
        "has_main_content": has_main_content,
        "is_too_short": is_too_short,
        "blocked_or_challenge": blocked_or_challenge,
        "quality_score": quality_score,
        "min_content_chars": min_content_chars,
        "truncated": truncated,
        "extractor": extractor,
        "status": status,
        "content_type": _clean_text(fetch_payload.get("content_type") or fetch_payload.get("contentType")),
        "fetch_attempts": [
            {
                "tool": "web_fetch",
                "extractor": extractor,
                "status": status,
                "content_chars": content_chars,
                "is_too_short": is_too_short,
                "blocked_or_challenge": blocked_or_challenge,
                "quality_score": quality_score,
            }
        ],
        "source_query": query,
        "search_provider": search_provider,
        "search_backend": search_backend,
        "search_freshness": _clean_text(item.get("search_freshness")),
        "search_rank": item.get("rank"),
    }


def _quality_score(
    *,
    content_chars: int,
    min_content_chars: int,
    has_title: bool,
    blocked_or_challenge: bool,
    truncated: bool,
    extractor: str,
) -> float:
    score = min(content_chars / max(min_content_chars, 1), 1.0) * 0.55
    if has_title:
        score += 0.15
    if not blocked_or_challenge:
        score += 0.15
    if extractor in {"trafilatura", "readability", "turndown", "jina", "firecrawl", "json"}:
        score += 0.10
    if not truncated:
        score += 0.05
    if blocked_or_challenge:
        score = min(score, 0.35)
    return round(min(max(score, 0.0), 1.0), 3)


def _canonicalize_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.netloc:
        return str(url or "").strip().rstrip("/")
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _domain_from_url(url: str) -> str:
    return urlsplit(str(url or "").strip()).netloc.lower()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
