"""LanceDB-backed per-chat search store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

import pyarrow as pa

from .base import SearchHit, SearchStore
from ..storage.base import StorageProvider, StoredMessage
from ..utils.log import logger


class LanceDBSearchStore(SearchStore):
    """Per-chat searchable history and knowledge index backed by LanceDB."""

    HISTORY_TABLE = "history_chunks"
    KNOWLEDGE_TABLE = "knowledge_chunks"
    STATE_TABLE = "chat_state"

    def __init__(
        self,
        path: str | Path,
        history_top_k: int = 5,
        knowledge_top_k: int = 5,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
    ):
        try:
            import lancedb
        except ImportError as exc:
            raise RuntimeError("LanceDB search is enabled but 'lancedb' is not installed") from exc

        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history_top_k = history_top_k
        self.knowledge_top_k = knowledge_top_k
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._lock = asyncio.Lock()
        self._db = lancedb.connect(str(self.path))
        self._history = self._ensure_table(
            self.HISTORY_TABLE,
            pa.schema(
                [
                    ("id", pa.string()),
                    ("chat_id", pa.string()),
                    ("role", pa.string()),
                    ("tool_name", pa.string()),
                    ("content", pa.string()),
                    ("chunk_index", pa.int32()),
                    ("created_at", pa.float64()),
                ]
            ),
            ["content"],
        )
        self._knowledge = self._ensure_table(
            self.KNOWLEDGE_TABLE,
            pa.schema(
                [
                    ("id", pa.string()),
                    ("chat_id", pa.string()),
                    ("source_type", pa.string()),
                    ("tool_name", pa.string()),
                    ("query", pa.string()),
                    ("title", pa.string()),
                    ("url", pa.string()),
                    ("content", pa.string()),
                    ("chunk_index", pa.int32()),
                    ("created_at", pa.float64()),
                ]
            ),
            ["query", "title", "url", "content"],
        )
        self._state = self._ensure_table(
            self.STATE_TABLE,
            pa.schema(
                [
                    ("chat_id", pa.string()),
                    ("history_count", pa.int64()),
                ]
            ),
            None,
        )

    async def sync_from_storage(self, storage: StorageProvider) -> None:
        async with self._lock:
            for chat_id in await storage.get_all_chats():
                messages = await storage.get_messages(chat_id)
                synced_count = self._get_history_count(chat_id)
                if synced_count > len(messages):
                    self._clear_chat_locked(chat_id)
                    synced_count = 0

                if synced_count >= len(messages):
                    continue

                for message in messages[synced_count:]:
                    created_at = message.timestamp or time.time()
                    self._add_history_rows(
                        self._build_history_rows(
                            chat_id=chat_id,
                            role=message.role,
                            content=message.content,
                            tool_name=message.tool_name,
                            created_at=created_at,
                        )
                    )
                    knowledge_rows = self._build_knowledge_rows_from_message(chat_id, message, created_at)
                    if knowledge_rows:
                        self._add_knowledge_rows(knowledge_rows)

                self._set_history_count(chat_id, len(messages))

    async def index_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        created_at: float | None = None,
    ) -> None:
        async with self._lock:
            self._add_history_rows(
                self._build_history_rows(
                    chat_id=chat_id,
                    role=role,
                    content=content,
                    tool_name=tool_name,
                    created_at=created_at or time.time(),
                )
            )
            self._set_history_count(chat_id, self._get_history_count(chat_id) + 1)

    async def index_tool_result(
        self,
        chat_id: str,
        tool_name: str,
        tool_args: dict,
        result: str,
        created_at: float | None = None,
    ) -> None:
        if tool_name not in {"web_search", "web_fetch"}:
            return

        async with self._lock:
            rows = self._build_knowledge_rows(
                chat_id=chat_id,
                tool_name=tool_name,
                tool_args=tool_args,
                result=result,
                created_at=created_at or time.time(),
            )
            if rows:
                self._add_knowledge_rows(rows)

    async def search_history(self, chat_id: str, query: str, limit: int = 5) -> list[SearchHit]:
        async with self._lock:
            rows = self._search_table(
                table=self._history,
                query=query,
                where=f"chat_id = '{self._escape(chat_id)}'",
                limit=limit or self.history_top_k,
            )
            return [self._history_hit(row) for row in rows]

    async def search_knowledge(
        self,
        chat_id: str,
        query: str,
        limit: int = 5,
        source_type: str | None = None,
    ) -> list[SearchHit]:
        async with self._lock:
            where = f"chat_id = '{self._escape(chat_id)}'"
            if source_type:
                where += f" AND source_type = '{self._escape(source_type)}'"
            rows = self._search_table(
                table=self._knowledge,
                query=query,
                where=where,
                limit=limit or self.knowledge_top_k,
            )
            return [self._knowledge_hit(row) for row in rows]

    async def clear_chat(self, chat_id: str) -> None:
        async with self._lock:
            self._clear_chat_locked(chat_id)

    def _ensure_table(self, name: str, schema: pa.Schema, fts_fields: list[str] | None):
        tables = set(self._db.table_names())
        if name in tables:
            table = self._db.open_table(name)
        else:
            table = self._db.create_table(name, schema=schema, mode="create")

        if fts_fields:
            try:
                table.create_fts_index(fts_fields, replace=False)
            except Exception as exc:
                logger.debug("Skipping FTS index creation for {}: {}", name, exc)
        return table

    def _search_table(self, table, query: str, where: str, limit: int) -> list[dict]:
        search_limit = max(limit, 1)
        try:
            return table.search(query, query_type="fts").where(where, prefilter=True).limit(search_limit).to_list()
        except Exception as exc:
            logger.debug("FTS search failed, falling back to lexical scan: {}", exc)
            rows = [row for row in table.to_arrow().to_pylist() if self._matches_where(row, where)]
            scored = []
            for row in rows:
                haystack = " ".join(
                    str(row.get(key, "")) for key in ("title", "query", "content", "url") if row.get(key)
                )
                score = self._lexical_score(query, haystack)
                if score > 0:
                    row["_score"] = score
                    scored.append(row)
            scored.sort(key=lambda item: item.get("_score", 0), reverse=True)
            return scored[:search_limit]

    def _build_history_rows(
        self,
        chat_id: str,
        role: str,
        content: str,
        tool_name: str | None,
        created_at: float,
    ) -> list[dict]:
        rows = []
        for chunk_index, chunk in enumerate(self._chunk_text(content)):
            rows.append(
                {
                    "id": self._make_id("history", chat_id, role, tool_name or "", created_at, chunk_index, chunk),
                    "chat_id": chat_id,
                    "role": role,
                    "tool_name": tool_name,
                    "content": chunk,
                    "chunk_index": chunk_index,
                    "created_at": created_at,
                }
            )
        return rows

    def _build_knowledge_rows(
        self,
        chat_id: str,
        tool_name: str,
        tool_args: dict,
        result: str,
        created_at: float,
    ) -> list[dict]:
        if tool_name == "web_search":
            return self._build_web_search_rows(chat_id, tool_args, result, created_at)
        if tool_name == "web_fetch":
            return self._build_web_fetch_rows(chat_id, tool_args, result, created_at)
        return []

    def _build_knowledge_rows_from_message(
        self,
        chat_id: str,
        message: StoredMessage,
        created_at: float,
    ) -> list[dict]:
        tool_name = message.tool_name or self._guess_tool_name(message.content)
        if tool_name == "web_search":
            return self._build_web_search_rows(chat_id, {}, message.content, created_at)
        if tool_name == "web_fetch":
            return self._build_web_fetch_rows(chat_id, {}, message.content, created_at)
        return []

    def _build_web_search_rows(self, chat_id: str, tool_args: dict, result: str, created_at: float) -> list[dict]:
        query = str(tool_args.get("query", "") or "").strip()
        parsed_query, items = self._parse_web_search_results(result)
        query = query or parsed_query
        if not items:
            items = [{"title": f"Search: {query or 'unknown'}", "url": "", "content": result}]

        rows = []
        for item_index, item in enumerate(items):
            title = item.get("title", "")
            url = item.get("url", "")
            for chunk_index, chunk in enumerate(self._chunk_text(item.get("content", "") or result)):
                rows.append(
                    {
                        "id": self._make_id("knowledge", chat_id, "web_search", query, title, url, item_index, chunk_index, chunk),
                        "chat_id": chat_id,
                        "source_type": "web_search",
                        "tool_name": "web_search",
                        "query": query,
                        "title": title,
                        "url": url,
                        "content": chunk,
                        "chunk_index": chunk_index,
                        "created_at": created_at,
                    }
                )
        return rows

    def _build_web_fetch_rows(self, chat_id: str, tool_args: dict, result: str, created_at: float) -> list[dict]:
        query = str(tool_args.get("url", "") or "")
        title = ""
        url = query
        content = result
        try:
            payload = json.loads(result)
            if isinstance(payload, dict):
                title = str(payload.get("title", "") or "")
                url = str(payload.get("finalUrl", payload.get("url", query)) or query)
                content = str(payload.get("text", result) or result)
        except Exception:
            pass

        rows = []
        for chunk_index, chunk in enumerate(self._chunk_text(content)):
            rows.append(
                {
                    "id": self._make_id("knowledge", chat_id, "web_fetch", title, url, created_at, chunk_index, chunk),
                    "chat_id": chat_id,
                    "source_type": "web_fetch",
                    "tool_name": "web_fetch",
                    "query": query,
                    "title": title,
                    "url": url,
                    "content": chunk,
                    "chunk_index": chunk_index,
                    "created_at": created_at,
                }
            )
        return rows

    def _add_history_rows(self, rows: list[dict]) -> None:
        if rows:
            self._history.add(rows)

    def _add_knowledge_rows(self, rows: list[dict]) -> None:
        if rows:
            self._knowledge.add(rows)

    def _get_history_count(self, chat_id: str) -> int:
        for row in self._state.to_arrow().to_pylist():
            if row.get("chat_id") == chat_id:
                return int(row.get("history_count", 0) or 0)
        return 0

    def _clear_chat_locked(self, chat_id: str) -> None:
        clause = f"chat_id = '{self._escape(chat_id)}'"
        self._history.delete(clause)
        self._knowledge.delete(clause)
        self._state.delete(clause)

    def _set_history_count(self, chat_id: str, history_count: int) -> None:
        clause = f"chat_id = '{self._escape(chat_id)}'"
        self._state.delete(clause)
        self._state.add([{"chat_id": chat_id, "history_count": int(history_count)}])

    def _history_hit(self, row: dict) -> SearchHit:
        return SearchHit(
            id=row.get("id", ""),
            chat_id=row.get("chat_id", ""),
            source_type="history",
            content=row.get("content", ""),
            created_at=float(row.get("created_at", 0) or 0),
            score=row.get("_score"),
            role=row.get("role"),
            tool_name=row.get("tool_name"),
        )

    def _knowledge_hit(self, row: dict) -> SearchHit:
        return SearchHit(
            id=row.get("id", ""),
            chat_id=row.get("chat_id", ""),
            source_type=row.get("source_type", "knowledge"),
            content=row.get("content", ""),
            created_at=float(row.get("created_at", 0) or 0),
            score=row.get("_score"),
            tool_name=row.get("tool_name"),
            title=row.get("title"),
            url=row.get("url"),
            query=row.get("query"),
        )

    def _matches_where(self, row: dict, where: str) -> bool:
        checks = [part.strip() for part in where.split("AND")]
        for check in checks:
            if "=" not in check:
                continue
            field, value = check.split("=", 1)
            field = field.strip()
            value = value.strip().strip("'")
            if str(row.get(field, "")) != value:
                return False
        return True

    def _lexical_score(self, query: str, content: str) -> float:
        query_tokens = {token for token in re.findall(r"\w+", query.lower()) if len(token) > 1}
        if not query_tokens:
            return 0.0
        content_tokens = re.findall(r"\w+", content.lower())
        if not content_tokens:
            return 0.0
        counts = {token: content_tokens.count(token) for token in query_tokens}
        return sum(counts.values()) / max(len(content_tokens), 1)

    def _chunk_text(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return []
        if len(normalized) <= self.chunk_size:
            return [normalized]

        chunks = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + self.chunk_size)
            chunks.append(normalized[start:end].strip())
            if end >= len(normalized):
                break
            start = max(end - self.chunk_overlap, start + 1)
        return [chunk for chunk in chunks if chunk]

    def _make_id(self, *parts: object) -> str:
        raw = "|".join(str(part) for part in parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _escape(self, value: str) -> str:
        return value.replace("'", "''")

    def _guess_tool_name(self, content: str) -> str | None:
        stripped = content.strip()
        if stripped.startswith("Results for:"):
            return "web_search"
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except Exception:
                return None
            if isinstance(payload, dict) and ("url" in payload or "finalUrl" in payload) and "text" in payload:
                return "web_fetch"
        return None

    def _parse_web_search_results(self, result: str) -> tuple[str, list[dict]]:
        query = ""
        items = []
        current: dict | None = None
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
