"""SQLite-backed per-chat search store using FTS5."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from .base import SearchHit, SearchStore
from .indexing import build_history_chunks, build_knowledge_documents
from ..storage.base import StorageProvider
from ..storage.sqlite import (
    ensure_sqlite_schema,
    find_message_owner_id,
    insert_knowledge_document,
    insert_search_chunks,
    open_sqlite_connection,
)


class SQLiteSearchStore(SearchStore):
    """Per-chat searchable history and knowledge index backed by SQLite."""

    def __init__(
        self,
        path: str | Path,
        history_top_k: int = 5,
        knowledge_top_k: int = 5,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
    ):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history_top_k = history_top_k
        self.knowledge_top_k = knowledge_top_k
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._lock = asyncio.Lock()
        conn = self._get_conn()
        try:
            ensure_sqlite_schema(conn)
        finally:
            conn.close()

    def _get_conn(self):
        return open_sqlite_connection(self.path)

    async def sync_from_storage(self, storage: StorageProvider) -> None:
        """The shared SQLite database no longer needs out-of-band backfills."""
        return None

    async def index_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        created_at: float | None = None,
    ) -> None:
        current_created_at = created_at or time.time()
        chunks = build_history_chunks(
            role=role,
            content=content,
            tool_name=tool_name,
            created_at=current_created_at,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        if not chunks:
            return

        async with self._lock:
            conn = self._get_conn()
            try:
                owner_id = find_message_owner_id(
                    conn,
                    chat_id=chat_id,
                    role=role,
                    content=content,
                    tool_name=tool_name,
                    created_at=current_created_at,
                )
                insert_search_chunks(
                    conn,
                    chat_id=chat_id,
                    owner_type="message",
                    owner_id=owner_id,
                    chunks=chunks,
                )
                conn.commit()
            finally:
                conn.close()

    async def index_tool_result(
        self,
        chat_id: str,
        tool_name: str,
        tool_args: dict,
        result: str,
        created_at: float | None = None,
    ) -> None:
        documents = build_knowledge_documents(
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        if not documents:
            return

        current_created_at = created_at or time.time()
        async with self._lock:
            conn = self._get_conn()
            try:
                for document in documents:
                    insert_knowledge_document(
                        conn,
                        chat_id=chat_id,
                        document=document,
                        created_at=current_created_at,
                    )
                conn.commit()
            finally:
                conn.close()

    async def search_history(self, chat_id: str, query: str, limit: int = 5) -> list[SearchHit]:
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = self._search_rows(
                    conn,
                    chat_id=chat_id,
                    query=query,
                    owner_type="message",
                    limit=limit or self.history_top_k,
                )
                return [self._row_to_hit(row) for row in rows]
            finally:
                conn.close()

    async def search_knowledge(
        self,
        chat_id: str,
        query: str,
        limit: int = 5,
        source_type: str | None = None,
    ) -> list[SearchHit]:
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = self._search_rows(
                    conn,
                    chat_id=chat_id,
                    query=query,
                    owner_type="knowledge",
                    limit=limit or self.knowledge_top_k,
                    source_type=source_type,
                )
                return [self._row_to_hit(row) for row in rows]
            finally:
                conn.close()

    async def clear_chat(self, chat_id: str) -> None:
        async with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM search_chunks WHERE chat_id = ?", (chat_id,))
                conn.execute("DELETE FROM knowledge_sources WHERE chat_id = ?", (chat_id,))
                conn.commit()
            finally:
                conn.close()

    def _search_rows(
        self,
        conn,
        *,
        chat_id: str,
        query: str,
        owner_type: str,
        limit: int,
        source_type: str | None = None,
    ):
        match_query = self._compile_match_query(query)
        if match_query is None:
            return self._search_rows_fallback(
                conn,
                chat_id=chat_id,
                query=query,
                owner_type=owner_type,
                limit=limit,
                source_type=source_type,
            )

        sql = """
            SELECT
                c.id,
                c.chat_id,
                c.source_type,
                c.content,
                c.created_at,
                c.role,
                c.tool_name,
                c.title,
                c.url,
                c.query,
                bm25(search_chunks_fts) AS score
            FROM search_chunks_fts
            JOIN search_chunks c ON c.id = search_chunks_fts.rowid
            WHERE search_chunks_fts MATCH ?
              AND c.chat_id = ?
              AND c.owner_type = ?
        """
        params: list[object] = [match_query, chat_id, owner_type]
        if source_type:
            sql += " AND c.source_type = ?"
            params.append(source_type)
        sql += " ORDER BY score ASC, c.created_at DESC, c.id DESC LIMIT ?"
        params.append(max(limit, 1))

        try:
            return conn.execute(sql, params).fetchall()
        except Exception:
            return self._search_rows_fallback(
                conn,
                chat_id=chat_id,
                query=query,
                owner_type=owner_type,
                limit=limit,
                source_type=source_type,
            )

    def _search_rows_fallback(
        self,
        conn,
        *,
        chat_id: str,
        query: str,
        owner_type: str,
        limit: int,
        source_type: str | None = None,
    ):
        sql = """
            SELECT id, chat_id, source_type, content, created_at, role, tool_name, title, url, query
            FROM search_chunks
            WHERE chat_id = ?
              AND owner_type = ?
        """
        params: list[object] = [chat_id, owner_type]
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        sql += " ORDER BY created_at DESC, id DESC"

        rows = conn.execute(sql, params).fetchall()
        scored = []
        for row in rows:
            haystack = " ".join(
                str(row[key] or "")
                for key in ("title", "query", "content", "url")
            )
            score = self._lexical_score(query, haystack)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], float(item[1]["created_at"] or 0), int(item[1]["id"])), reverse=True)
        return [self._row_with_score(row, score) for score, row in scored[: max(limit, 1)]]

    @staticmethod
    def _compile_match_query(query: str) -> str | None:
        tokens = []
        for token in re.findall(r"\w+", query.lower()):
            cleaned = token.strip()
            if cleaned:
                tokens.append(cleaned)
        if not tokens:
            return None
        return " AND ".join(f'"{token}"' for token in tokens)

    @staticmethod
    def _lexical_score(query: str, content: str) -> float:
        query_tokens = {token for token in re.findall(r"\w+", query.lower()) if len(token) > 1}
        if not query_tokens:
            normalized_query = query.strip().lower()
            return 1.0 if normalized_query and normalized_query in content.lower() else 0.0
        content_tokens = re.findall(r"\w+", content.lower())
        if not content_tokens:
            return 0.0
        counts = {token: content_tokens.count(token) for token in query_tokens}
        return sum(counts.values()) / max(len(content_tokens), 1)

    @staticmethod
    def _row_with_score(row, score: float):
        payload = dict(row)
        payload["score"] = score
        return payload

    @staticmethod
    def _row_to_hit(row) -> SearchHit:
        score = row["score"] if isinstance(row, dict) else row["score"]
        return SearchHit(
            id=str(row["id"]),
            chat_id=str(row["chat_id"]),
            source_type=str(row["source_type"]),
            content=str(row["content"]),
            created_at=float(row["created_at"] or 0),
            score=float(score) if score is not None else None,
            role=row["role"],
            tool_name=row["tool_name"],
            title=row["title"],
            url=row["url"],
            query=row["query"],
        )
