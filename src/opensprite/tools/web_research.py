"""High-level web research orchestration tool."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from ..config.schema import WebFetchToolConfig, WebSearchToolConfig
from ..search.base import SearchHit, SearchStore
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN
from .web_fetch import WEB_FETCH_MIN_CONTENT_CHARS, WebFetchTool
from .web_search import FRESHNESS_VALUES, WebSearchTool, _normalize_freshness


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
        knowledge_store: SearchStore | None = None,
        get_session_id: Callable[[], str | None] | None = None,
        knowledge_limit: int = 5,
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
        self.knowledge_store = knowledge_store
        self.get_session_id = get_session_id or (lambda: None)
        self.knowledge_limit = max(1, int(knowledge_limit or 1))

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
                    "description": "Search candidates to inspect before dedupe",
                    "default": min(8, self.search_config.max_results),
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
                    "description": "Recency filter passed through to web_search",
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
        search_count = min(max(int(count or min(8, self.search_config.max_results)), 1), self.search_config.max_results)
        target_fetches = min(max(int(fetch_count or 2), 1), 5)
        effective_freshness = _normalize_freshness(freshness, self.search_config.freshness)
        effective_max_chars = max_chars if max_chars is not None else self.fetch_config.max_chars
        research_queries = _research_queries(query, queries)
        reused_sources, reuse_attempt, reuse_attempts = await self._reuse_knowledge_sources_for_queries(
            queries=research_queries,
            target_count=target_fetches,
        )
        fetched_sources: list[dict[str, Any]] = list(reused_sources)
        failed_sources: list[dict[str, Any]] = []
        source_records: list[dict[str, Any]] = [
            {**source, "tool_name": "web_fetch", "fetched": True, "reused": True}
            for source in reused_sources
        ]
        reused_urls = {str(source.get("canonical_url") or source.get("url") or "") for source in reused_sources}

        if len(fetched_sources) >= target_fetches:
            return _research_payload(
                query=query,
                freshness=effective_freshness,
                search_provider="search_knowledge",
                search_items=[],
                fetched_sources=fetched_sources[:target_fetches],
                failed_sources=failed_sources,
                sources=source_records[:target_fetches],
                target_fetch_count=target_fetches,
                search_attempts=[],
                query_attempts=[],
                reuse_attempt=reuse_attempt,
                reuse_attempts=reuse_attempts,
                queries=research_queries,
            )

        search_items, search_provider, search_attempts, query_attempts = await self._search_queries_with_fallback(
            queries=research_queries,
            count=search_count,
            freshness=effective_freshness,
        )
        if not search_items:
            return _research_payload(
                query=query,
                freshness=effective_freshness,
                search_provider="search_knowledge" if fetched_sources else search_provider,
                search_items=[],
                fetched_sources=fetched_sources,
                failed_sources=[{"reason": "web_search returned no structured result with fetchable URLs"}],
                sources=source_records,
                target_fetch_count=target_fetches,
                search_attempts=search_attempts,
                query_attempts=query_attempts,
                reuse_attempt=reuse_attempt,
                reuse_attempts=reuse_attempts,
                queries=research_queries,
            )

        fetched_by_candidate_url: dict[str, dict[str, Any]] = {}
        fetch_candidates = _prioritize_research_candidates(
            search_items,
            existing_sources=fetched_sources,
        )
        for item in fetch_candidates:
            if len(fetched_sources) >= target_fetches:
                break
            url = item.get("url", "")
            if not url:
                failed_sources.append({**item, "reason": "missing url"})
                continue
            canonical_url = _candidate_url_key(item)
            if canonical_url in reused_urls:
                continue

            item_search_provider = str(item.get("search_provider") or search_provider)
            fetch_result = await self.fetch_tool._execute(url=url, max_chars=effective_max_chars)
            fetch_payload = _parse_json_object(fetch_result)
            if fetch_payload is None:
                failed_sources.append({**item, "reason": str(fetch_result or "web_fetch returned no structured result")[:500]})
                continue

            fetched = _merge_fetch_source(
                item,
                fetch_payload,
                query=str(item.get("source_query") or query),
                search_provider=item_search_provider,
            )
            if fetched.get("blocked_or_challenge"):
                failed_sources.append({**fetched, "reason": "fetched content looked blocked or challenged"})
                continue
            if fetched.get("is_too_short") or not fetched.get("has_main_content"):
                failed_sources.append({**fetched, "reason": "fetched content was too short"})
                continue
            fetched_sources.append(fetched)
            fetched_by_candidate_url[canonical_url] = fetched
            reused_urls.add(canonical_url)
            reused_urls.add(str(fetched.get("canonical_url") or fetched.get("url") or ""))

        for item in search_items:
            item_search_provider = str(item.get("search_provider") or search_provider)
            source_records.append({**item, "tool_name": "web_search", "fetched": False, "search_provider": item_search_provider})
            fetched = fetched_by_candidate_url.get(_candidate_url_key(item))
            if fetched is not None:
                source_records.append({**fetched, "tool_name": "web_fetch", "fetched": True})

        return _research_payload(
            query=query,
            freshness=effective_freshness,
            search_provider=search_provider,
            search_items=search_items,
            fetched_sources=fetched_sources,
            failed_sources=failed_sources,
            sources=source_records,
            target_fetch_count=target_fetches,
            search_attempts=search_attempts,
            query_attempts=query_attempts,
            reuse_attempt=reuse_attempt,
            reuse_attempts=reuse_attempts,
            queries=research_queries,
        )

    async def _reuse_knowledge_sources_for_queries(
        self,
        *,
        queries: list[str],
        target_count: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        sources: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for current_query in queries:
            remaining = max(0, target_count - len(sources))
            if remaining <= 0:
                break
            query_sources, attempt = await self._reuse_knowledge_sources(query=current_query, target_count=remaining)
            attempts.append({"query": current_query, **attempt})
            for source in query_sources:
                key = str(source.get("canonical_url") or source.get("url") or "")
                if not key or key in seen_urls:
                    continue
                seen_urls.add(key)
                sources.append(source)
                if len(sources) >= target_count:
                    break

        return sources, _aggregate_reuse_attempts(attempts), attempts

    async def _reuse_knowledge_sources(
        self,
        *,
        query: str,
        target_count: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.knowledge_store is None:
            return [], {"source": "search_knowledge", "ok": False, "reason": "not configured"}
        session_id = self.get_session_id()
        if not session_id:
            return [], {"source": "search_knowledge", "ok": False, "reason": "missing session_id"}
        try:
            hits = await self.knowledge_store.search_knowledge(
                session_id=session_id,
                query=query,
                limit=max(self.knowledge_limit, target_count * 3),
                source_type="web_fetch",
            )
        except Exception as exc:
            return [], {"source": "search_knowledge", "ok": False, "reason": str(exc)[:500]}

        sources: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for hit in hits:
            source = _reuse_source_from_hit(hit, query=query)
            if source is None:
                continue
            key = str(source.get("canonical_url") or source.get("url") or "")
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            sources.append(source)
            if len(sources) >= target_count:
                break

        return sources, {
            "source": "search_knowledge",
            "ok": bool(sources),
            "result_count": len(hits),
            "reused_count": len(sources),
        }

    async def _search_queries_with_fallback(
        self,
        *,
        queries: list[str],
        count: int,
        freshness: str,
    ) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], list[dict[str, Any]]]:
        all_items: list[dict[str, Any]] = []
        all_attempts: list[dict[str, Any]] = []
        query_attempts: list[dict[str, Any]] = []
        selected_provider = ""
        fallback_provider = ""
        for current_query in queries:
            payload, items, provider, attempts = await self._search_with_fallback(
                query=current_query,
                count=count,
                freshness=freshness,
            )
            all_attempts.extend(attempts)
            query_attempts.append(
                {
                    "query": current_query,
                    "provider": provider,
                    "ok": payload is not None and bool(items),
                    "result_count": len(items),
                    "search_attempts": attempts,
                }
            )
            if not fallback_provider and provider:
                fallback_provider = provider
            if items and not selected_provider and provider:
                selected_provider = provider
            all_items.extend({**item, "source_query": current_query} for item in items)

        return _dedupe_search_items(all_items, limit=max(count * max(len(queries), 1), count)), selected_provider or fallback_provider, all_attempts, query_attempts

    async def _search_with_fallback(
        self,
        *,
        query: str,
        count: int,
        freshness: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str, list[dict[str, Any]]]:
        attempts: list[dict[str, Any]] = []
        last_provider = getattr(self.search_tool, "provider", self.search_config.provider)
        if self._custom_search_tool:
            providers = [str(last_provider or self.search_config.provider or "duckduckgo")]
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
            last_provider = provider_name
            items = _dedupe_search_items(_coerce_search_items(payload or {}), limit=count) if payload else []
            fetchable_count = sum(1 for item in items if item.get("url"))
            attempts.append(
                {
                    "provider": provider_name,
                    "configured_provider": provider,
                    "ok": payload is not None and fetchable_count > 0,
                    "result_count": len(items),
                    "fetchable_count": fetchable_count,
                    "error": "" if payload is not None else str(result or "")[:500],
                }
            )
            if payload is not None and fetchable_count > 0:
                return payload, [{**item, "search_provider": provider_name, "source_query": query} for item in items], provider_name, attempts
        return None, [], str(last_provider or ""), attempts

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
    search_items: list[dict[str, Any]],
    fetched_sources: list[dict[str, Any]],
    failed_sources: list[dict[str, Any]],
    sources: list[dict[str, Any]] | None = None,
    queries: list[str] | None = None,
    target_fetch_count: int | None = None,
    search_attempts: list[dict[str, Any]] | None = None,
    query_attempts: list[dict[str, Any]] | None = None,
    reuse_attempt: dict[str, Any] | None = None,
    reuse_attempts: list[dict[str, Any]] | None = None,
) -> str:
    reused_count = sum(1 for item in fetched_sources if item.get("reused"))
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
            "reuse_attempt": reuse_attempt or {"source": "search_knowledge", "ok": False, "reason": "not configured"},
            "reuse_attempts": reuse_attempts or [],
            "reused_count": reused_count,
            "coverage": coverage,
        },
        ensure_ascii=False,
    )


def _research_queries(query: str, queries: list[str] | None) -> list[str]:
    values = [_clean_text(query)]
    if isinstance(queries, list):
        values.extend(_clean_text(value) for value in queries[:5])
    elif queries is not None:
        values.append(_clean_text(queries))

    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out or [_clean_text(query)]


def _aggregate_reuse_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    if not attempts:
        return {"source": "search_knowledge", "ok": False, "reason": "not configured"}
    if len(attempts) == 1:
        return {key: value for key, value in attempts[0].items() if key != "query"}

    result_count = sum(_coerce_int(attempt.get("result_count"), default=0) for attempt in attempts)
    reused_count = sum(_coerce_int(attempt.get("reused_count"), default=0) for attempt in attempts)
    reasons: list[str] = []
    seen_reasons: set[str] = set()
    for attempt in attempts:
        reason = str(attempt.get("reason") or "").strip()
        key = reason.lower()
        if not reason or key in seen_reasons:
            continue
        seen_reasons.add(key)
        reasons.append(reason)
    aggregate: dict[str, Any] = {
        "source": "search_knowledge",
        "ok": reused_count > 0,
        "query_count": len(attempts),
        "result_count": result_count,
        "reused_count": reused_count,
    }
    if reasons and reused_count == 0:
        aggregate["reason"] = "; ".join(reasons)[:500]
    return aggregate


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
    configured = (configured_provider or config.provider or "duckduckgo").strip().lower() or "duckduckgo"
    candidates = [configured]
    probe_tool = WebSearchTool(config=config)
    if probe_tool.brave_api_key:
        candidates.append("brave")
    if probe_tool.tavily_api_key:
        candidates.append("tavily")
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
) -> list[dict[str, Any]]:
    if len(items) <= 1:
        return items
    item_queries = {_candidate_query(item) for item in items}
    item_queries.discard("")
    if len(item_queries) <= 1:
        return items

    selected: list[dict[str, Any]] = []
    remaining = list(items)
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
) -> dict[str, Any]:
    url = _clean_text(fetch_payload.get("final_url") or fetch_payload.get("finalUrl") or fetch_payload.get("url") or item.get("url"))
    content = str(fetch_payload.get("content") or fetch_payload.get("text") or "")
    content_chars = _coerce_int(fetch_payload.get("content_chars"), default=len(content.strip()))
    min_content_chars = _coerce_int(fetch_payload.get("min_content_chars"), default=WEB_FETCH_MIN_CONTENT_CHARS)
    title = _clean_text(fetch_payload.get("title") or item.get("title"))
    status = fetch_payload.get("status")
    extractor = _clean_text(fetch_payload.get("extractor"))
    truncated = bool(fetch_payload.get("truncated"))
    blocked_or_challenge = _blocked_or_challenge(title=title, content=content, status=status)
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
        "reused": False,
        "source_query": query,
        "search_provider": search_provider,
        "search_rank": item.get("rank"),
    }


def _reuse_source_from_hit(hit: SearchHit, *, query: str) -> dict[str, Any] | None:
    content = str(hit.content or "")
    content_chars = len(content.strip())
    min_content_chars = WEB_FETCH_MIN_CONTENT_CHARS
    title = _clean_text(hit.title)
    url = _clean_text(hit.url)
    status = hit.status
    extractor = _clean_text(hit.extractor)
    truncated = bool(hit.truncated)
    blocked_or_challenge = _blocked_or_challenge(title=title, content=content, status=status)
    is_too_short = content_chars < min_content_chars
    has_main_content = bool(content.strip()) and not is_too_short and not blocked_or_challenge
    if not has_main_content:
        return None
    quality_score = _quality_score(
        content_chars=content_chars,
        min_content_chars=min_content_chars,
        has_title=bool(title),
        blocked_or_challenge=blocked_or_challenge,
        truncated=truncated,
        extractor=extractor,
    )
    canonical_url = _canonicalize_url(url)
    return {
        "rank": None,
        "title": title,
        "url": url,
        "canonical_url": canonical_url,
        "domain": _domain_from_url(url),
        "snippet": _clean_text(hit.summary or content[:500]),
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
        "content_type": _clean_text(hit.content_type),
        "fetch_attempts": [
            {
                "tool": "search_knowledge",
                "extractor": extractor,
                "status": status,
                "content_chars": content_chars,
                "is_too_short": is_too_short,
                "blocked_or_challenge": blocked_or_challenge,
                "quality_score": quality_score,
            }
        ],
        "reused": True,
        "reuse_source": "search_knowledge",
        "source_query": _clean_text(hit.query) or query,
        "search_provider": _clean_text(hit.provider),
        "search_rank": None,
    }


def _blocked_or_challenge(*, title: str, content: str, status: Any) -> bool:
    if _coerce_int(status, default=0) in {401, 403, 407, 408, 409, 429, 451, 503}:
        return True
    normalized = f"{title}\n{content}".lower()
    markers = (
        "captcha",
        "cloudflare",
        "access denied",
        "forbidden",
        "enable javascript",
        "verify you are human",
        "prove you are human",
        "unusual traffic",
        "rate limit",
        "too many requests",
    )
    return any(marker in normalized for marker in markers)


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
