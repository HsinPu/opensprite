"""Tool evidence payloads used by agent completion checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


_WEB_SOURCE_TOOLS = frozenset({"web_search", "web_fetch", "browser_navigate", "browser_snapshot"})
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
    metadata = _build_web_source_metadata(tool_name, args, result) if ok else {}
    return ToolEvidence(
        name=tool_name,
        args=dict(args or {}),
        ok=ok,
        result_preview=str(result or "")[:240],
        metadata=metadata,
    )


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
    else:
        sources = _browser_sources(tool_name, args, payload, result)
    if not sources:
        return {}
    return {"source_count": len(sources), "sources": sources}


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
) -> list[dict[str, str]]:
    if payload is None:
        return []
    query = _clean_source_text(payload.get("query") or args.get("query"))
    provider = _clean_source_text(payload.get("provider"))
    raw_items = payload.get("items", payload.get("results", []))
    if not isinstance(raw_items, list):
        return []

    sources: list[dict[str, str]] = []
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
        )
        if source:
            sources.append(source)
    return sources


def _web_fetch_sources(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any] | None,
    result: str,
) -> list[dict[str, str]]:
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
    source = _source_record(
        tool_name=tool_name,
        title=payload.get("title"),
        url=url,
        snippet=payload.get("content") or payload.get("text") or payload.get("summary"),
        query=payload.get("query") or args.get("url"),
        provider=payload.get("provider") or tool_name,
    )
    return [source] if source else []


def _browser_sources(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any] | None,
    result: str,
) -> list[dict[str, str]]:
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
) -> dict[str, str]:
    clean_url = _clean_source_text(url)
    clean_title = _clean_source_text(title)
    clean_snippet = _clean_source_text(snippet)[:_SOURCE_SNIPPET_MAX_CHARS]
    if not clean_url or not (clean_title or clean_snippet):
        return {}
    return {
        "tool_name": tool_name,
        "url": clean_url,
        "title": clean_title,
        "snippet": clean_snippet,
        "query": _clean_source_text(query),
        "provider": _clean_source_text(provider),
    }


def _clean_source_text(value: Any) -> str:
    return " ".join(str(value or "").split())
