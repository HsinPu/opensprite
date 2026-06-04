"""Tool evidence payloads used by agent completion checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..agent.verification_policy import VERIFICATION_NAME_METADATA_FIELD, VERIFICATION_STATUS_METADATA_FIELD
from .result_status import classify_tool_result_status


_WEB_SOURCE_TOOLS = frozenset({"web_search", "web_fetch", "web_research", "browser_navigate", "browser_snapshot"})
_SOURCE_SNIPPET_MAX_CHARS = 500


@dataclass(frozen=True)
class ToolEvidence:
    """One completed tool call summarized for contract evaluation."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    resource_ids: tuple[str, ...] = ()
    result_preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": dict(self.args),
            "ok": self.ok,
            "resource_ids": list(self.resource_ids),
            "result_preview": self.result_preview,
            "metadata": dict(self.metadata),
        }


def build_tool_evidence(tool_name: str, args: dict[str, Any], result: str, *, ok: bool) -> ToolEvidence:
    """Create default evidence for tools without resource-specific metadata."""
    effective_ok = (
        bool(ok)
        and not _tool_result_is_error(tool_name, result)
        and not _web_search_has_no_sources(tool_name, args, result)
        and not _web_research_has_no_sources(tool_name, result)
    )
    metadata = _build_metadata(tool_name, args, result) if effective_ok else _build_failed_metadata(tool_name, args, result)
    return ToolEvidence(
        name=tool_name,
        args=dict(args or {}),
        ok=effective_ok,
        result_preview=str(result or "")[:240],
        metadata=metadata,
    )


def _build_metadata(tool_name: str, args: dict[str, Any], result: str) -> dict[str, Any]:
    if tool_name == "exec":
        return _exec_metadata(args)
    if tool_name == "verify":
        return _verification_metadata(result)
    return _build_web_source_metadata(tool_name, args, result)


def _build_failed_metadata(tool_name: str, args: dict[str, Any], result: str) -> dict[str, Any]:
    metadata = _exec_metadata(args) if tool_name == "exec" else {}
    if tool_name == "verify":
        metadata.update(_verification_metadata(result))
    if tool_name == "web_search":
        metadata.update(_web_search_failure_metadata(args, result))
    if tool_name == "web_research":
        metadata.update(_web_research_failure_metadata(args, result))
    status = classify_tool_result_status(result)
    if not status.ok and status.error:
        metadata["error"] = status.error[:500]
    return metadata


def _verification_metadata(result: str) -> dict[str, Any]:
    from .verify import classify_verification_result

    outcome = classify_verification_result(result)
    metadata: dict[str, Any] = {
        VERIFICATION_STATUS_METADATA_FIELD: outcome.get("status"),
        "verification_ok": bool(outcome.get("ok")),
        "verification_attempted": bool(outcome.get("attempted")),
    }
    if outcome.get("name"):
        metadata[VERIFICATION_NAME_METADATA_FIELD] = outcome.get("name")
    return metadata


def _tool_result_is_error(tool_name: str, result: str) -> bool:
    text = str(result or "").strip()
    if not text:
        return False
    status = classify_tool_result_status(text)
    if not status.ok:
        return True
    return tool_name == "web_fetch" and status.status_code is not None


def _web_search_has_no_sources(tool_name: str, args: dict[str, Any], result: str) -> bool:
    if tool_name != "web_search":
        return False
    payload = _parse_json_object(result)
    if not isinstance(payload, dict):
        return False
    return not _web_search_sources(tool_name, args, payload, result)


def _web_search_failure_metadata(args: dict[str, Any], result: str) -> dict[str, Any]:
    payload = _parse_json_object(result)
    if not isinstance(payload, dict):
        return {}
    raw_items = payload.get("items", payload.get("results", []))
    result_count = len(raw_items) if isinstance(raw_items, list) else 0
    metadata: dict[str, Any] = {
        "source_count": 0,
        "result_count": result_count,
        "error": "web_search returned no traceable sources",
    }
    query = _clean_source_text(payload.get("query") or args.get("query"))
    if query:
        metadata["query"] = query
    provider = _clean_source_text(payload.get("provider"))
    if provider:
        metadata["provider"] = provider
    backend = _clean_source_text(payload.get("backend"))
    if backend:
        metadata["backend"] = backend
    return metadata


