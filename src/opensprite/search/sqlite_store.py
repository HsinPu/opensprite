"""SQLite-backed per-chat search store using FTS5."""

from __future__ import annotations

import asyncio
import math
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from .base import SearchHit, SearchStore
from .embeddings import EmbeddingProvider
from .indexing import build_history_chunks, build_knowledge_documents, build_knowledge_documents_from_message
from ..storage.base import StorageProvider, StoredMessage
from ..storage.sqlite import (
    ensure_sqlite_schema,
    find_message_owner_id,
    insert_knowledge_document,
    insert_search_chunks,
    open_sqlite_connection,
    unpack_embedding,
    upsert_chunk_embedding,
)
from ..utils.log import logger

SEARCH_INDEX_VERSION = 2
SEARCH_INDEX_SIGNATURE_KEY = "index_signature"


class SQLiteSearchStore(SearchStore):
    """Per-chat searchable history and knowledge index backed by SQLite."""

    def __init__(
        self,
        path: str | Path,
        history_top_k: int = 5,
        knowledge_top_k: int = 5,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
        embedding_provider: EmbeddingProvider | None = None,
        hybrid_candidate_count: int = 20,
        retry_failed_on_startup: bool = False,
    ):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history_top_k = history_top_k
        self.knowledge_top_k = knowledge_top_k
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_provider = embedding_provider
        self.hybrid_candidate_count = max(hybrid_candidate_count, max(history_top_k, knowledge_top_k))
        self.retry_failed_on_startup = retry_failed_on_startup
        self._lock = asyncio.Lock()
        self._embedding_task: asyncio.Task | None = None
        conn = self._get_conn()
        try:
            ensure_sqlite_schema(conn)
        finally:
            conn.close()

    def _get_conn(self):
        return open_sqlite_connection(self.path)

    @property
    def _index_signature(self) -> str:
        """Return the current signature for the SQLite search index layout."""
        embedding_signature = "disabled"
        if self.embedding_provider is not None:
            embedding_signature = f"{self.embedding_provider.provider_name}:{self.embedding_provider.model_name}"
        return f"v{SEARCH_INDEX_VERSION}:chunk={self.chunk_size}:{self.chunk_overlap}:embed={embedding_signature}"

    def _read_index_signature(self, conn) -> str | None:
        """Read the persisted search index signature, if any."""
        row = conn.execute(
            "SELECT value FROM search_metadata WHERE key = ?",
            (SEARCH_INDEX_SIGNATURE_KEY,),
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def _write_index_signature(self, conn) -> None:
        """Persist the current search index signature."""
        conn.execute(
            """
            INSERT INTO search_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (SEARCH_INDEX_SIGNATURE_KEY, self._index_signature, time.time()),
        )

    def _candidate_limit(self, requested_limit: int) -> int:
        """Expand the search candidate pool when embeddings are enabled."""
        if self.embedding_provider is None:
            return max(requested_limit, 1)
        return max(requested_limit, self.hybrid_candidate_count)

    def _schedule_pending_embeddings(self) -> None:
        """Start the background embedding worker when there is an active event loop."""
        if self.embedding_provider is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._embedding_task is not None and not self._embedding_task.done():
            return
        self._embedding_task = loop.create_task(self.process_pending_embeddings())
        self._embedding_task.add_done_callback(self._clear_embedding_task)

    def _clear_embedding_task(self, task: asyncio.Task) -> None:
        """Reset the cached background task when it finishes."""
        if self._embedding_task is task:
            self._embedding_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            logger.info("search.embed | background worker cancelled")
        except Exception as exc:
            logger.warning("search.embed | background worker failed: {}", exc)

    def _queue_chunk_embeddings(self, conn, chunk_ids: list[int]) -> None:
        """Mark new chunk rows as pending embedding work."""
        if self.embedding_provider is None or not chunk_ids:
            return
        for chunk_id in chunk_ids:
            upsert_chunk_embedding(
                conn,
                chunk_id=chunk_id,
                provider=self.embedding_provider.provider_name,
                model=self.embedding_provider.model_name,
                values=None,
                status="pending",
                embedded_at=None,
            )

    async def _claim_pending_embedding_batch(self) -> list[tuple[int, str]]:
        """Claim one batch of pending chunk ids for background embedding."""
        if self.embedding_provider is None:
            return []
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT ce.chunk_id, sc.content
                    FROM chunk_embeddings ce
                    JOIN search_chunks sc ON sc.id = ce.chunk_id
                    WHERE ce.embedding_status = 'pending'
                      AND ce.embedding_model = ?
                    ORDER BY ce.chunk_id ASC
                    LIMIT ?
                    """,
                    (self.embedding_provider.model_name, self.embedding_provider.batch_size),
                ).fetchall()
                if not rows:
                    return []
                chunk_ids = [int(row["chunk_id"]) for row in rows]
                conn.executemany(
                    "UPDATE chunk_embeddings SET embedding_status = 'processing' WHERE chunk_id = ? AND embedding_status = 'pending'",
                    [(chunk_id,) for chunk_id in chunk_ids],
                )
                conn.commit()
                return [(int(row["chunk_id"]), str(row["content"] or "")) for row in rows]
            finally:
                conn.close()

    async def _mark_embedding_batch(
        self,
        chunk_ids: list[int],
        *,
        vectors: list[list[float]] | None,
        status: str,
    ) -> None:
        """Persist one processed embedding batch."""
        if self.embedding_provider is None or not chunk_ids:
            return
        async with self._lock:
            conn = self._get_conn()
            try:
                payloads = vectors or [None] * len(chunk_ids)
                for chunk_id, values in zip(chunk_ids, payloads, strict=True):
                    upsert_chunk_embedding(
                        conn,
                        chunk_id=chunk_id,
                        provider=self.embedding_provider.provider_name,
                        model=self.embedding_provider.model_name,
                        values=values,
                        status=status,
                    )
                conn.commit()
            finally:
                conn.close()

    async def process_pending_embeddings(self) -> dict[str, int]:
        """Drain pending chunk embeddings and persist results."""
        if self.embedding_provider is None:
            return await self.get_status()

        current_task = asyncio.current_task()
        if self._embedding_task is not None and self._embedding_task is not current_task and not self._embedding_task.done():
            await self._embedding_task
            return await self.get_status()

        while True:
            batch = await self._claim_pending_embedding_batch()
            if not batch:
                break
            chunk_ids = [chunk_id for chunk_id, _ in batch]
            texts = [content for _, content in batch]
            try:
                vectors = await self.embedding_provider.embed_texts(texts)
            except Exception as exc:
                logger.warning("search.embed | failed to embed batch: {}", exc)
                await self._mark_embedding_batch(chunk_ids, vectors=None, status="failed")
                continue
            await self._mark_embedding_batch(chunk_ids, vectors=vectors, status="completed")

        return await self.get_status()

    async def wait_for_embedding_idle(self) -> dict[str, int]:
        """Wait for background embedding work to finish and return current status counts."""
        if self._embedding_task is not None and not self._embedding_task.done():
            await self._embedding_task
        return await self.process_pending_embeddings()

    async def _requeue_embeddings(self, *, from_status: str, chat_id: str | None = None) -> int:
        """Move matching embedding jobs back to pending."""
        if self.embedding_provider is None:
            return 0

        async with self._lock:
            conn = self._get_conn()
            try:
                params: list[object] = [self.embedding_provider.model_name]
                where_clauses = ["ce.embedding_status = ?", "ce.embedding_model = ?"]
                params.insert(0, from_status)
                if chat_id:
                    where_clauses.append("sc.chat_id = ?")
                    params.append(chat_id)
                rows = conn.execute(
                    f"""
                    SELECT ce.chunk_id
                    FROM chunk_embeddings ce
                    JOIN search_chunks sc ON sc.id = ce.chunk_id
                    WHERE {' AND '.join(where_clauses)}
                    ORDER BY ce.chunk_id ASC
                    """,
                    params,
                ).fetchall()
                chunk_ids = [int(row["chunk_id"]) for row in rows]
                if chunk_ids:
                    conn.executemany(
                        "UPDATE chunk_embeddings SET embedding_status = 'pending', embedded_at = NULL WHERE chunk_id = ?",
                        [(chunk_id,) for chunk_id in chunk_ids],
                    )
                    conn.commit()
                return len(chunk_ids)
            finally:
                conn.close()

    async def retry_failed_embeddings(
        self,
        chat_id: str | None = None,
        *,
        wait: bool = True,
    ) -> dict[str, int]:
        """Move failed embedding jobs back to pending and optionally wait for completion."""
        if self.embedding_provider is None:
            status = await self.get_status(chat_id=chat_id)
            status["retried"] = 0
            return status

        retried = await self._requeue_embeddings(from_status="failed", chat_id=chat_id)

        if retried:
            self._schedule_pending_embeddings()
            if wait:
                await self.wait_for_embedding_idle()

        filtered_status = await self.get_status(chat_id=chat_id)
        filtered_status["retried"] = retried
        return filtered_status

    async def get_status(self, chat_id: str | None = None) -> dict[str, int]:
        """Return search and embedding status counts."""
        async with self._lock:
            conn = self._get_conn()
            try:
                filters = ""
                params: list[object] = []
                if chat_id:
                    filters = " WHERE chat_id = ?"
                    params.append(chat_id)
                chats = conn.execute(
                    f"SELECT COUNT(DISTINCT chat_id) FROM search_chunks{filters}",
                    params,
                ).fetchone()[0]
                knowledge = conn.execute(
                    f"SELECT COUNT(*) FROM knowledge_sources{filters}",
                    params,
                ).fetchone()[0]
                chunks = conn.execute(
                    f"SELECT COUNT(*) FROM search_chunks{filters}",
                    params,
                ).fetchone()[0]
                messages = conn.execute(
                    f"SELECT COUNT(*) FROM messages{filters}",
                    params,
                ).fetchone()[0]

                embedding_filters = ""
                embedding_params: list[object] = []
                if chat_id:
                    embedding_filters = " WHERE sc.chat_id = ?"
                    embedding_params.append(chat_id)
                status_rows = conn.execute(
                    f"""
                    SELECT ce.embedding_status, COUNT(*) AS count
                    FROM chunk_embeddings ce
                    JOIN search_chunks sc ON sc.id = ce.chunk_id
                    {embedding_filters}
                    GROUP BY ce.embedding_status
                    """,
                    embedding_params,
                ).fetchall()
            finally:
                conn.close()

        counts = {str(row["embedding_status"]): int(row["count"] or 0) for row in status_rows}
        total_embeddings = sum(counts.values())
        return {
            "chat_count": int(chats or 0),
            "message_count": int(messages or 0),
            "knowledge_count": int(knowledge or 0),
            "chunk_count": int(chunks or 0),
            "embedding_total": int(total_embeddings),
            "queued": counts.get("pending", 0) + counts.get("processing", 0),
            "pending": counts.get("pending", 0),
            "processing": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
        }

    async def _rerank_rows(self, conn, query: str, rows, limit: int, *, owner_type: str):
        """Fuse FTS, lexical coverage, embeddings, and source preference into final ranking."""
        if not rows:
            return []

        normalized_query = self._normalize_query_text(query)
        query_tokens = self._query_tokens(normalized_query)
        ranked_rows = [dict(row) if not isinstance(row, dict) else dict(row) for row in rows]

        embedding_similarities: dict[int, float] = {}
        if self.embedding_provider is not None:
            try:
                query_vectors = await self.embedding_provider.embed_texts([normalized_query or query])
            except Exception as exc:
                logger.warning("search.embed | failed to embed query for rerank: {}", exc)
                query_vectors = []

            if query_vectors:
                query_vector = query_vectors[0]
                row_ids = [int(row["id"]) for row in ranked_rows]
                placeholders = ", ".join("?" for _ in row_ids)
                embedding_rows = conn.execute(
                    f"""
                    SELECT chunk_id, embedding, embedding_dim
                    FROM chunk_embeddings
                    WHERE chunk_id IN ({placeholders})
                      AND embedding_status = 'completed'
                    """,
                    row_ids,
                ).fetchall()
                vectors = {
                    int(row["chunk_id"]): unpack_embedding(row["embedding"], int(row["embedding_dim"] or 0))
                    for row in embedding_rows
                }
                for row in ranked_rows:
                    vector = vectors.get(int(row["id"]))
                    similarity = self._cosine_similarity(query_vector, vector) if vector else None
                    if similarity is not None:
                        embedding_similarities[int(row["id"])] = similarity

        scored_rows: list[dict] = []
        for index, row in enumerate(ranked_rows, start=1):
            fts_component = 1.0 / index
            coverage_component = self._coverage_score(query_tokens, row)
            embedding_similarity = embedding_similarities.get(int(row["id"]))
            embedding_component = ((embedding_similarity + 1.0) / 2.0) if embedding_similarity is not None else 0.0
            source_bonus = self._source_bonus(row["source_type"] if owner_type == "knowledge" else None)

            if embedding_similarity is not None:
                combined_score = (0.25 * fts_component) + (0.25 * coverage_component) + (0.45 * embedding_component) + source_bonus
            else:
                combined_score = (0.55 * fts_component) + (0.35 * coverage_component) + source_bonus

            row["score"] = combined_score
            row["embedding_similarity"] = embedding_similarity
            scored_rows.append(row)

        scored_rows.sort(
            key=lambda row: (
                float(row["score"] or 0),
                self._source_rank(str(row["source_type"] or "")) if owner_type == "knowledge" else 0,
                float(row["created_at"] or 0),
                int(row["id"]),
            ),
            reverse=True,
        )

        if owner_type == "knowledge":
            return self._dedupe_knowledge_rows(scored_rows, limit)
        return scored_rows[: max(limit, 1)]

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float | None:
        """Return cosine similarity for two vectors of the same dimension."""
        if not left or not right or len(left) != len(right):
            return None
        numerator = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0 or right_norm == 0:
            return None
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _normalize_query_text(text: str) -> str:
        """Normalize a free-form query into a whitespace-separated token string."""
        tokens = [token.strip() for token in re.findall(r"\w+", text.lower()) if token.strip()]
        if tokens:
            return " ".join(tokens)
        return " ".join(text.strip().lower().split())

    @classmethod
    def _query_tokens(cls, text: str) -> list[str]:
        """Tokenize a normalized query string."""
        normalized = cls._normalize_query_text(text)
        return [token for token in normalized.split() if token]

    @classmethod
    def _coverage_score(cls, query_tokens: list[str], row) -> float:
        """Estimate how much of the normalized query is covered by this row."""
        if not query_tokens:
            return 0.0
        haystack = " ".join(
            str(row.get(key, "") or "")
            for key in ("title", "query", "summary", "content", "url")
        )
        haystack_tokens = set(cls._query_tokens(haystack))
        if not haystack_tokens:
            return 0.0
        matched = sum(1 for token in query_tokens if token in haystack_tokens)
        return matched / max(len(query_tokens), 1)

    @staticmethod
    def _source_rank(source_type: str | None) -> int:
        """Return a stable rank for knowledge source preference."""
        if source_type == "web_fetch":
            return 2
        if source_type == "web_search":
            return 1
        return 0

    @classmethod
    def _source_bonus(cls, source_type: str | None) -> float:
        """Apply a modest bonus to higher-fidelity knowledge sources."""
        rank = cls._source_rank(source_type)
        if rank == 2:
            return 0.08
        if rank == 1:
            return 0.03
        return 0.0

    @staticmethod
    def _canonicalize_url(url: str | None) -> str | None:
        """Normalize URLs so related knowledge hits can be deduplicated."""
        if not url:
            return None
        parsed = urlsplit(url.strip())
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        if not host and not path:
            return None
        if host:
            return f"{host}{path}"
        return path

    @classmethod
    def _dedupe_knowledge_rows(cls, rows: list[dict], limit: int) -> list[dict]:
        """Deduplicate knowledge rows by canonical URL or document owner."""
        best_by_key: dict[str, dict] = {}
        for row in rows:
            canonical_url = cls._canonicalize_url(str(row.get("url") or ""))
            owner_id = int(row.get("owner_id") or row.get("id") or 0)
            dedupe_key = canonical_url or f"owner:{owner_id}"
            current = best_by_key.get(dedupe_key)
            if current is None:
                best_by_key[dedupe_key] = row
                continue
            candidate_key = (
                cls._source_rank(str(row.get("source_type") or "")),
                float(row.get("score") or 0),
                float(row.get("created_at") or 0),
                int(row.get("id") or 0),
            )
            current_key = (
                cls._source_rank(str(current.get("source_type") or "")),
                float(current.get("score") or 0),
                float(current.get("created_at") or 0),
                int(current.get("id") or 0),
            )
            if candidate_key > current_key:
                best_by_key[dedupe_key] = row

        deduped = sorted(
            best_by_key.values(),
            key=lambda row: (
                float(row.get("score") or 0),
                cls._source_rank(str(row.get("source_type") or "")),
                float(row.get("created_at") or 0),
                int(row.get("id") or 0),
            ),
            reverse=True,
        )
        return deduped[: max(limit, 1)]

    async def sync_from_storage(self, storage: StorageProvider) -> None:
        """Backfill the shared SQLite index when search is enabled after history already exists."""
        async with self._lock:
            conn = self._get_conn()
            try:
                ensure_sqlite_schema(conn)
                persisted_signature = self._read_index_signature(conn)
                indexable_message_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM messages WHERE TRIM(content) <> ''"
                    ).fetchone()[0]
                )
                indexed_message_count = int(
                    conn.execute(
                        "SELECT COUNT(DISTINCT owner_id) FROM search_chunks WHERE owner_type = 'message'"
                    ).fetchone()[0]
                )
                knowledge_source_count = int(
                    conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0]
                )
                pending_embedding_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunk_embeddings WHERE embedding_status = 'pending'"
                    ).fetchone()[0]
                )
                processing_embedding_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunk_embeddings WHERE embedding_status = 'processing'"
                    ).fetchone()[0]
                )
                failed_embedding_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunk_embeddings WHERE embedding_status = 'failed'"
                    ).fetchone()[0]
                )
                indexed_knowledge_count = int(
                    conn.execute(
                        "SELECT COUNT(DISTINCT owner_id) FROM search_chunks WHERE owner_type = 'knowledge'"
                    ).fetchone()[0]
                )
                has_tool_knowledge_candidates = bool(
                    conn.execute(
                        "SELECT 1 FROM messages WHERE tool_name IN ('web_search', 'web_fetch') LIMIT 1"
                    ).fetchone()
                )
            finally:
                conn.close()

        needs_rebuild = False
        if persisted_signature != self._index_signature:
            needs_rebuild = True
        elif indexable_message_count > indexed_message_count:
            needs_rebuild = True
        elif knowledge_source_count > indexed_knowledge_count:
            needs_rebuild = True
        elif has_tool_knowledge_candidates and knowledge_source_count == 0:
            needs_rebuild = True

        if not needs_rebuild:
            if processing_embedding_count > 0:
                requeued_processing = await self._requeue_embeddings(from_status="processing")
                if requeued_processing > 0:
                    pending_embedding_count += requeued_processing
            if self.retry_failed_on_startup and failed_embedding_count > 0:
                await self.retry_failed_embeddings(wait=False)
            if pending_embedding_count > 0:
                self._schedule_pending_embeddings()
            return None

        logger.info(
            "search.sync | rebuilding sqlite index signature={} expected={} messages={} indexed_messages={} knowledge={} indexed_knowledge={}",
            persisted_signature,
            self._index_signature,
            indexable_message_count,
            indexed_message_count,
            knowledge_source_count,
            indexed_knowledge_count,
        )
        await self.rebuild_index()
        self._schedule_pending_embeddings()
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
                chunk_ids = insert_search_chunks(
                    conn,
                    chat_id=chat_id,
                    owner_type="message",
                    owner_id=owner_id,
                    chunks=chunks,
                )
                self._queue_chunk_embeddings(conn, chunk_ids)
                self._write_index_signature(conn)
                conn.commit()
            finally:
                conn.close()
        self._schedule_pending_embeddings()

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
                    _, chunk_ids = insert_knowledge_document(
                        conn,
                        chat_id=chat_id,
                        document=document,
                        created_at=current_created_at,
                    )
                    self._queue_chunk_embeddings(conn, chunk_ids)
                self._write_index_signature(conn)
                conn.commit()
            finally:
                conn.close()
        self._schedule_pending_embeddings()

    async def search_history(self, chat_id: str, query: str, limit: int = 5) -> list[SearchHit]:
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = self._search_rows(
                    conn,
                    chat_id=chat_id,
                    query=query,
                    owner_type="message",
                    limit=self._candidate_limit(limit or self.history_top_k),
                )
                rows = await self._rerank_rows(
                    conn,
                    query,
                    rows,
                    limit or self.history_top_k,
                    owner_type="message",
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
        provider: str | None = None,
        extractor: str | None = None,
        status: int | None = None,
        content_type: str | None = None,
        truncated: bool | None = None,
    ) -> list[SearchHit]:
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = self._search_rows(
                    conn,
                    chat_id=chat_id,
                    query=query,
                    owner_type="knowledge",
                    limit=self._candidate_limit(limit or self.knowledge_top_k),
                    source_type=source_type,
                    provider=provider,
                    extractor=extractor,
                    status=status,
                    content_type=content_type,
                    truncated=truncated,
                )
                rows = await self._rerank_rows(
                    conn,
                    query,
                    rows,
                    limit or self.knowledge_top_k,
                    owner_type="knowledge",
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

    async def rebuild_index(self, chat_id: str | None = None) -> dict[str, int]:
        """Rebuild indexed history and knowledge rows from persisted messages."""
        async with self._lock:
            conn = self._get_conn()
            try:
                ensure_sqlite_schema(conn)
                conn.execute("BEGIN")
                if chat_id:
                    conn.execute("DELETE FROM search_chunks WHERE chat_id = ?", (chat_id,))
                    conn.execute("DELETE FROM knowledge_sources WHERE chat_id = ?", (chat_id,))
                    rows = conn.execute(
                        """
                        SELECT id, chat_id, role, content, tool_name, created_at
                        FROM messages
                        WHERE chat_id = ?
                        ORDER BY id ASC
                        """,
                        (chat_id,),
                    ).fetchall()
                else:
                    conn.execute("DELETE FROM search_chunks")
                    conn.execute("DELETE FROM knowledge_sources")
                    rows = conn.execute(
                        """
                        SELECT id, chat_id, role, content, tool_name, created_at
                        FROM messages
                        ORDER BY chat_id ASC, id ASC
                        """
                    ).fetchall()

                message_count = 0
                knowledge_count = 0
                chunk_count = 0
                for row in rows:
                    created_at = float(row["created_at"] or 0)
                    history_chunks = build_history_chunks(
                        role=str(row["role"]),
                        content=str(row["content"]),
                        tool_name=row["tool_name"],
                        created_at=created_at,
                        chunk_size=self.chunk_size,
                        chunk_overlap=self.chunk_overlap,
                    )
                    history_chunk_ids = insert_search_chunks(
                        conn,
                        chat_id=str(row["chat_id"]),
                        owner_type="message",
                        owner_id=int(row["id"]),
                        chunks=history_chunks,
                    )
                    self._queue_chunk_embeddings(conn, history_chunk_ids)
                    chunk_count += len(history_chunks)
                    message_count += 1

                    message = StoredMessage(
                        role=str(row["role"]),
                        content=str(row["content"]),
                        timestamp=created_at,
                        tool_name=row["tool_name"],
                    )
                    documents = build_knowledge_documents_from_message(
                        message,
                        chunk_size=self.chunk_size,
                        chunk_overlap=self.chunk_overlap,
                    )
                    for document in documents:
                        _, knowledge_chunk_ids = insert_knowledge_document(
                            conn,
                            chat_id=str(row["chat_id"]),
                            document=document,
                            created_at=created_at,
                        )
                        self._queue_chunk_embeddings(conn, knowledge_chunk_ids)
                        knowledge_count += 1
                        chunk_count += len(document.chunks)

                self._write_index_signature(conn)
                conn.commit()
                self._schedule_pending_embeddings()
                return {
                    "chat_count": len({str(row["chat_id"]) for row in rows}),
                    "message_count": message_count,
                    "knowledge_count": knowledge_count,
                    "chunk_count": chunk_count,
                }
            except Exception:
                conn.rollback()
                raise
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
        provider: str | None = None,
        extractor: str | None = None,
        status: int | None = None,
        content_type: str | None = None,
        truncated: bool | None = None,
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
                provider=provider,
                extractor=extractor,
                status=status,
                content_type=content_type,
                truncated=truncated,
            )

        sql = """
            SELECT
                c.id,
                c.owner_id,
                c.chat_id,
                c.source_type,
                c.content,
                c.created_at,
                c.role,
                c.tool_name,
                c.title,
                c.url,
                c.query,
                ks.summary,
                ks.provider,
                ks.extractor,
                ks.status,
                ks.content_type,
                ks.truncated,
                bm25(search_chunks_fts) AS score
            FROM search_chunks_fts
            JOIN search_chunks c ON c.id = search_chunks_fts.rowid
            LEFT JOIN knowledge_sources ks ON c.owner_type = 'knowledge' AND ks.id = c.owner_id
            WHERE search_chunks_fts MATCH ?
              AND c.chat_id = ?
              AND c.owner_type = ?
        """
        params: list[object] = [match_query, chat_id, owner_type]
        if source_type:
            sql += " AND c.source_type = ?"
            params.append(source_type)
        if provider:
            sql += " AND ks.provider = ?"
            params.append(provider)
        if extractor:
            sql += " AND ks.extractor = ?"
            params.append(extractor)
        if status is not None:
            sql += " AND ks.status = ?"
            params.append(status)
        if content_type:
            sql += " AND ks.content_type = ?"
            params.append(content_type)
        if truncated is not None:
            sql += " AND ks.truncated = ?"
            params.append(1 if truncated else 0)
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
                provider=provider,
                extractor=extractor,
                status=status,
                content_type=content_type,
                truncated=truncated,
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
        provider: str | None = None,
        extractor: str | None = None,
        status: int | None = None,
        content_type: str | None = None,
        truncated: bool | None = None,
    ):
        sql = """
            SELECT
                c.id,
                c.owner_id,
                c.chat_id,
                c.source_type,
                c.content,
                c.created_at,
                c.role,
                c.tool_name,
                c.title,
                c.url,
                c.query,
                ks.summary,
                ks.provider,
                ks.extractor,
                ks.status,
                ks.content_type,
                ks.truncated
            FROM search_chunks c
            LEFT JOIN knowledge_sources ks ON c.owner_type = 'knowledge' AND ks.id = c.owner_id
            WHERE c.chat_id = ?
              AND c.owner_type = ?
        """
        params: list[object] = [chat_id, owner_type]
        if source_type:
            sql += " AND c.source_type = ?"
            params.append(source_type)
        if provider:
            sql += " AND ks.provider = ?"
            params.append(provider)
        if extractor:
            sql += " AND ks.extractor = ?"
            params.append(extractor)
        if status is not None:
            sql += " AND ks.status = ?"
            params.append(status)
        if content_type:
            sql += " AND ks.content_type = ?"
            params.append(content_type)
        if truncated is not None:
            sql += " AND ks.truncated = ?"
            params.append(1 if truncated else 0)
        sql += " ORDER BY c.created_at DESC, c.id DESC"

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
        tokens = SQLiteSearchStore._query_tokens(query)
        if not tokens:
            return None
        return " AND ".join(f'"{token}"' for token in tokens)

    @staticmethod
    def _lexical_score(query: str, content: str) -> float:
        query_tokens = {token for token in SQLiteSearchStore._query_tokens(query) if len(token) > 1}
        if not query_tokens:
            normalized_query = SQLiteSearchStore._normalize_query_text(query)
            normalized_content = SQLiteSearchStore._normalize_query_text(content)
            return 1.0 if normalized_query and normalized_query in normalized_content else 0.0
        content_tokens = SQLiteSearchStore._query_tokens(content)
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
            summary=row["summary"],
            provider=row["provider"],
            extractor=row["extractor"],
            status=int(row["status"]) if row["status"] is not None else None,
            content_type=row["content_type"],
            truncated=bool(row["truncated"]) if row["truncated"] is not None else None,
        )
