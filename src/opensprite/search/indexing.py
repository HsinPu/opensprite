"""Shared search indexing helpers for SQLite-backed storage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..storage.base import StoredMessage

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class SearchChunkPayload:
    """Searchable chunk payload stored in the shared SQLite index."""

    source_type: str
    content: str
    created_at: float
    role: str | None = None
    tool_name: str | None = None
    query: str | None = None
    title: str | None = None
    url: str | None = None
    chunk_index: int = 0


@dataclass(frozen=True)
class KnowledgeDocument:
    """Structured knowledge source parsed from a tool result."""

    source_type: str
    tool_name: str
    query: str
    title: str
    url: str
    raw_result: str
    chunks: list[str]
    summary: str = ""
    provider: str = ""
    extractor: str = ""
    status: int | None = None
    content_type: str = ""
    truncated: bool | None = None


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Normalize text and split it into overlapping chunks."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(end - chunk_overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


def build_history_chunks(
    *,
    role: str,
    content: str,
    tool_name: str | None,
    created_at: float,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[SearchChunkPayload]:
    """Build history chunk payloads from one stored message."""
    return [
        SearchChunkPayload(
            source_type="history",
            role=role,
            tool_name=tool_name,
            content=chunk,
            chunk_index=chunk_index,
            created_at=created_at,
        )
        for chunk_index, chunk in enumerate(
            chunk_text(content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        )
    ]


def build_knowledge_documents(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    result: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[KnowledgeDocument]:
    """Build structured knowledge documents from a tool result."""
    if tool_name == "web_search":
        return _build_web_search_documents(
            tool_args,
            result,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    if tool_name == "web_fetch":
        return _build_web_fetch_documents(
            tool_args,
            result,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    return []


def build_knowledge_documents_from_message(
    message: StoredMessage,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[KnowledgeDocument]:
    """Rebuild knowledge documents from a stored tool message."""
    tool_name = message.tool_name or guess_tool_name(message.content)
    if tool_name is None:
        return []
    return build_knowledge_documents(
        tool_name=tool_name,
        tool_args={},
        result=message.content,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def build_knowledge_chunks(document: KnowledgeDocument, *, created_at: float) -> list[SearchChunkPayload]:
    """Build searchable chunk payloads from one structured knowledge document."""
    return [
        SearchChunkPayload(
            source_type=document.source_type,
            tool_name=document.tool_name,
            query=document.query,
            title=document.title,
            url=document.url,
            content=chunk,
            chunk_index=chunk_index,
            created_at=created_at,
        )
        for chunk_index, chunk in enumerate(document.chunks)
    ]


def guess_tool_name(content: str) -> str | None:
    """Infer the originating tool from a stored tool result payload."""
    stripped = content.strip()
    if stripped.startswith("Results for:"):
        return "web_search"
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except Exception:
            return None
        if isinstance(payload, dict) and (
            payload.get("type") == "web_search"
            or ("query" in payload and isinstance(payload.get("items", payload.get("results")), list))
        ):
            return "web_search"
        if isinstance(payload, dict) and (
            payload.get("type") == "web_fetch"
            or (("url" in payload or "finalUrl" in payload or "final_url" in payload) and ("text" in payload or "content" in payload))
        ):
            return "web_fetch"
    return None


def parse_web_search_results(result: str) -> tuple[str, list[dict[str, str]]]:
    """Parse the structured or legacy output of ``web_search`` into items."""
    stripped = result.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            query = str(payload.get("query", "") or "")
            raw_results = payload.get("items", payload.get("results", []))
            items: list[dict[str, str]] = []
            if isinstance(raw_results, list):
                for item in raw_results:
                    if not isinstance(item, dict):
                        continue
                    items.append(
                        {
                            "title": str(item.get("title", "") or ""),
                            "url": str(item.get("url", "") or ""),
                            "content": str(item.get("content", "") or ""),
                        }
                    )
            return query, items

    query = ""
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in result.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not query and line.startswith("Results for:"):
            query = line.split(":", 1)[1].strip()
            continue

        match = re.match(r"^(\d+)\.\s+(.*)$", line)
        if match:
            if current:
                items.append(current)
            current = {"title": match.group(2).strip(), "url": "", "content": ""}
            continue

        if current is None:
            continue

        if not current["url"] and line.startswith(("http://", "https://")):
            current["url"] = line
        else:
            current["content"] = f"{current['content']} {line}".strip()

    if current:
        items.append(current)
    return query, items


def _build_web_search_documents(
    tool_args: dict[str, Any],
    result: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[KnowledgeDocument]:
    query = str(tool_args.get("query", "") or "").strip()
    provider = str(tool_args.get("provider", "") or "")
    extractor = "search"
    content_type = "application/json"
    truncated: bool | None = False
    try:
        payload = json.loads(result)
        if isinstance(payload, dict):
            provider = str(payload.get("provider", provider) or provider)
            extractor = str(payload.get("extractor", extractor) or extractor)
            content_type = str(payload.get("content_type", content_type) or content_type)
            raw_truncated = payload.get("truncated")
            truncated = raw_truncated if isinstance(raw_truncated, bool) else truncated
    except Exception:
        pass
    parsed_query, items = parse_web_search_results(result)
    query = query or parsed_query
    if not items:
        items = [{"title": f"Search: {query or 'unknown'}", "url": "", "content": result}]

    documents = []
    for item in items:
        chunks = chunk_text(item.get("content", "") or result, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks:
            continue
        documents.append(
            KnowledgeDocument(
                source_type="web_search",
                tool_name="web_search",
                query=query,
                title=item.get("title", ""),
                url=item.get("url", ""),
                summary=str(item.get("content", "") or ""),
                provider=provider,
                extractor=extractor,
                content_type=content_type,
                truncated=truncated,
                raw_result=result,
                chunks=chunks,
            )
        )
    return documents


def _build_web_fetch_documents(
    tool_args: dict[str, Any],
    result: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[KnowledgeDocument]:
    query = str(tool_args.get("url", "") or "")
    title = ""
    url = query
    content = result
    summary = ""
    provider = ""
    extractor = ""
    status: int | None = None
    content_type = ""
    truncated: bool | None = None
    try:
        payload = json.loads(result)
        if isinstance(payload, dict):
            title = str(payload.get("title", "") or "")
            url = str(payload.get("final_url", payload.get("finalUrl", payload.get("url", query))) or query)
            content = str(payload.get("content", payload.get("text", result)) or result)
            summary = str(payload.get("summary", "") or "")
            provider = str(payload.get("provider", "") or "")
            extractor = str(payload.get("extractor", "") or "")
            raw_status = payload.get("status")
            status = int(raw_status) if isinstance(raw_status, (int, float)) else None
            content_type = str(payload.get("content_type", payload.get("contentType", "")) or "")
            raw_truncated = payload.get("truncated")
            truncated = raw_truncated if isinstance(raw_truncated, bool) else None
    except Exception:
        pass

    chunks = chunk_text(content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunks:
        return []
    return [
        KnowledgeDocument(
            source_type="web_fetch",
            tool_name="web_fetch",
            query=query,
            title=title,
            url=url,
            summary=summary,
            provider=provider,
            extractor=extractor,
            status=status,
            content_type=content_type,
            truncated=truncated,
            raw_result=result,
            chunks=chunks,
        )
    ]