def _web_research_has_no_sources(tool_name: str, result: str) -> bool:
    if tool_name != "web_research":
        return False
    payload = _parse_json_object(result)
    if not isinstance(payload, dict):
        return False
    sources = payload.get("sources")
    fetched_sources = payload.get("fetched_sources")
    list_source_count = len(sources) if isinstance(sources, list) else 0
    list_fetched_count = len(fetched_sources) if isinstance(fetched_sources, list) else 0
    source_count = _coerce_int(payload.get("source_count"), default=list_source_count)
    fetched_count = _coerce_int(payload.get("fetched_count"), default=max(list_fetched_count, list_source_count))
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    target_met = bool(coverage.get("target_met")) if isinstance(coverage, dict) else False
    has_sources = source_count > 0 or _non_empty_list(sources) or _non_empty_list(fetched_sources)
    return not has_sources or (not target_met and fetched_count <= 0)


def _web_research_failure_metadata(args: dict[str, Any], result: str) -> dict[str, Any]:
    payload = _parse_json_object(result)
    if not isinstance(payload, dict):
        return {}
    metadata: dict[str, Any] = {
        "source_count": _coerce_int(payload.get("source_count"), default=0),
        "fetched_count": _coerce_int(payload.get("fetched_count"), default=0),
    }
    coverage = payload.get("coverage")
    if isinstance(coverage, dict):
        metadata["coverage"] = dict(coverage)
    for key in ("failed_sources", "search_attempts", "query_attempts"):
        value = payload.get(key)
        if value:
            metadata[key] = value
    query = _clean_source_text(payload.get("query") or args.get("query"))
    if query:
        metadata["query"] = query
    backend = _clean_source_text(payload.get("backend"))
    if backend:
        metadata["backend"] = backend
    metadata["error"] = "web_research returned no traceable sources"
    return metadata


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _exec_metadata(args: dict[str, Any]) -> dict[str, Any]:
    command = str((args or {}).get("command") or "")
    urls = tuple(dict.fromkeys(re.findall(r"https?://[^\s'\"<>]+", command)))
    if not urls:
        return {}
    return {
        "external_http_via_exec": True,
        "warning": "external HTTP fetched via exec instead of web_fetch",
        "urls": list(urls[:5]),
    }


def indexed_resource_id(prefix: str, value: Any) -> str:
    """Build a stable index resource id without letting malformed args crash evidence recording."""
    try:
        index = int(value)
    except (TypeError, ValueError):
        index = 0
    return f"{prefix}:{max(0, index)}"


def _build_web_source_metadata(tool_name: str, args: dict[str, Any], result: str) -> dict[str, Any]:
    if tool_name not in _WEB_SOURCE_TOOLS:
        return {}

    payload = _parse_json_object(result)
    if tool_name == "web_search":
        sources = _web_search_sources(tool_name, args, payload, result)
    elif tool_name == "web_fetch":
        sources = _web_fetch_sources(tool_name, args, payload, result)
    elif tool_name == "web_research":
        sources = _web_research_sources(tool_name, args, payload, result)
    else:
        sources = _browser_sources(tool_name, args, payload, result)
    if not sources:
        return {}
    metadata: dict[str, Any] = {"source_count": len(sources), "sources": sources}
    if tool_name == "web_research" and isinstance(payload, dict):
        coverage = payload.get("coverage")
        if isinstance(coverage, dict):
            metadata["coverage"] = dict(coverage)
    return metadata


def _parse_json_object(result: str) -> dict[str, Any] | None:
    stripped = str(result or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _web_search_sources(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any] | None,
    result: str,
) -> list[dict[str, Any]]:
    if payload is None:
        return []
    query = _clean_source_text(payload.get("query") or args.get("query"))
    provider = _clean_source_text(payload.get("provider"))
    backend = _clean_source_text(payload.get("backend"))
    raw_items = payload.get("items", payload.get("results", []))
    if not isinstance(raw_items, list):
        return []

    sources: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        source = _source_record(
            tool_name=tool_name,
            title=item.get("title"),
            url=item.get("url"),
            snippet=item.get("content") or item.get("snippet") or item.get("summary"),
            query=query,
            provider=provider,
            extra={"backend": backend},
        )
        if source:
            sources.append(source)
    return sources


