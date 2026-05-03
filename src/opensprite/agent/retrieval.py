"""Proactive retrieval helpers for turn-level prompt augmentation."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

from ..search.base import SearchHit, SearchStore


class ProactiveRetrievalService:
    """Fetch compact prior context when the current turn strongly implies follow-up work."""

    _RETRIEVAL_WORD_KEYWORDS = (
        "again",
        "before",
        "earlier",
        "history",
        "last time",
        "previous",
        "revisit",
        "repeat",
        "that fix",
        "that search",
        "that change",
    )
    _RETRIEVAL_TEXT_MARKERS = (
        "之前",
        "先前",
        "剛剛",
        "上次",
        "剛才",
        "前面",
        "那個修復",
        "那次搜尋",
    )
    _RETRIEVAL_WORD_PATTERN = re.compile(
        r"\b(?:" + "|".join(re.escape(keyword) for keyword in _RETRIEVAL_WORD_KEYWORDS) + r")\b",
        re.IGNORECASE,
    )

    def __init__(self, *, search_store: SearchStore | None):
        self.search_store = search_store

    @classmethod
    def should_retrieve(cls, current_message: str) -> bool:
        text = str(current_message or "").strip()
        if not text:
            return False
        lowered = text.lower()
        return bool(cls._RETRIEVAL_WORD_PATTERN.search(text)) or any(marker in lowered for marker in cls._RETRIEVAL_TEXT_MARKERS)

    async def build_context(self, *, session_id: str, current_message: str) -> str:
        if self.search_store is None or not self.should_retrieve(current_message):
            return ""

        history_hits, knowledge_hits = await asyncio.gather(
            self.search_store.search_history(session_id=session_id, query=current_message, limit=3),
            self.search_store.search_knowledge(session_id=session_id, query=current_message, limit=2),
        )
        if not history_hits and not knowledge_hits:
            return ""

        sections = [
            "# Proactive Retrieval Context",
            "This turn appears to refer to earlier chat context or prior research. Use the snippets below before asking the user to restate information.",
        ]
        if history_hits:
            sections.extend(["", "## Retrieved History", *self._format_history_hits(history_hits)])
        if knowledge_hits:
            sections.extend(["", "## Retrieved Knowledge", *self._format_knowledge_hits(knowledge_hits)])
        return "\n".join(sections).strip()

    @staticmethod
    def _format_time(created_at: float) -> str:
        if not created_at:
            return "unknown"
        return datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _truncate(text: str, limit: int = 180) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + "..."

    def _format_history_hits(self, hits: list[SearchHit]) -> list[str]:
        lines: list[str] = []
        for index, hit in enumerate(hits, start=1):
            label = hit.role or "message"
            if hit.tool_name:
                label = f"{label}:{hit.tool_name}"
            lines.append(f"{index}. [{label}] {self._format_time(hit.created_at)}")
            lines.append(f"   {self._truncate(hit.content)}")
        return lines

    def _format_knowledge_hits(self, hits: list[SearchHit]) -> list[str]:
        lines: list[str] = []
        for index, hit in enumerate(hits, start=1):
            title = hit.title or hit.source_type
            lines.append(f"{index}. [{hit.source_type}] {title}")
            if hit.url:
                lines.append(f"   {hit.url}")
            if hit.summary:
                lines.append(f"   summary: {self._truncate(hit.summary, limit=120)}")
            lines.append(f"   {self._truncate(hit.content)}")
        return lines