def _web_fetch_sources(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any] | None,
    result: str,
) -> list[dict[str, Any]]:
    if payload is None:
        source = _source_record(
            tool_name=tool_name,
            title="",
            url=args.get("url"),
            snippet=result,
            query=args.get("url"),
            provider=tool_name,
        )
        return [source] if source else []

    url = payload.get("final_url") or payload.get("finalUrl") or payload.get("url") or args.get("url")
    snippet = payload.get("content") or payload.get("text") or payload.get("summary")
    source = _source_record(
        tool_name=tool_name,
        title=payload.get("title"),
        url=url,
        snippet=snippet,
        query=payload.get("query") or args.get("url"),
        provider=payload.get("provider") or tool_name,
        extra={
            "content_chars": _coerce_int(payload.get("content_chars"), default=len(_clean_source_text(snippet))),
            "has_title": bool(_clean_source_text(payload.get("title"))),
            "is_too_short": bool(payload.get("is_too_short")),
            "min_content_chars": _coerce_int(payload.get("min_content_chars"), default=0),
            "truncated": bool(payload.get("truncated")),
            "extractor": _clean_source_text(payload.get("extractor")),
        },
    )
    return [source] if source else []


def _web_research_sources(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any] | None,
    result: str,
) -> list[dict[str, Any]]:
    if payload is None:
        return []
    query = _clean_source_text(payload.get("query") or args.get("query"))
    provider = _clean_source_text(payload.get("provider"))
    backend = _clean_source_text(payload.get("backend"))
    raw_sources = payload.get("sources") or payload.get("fetched_sources") or []
    if not isinstance(raw_sources, list):
        return []

    sources: list[dict[str, Any]] = []
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            continue
        raw_tool_name = _clean_source_text(raw_source.get("tool_name")) or (
            "web_fetch" if raw_source.get("fetched") or raw_source.get("content") else "web_search"
        )
        source = _source_record(
            tool_name=raw_tool_name,
            title=raw_source.get("title"),
            url=raw_source.get("url"),
            snippet=raw_source.get("content") or raw_source.get("snippet") or raw_source.get("summary"),
            query=raw_source.get("source_query") or query,
            provider=raw_source.get("search_provider") or provider or tool_name,
            extra={
                "content_chars": _coerce_int(raw_source.get("content_chars"), default=len(_clean_source_text(raw_source.get("content")))),
                "has_title": bool(_clean_source_text(raw_source.get("title"))),
                "is_too_short": bool(raw_source.get("is_too_short")),
                "has_main_content": bool(raw_source.get("has_main_content")),
                "blocked_or_challenge": bool(raw_source.get("blocked_or_challenge")),
                "quality_score": raw_source.get("quality_score"),
                "min_content_chars": _coerce_int(raw_source.get("min_content_chars"), default=0),
                "truncated": bool(raw_source.get("truncated")),
                "extractor": _clean_source_text(raw_source.get("extractor")),
                "search_rank": raw_source.get("search_rank"),
                "search_provider": _clean_source_text(raw_source.get("search_provider")),
                "search_backend": _clean_source_text(raw_source.get("search_backend") or backend),
            },
        )
        if source:
            sources.append(source)
    return sources


def _browser_sources(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any] | None,
    result: str,
) -> list[dict[str, Any]]:
    if payload is None:
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    assert isinstance(data, dict)
    url = payload.get("final_url") or payload.get("finalUrl") or payload.get("url") or data.get("url") or args.get("url")
    title = payload.get("title") or data.get("title")
    snippet = (
        payload.get("snapshot")
        or data.get("snapshot")
        or payload.get("content")
        or data.get("content")
        or payload.get("output")
        or result
    )
    source = _source_record(
        tool_name=tool_name,
        title=title or url,
        url=url,
        snippet=snippet,
        query=args.get("url") or url,
        provider="browser",
    )
    return [source] if source else []


def _source_record(
    *,
    tool_name: str,
    title: Any,
    url: Any,
    snippet: Any,
    query: Any,
    provider: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_url = _clean_source_text(url)
    clean_title = _clean_source_text(title)
    clean_snippet = _clean_source_text(snippet)[:_SOURCE_SNIPPET_MAX_CHARS]
    if not clean_url or not (clean_title or clean_snippet):
        return {}
    record: dict[str, Any] = {
        "tool_name": tool_name,
        "url": clean_url,
        "title": clean_title,
        "snippet": clean_snippet,
        "query": _clean_source_text(query),
        "provider": _clean_source_text(provider),
    }
    if extra:
        record.update({key: value for key, value in extra.items() if value is not None})
    return record


def _clean_source_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
