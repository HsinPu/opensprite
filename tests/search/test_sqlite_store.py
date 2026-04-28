import asyncio
import json
import sqlite3

from opensprite.search.sqlite_store import SQLiteSearchStore
from opensprite.storage.base import StoredMessage
from opensprite.storage.sqlite import SQLiteStorage


class FakeEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-embedding"
    batch_size = 8

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    async def embed_texts(self, texts):
        return [list(self.vectors.get(text, [0.0, 0.0])) for text in texts]


class BlockingEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-embedding"
    batch_size = 8

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def embed_texts(self, texts):
        self.started.set()
        await self.release.wait()
        return [[1.0, 0.0] for _ in texts]


class FailingThenPassingEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-embedding"
    batch_size = 8

    def __init__(self):
        self.should_fail = True

    async def embed_texts(self, texts):
        if self.should_fail:
            raise RuntimeError("temporary embedding failure")
        return [[1.0, 0.0] for _ in texts]


def test_sqlite_search_store_indexes_and_filters_history_and_knowledge(tmp_path):
    db_path = tmp_path / "search.db"

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(db_path, history_top_k=5, knowledge_top_k=5)

        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="Please keep sqlite fts docs handy", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="Please keep sqlite fts docs handy",
            created_at=10.0,
        )

        await storage.add_message(
            "chat-b",
            StoredMessage(role="user", content="Need postgres vector docs", timestamp=10.0),
        )
        await search.index_message(
            "chat-b",
            role="user",
            content="Need postgres vector docs",
            created_at=10.0,
        )

        await search.index_tool_result(
            "chat-a",
            tool_name="web_search",
            tool_args={"query": "sqlite fts5"},
            result=json.dumps(
                {
                    "type": "web_search",
                    "query": "sqlite fts5",
                    "url": "",
                    "final_url": "",
                    "title": "",
                    "content": "",
                    "summary": "Search results for: sqlite fts5",
                    "provider": "duckduckgo",
                    "extractor": "search",
                    "status": None,
                    "content_type": "application/json",
                    "items": [
                        {
                            "title": "SQLite FTS5",
                            "url": "https://sqlite.org/fts5.html",
                            "content": "Official full text search docs",
                        }
                    ],
                }
            ),
            created_at=11.0,
        )
        await search.index_tool_result(
            "chat-a",
            tool_name="web_fetch",
            tool_args={"url": "https://sqlite.org/fts5.html"},
            result=json.dumps(
                {
                    "type": "web_fetch",
                    "query": "https://sqlite.org/fts5.html",
                    "title": "SQLite FTS5",
                    "url": "https://sqlite.org/fts5.html",
                    "final_url": "https://sqlite.org/fts5.html",
                    "content": "SQLite FTS5 supports full text search docs and examples.",
                    "summary": "SQLite FTS5",
                    "provider": "web_fetch",
                    "extractor": "trafilatura",
                    "status": 200,
                    "content_type": "text/html",
                    "truncated": False,
                    "items": [],
                }
            ),
            created_at=12.0,
        )

        history_hits = await search.search_history("chat-a", "sqlite docs")
        other_chat_hits = await search.search_history("chat-b", "sqlite docs")
        knowledge_hits = await search.search_knowledge("chat-a", "SQLite!! full-text docs???")
        fetch_only_hits = await search.search_knowledge("chat-a", "examples", source_type="web_fetch")
        provider_hits = await search.search_knowledge("chat-a", "full text docs", provider="duckduckgo")
        extractor_hits = await search.search_knowledge("chat-a", "examples", extractor="trafilatura")
        status_hits = await search.search_knowledge("chat-a", "examples", status=200)
        content_type_hits = await search.search_knowledge("chat-a", "examples", content_type="text/html")
        truncated_hits = await search.search_knowledge("chat-a", "examples", truncated=False)

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        metadata_rows = conn.execute(
            "SELECT source_type, provider, extractor, status, content_type, truncated FROM knowledge_sources ORDER BY id ASC"
        ).fetchall()
        conn.close()

        await search.clear_session("chat-a")
        cleared_history_hits = await search.search_history("chat-a", "sqlite docs")
        remaining_messages = await storage.get_messages("chat-a")

        return (
            history_hits,
            other_chat_hits,
            knowledge_hits,
            fetch_only_hits,
            provider_hits,
            extractor_hits,
            status_hits,
            content_type_hits,
            truncated_hits,
            cleared_history_hits,
            remaining_messages,
            metadata_rows,
        )

    (
        history_hits,
        other_chat_hits,
        knowledge_hits,
        fetch_only_hits,
        provider_hits,
        extractor_hits,
        status_hits,
        content_type_hits,
        truncated_hits,
        cleared_history_hits,
        remaining_messages,
        metadata_rows,
    ) = asyncio.run(scenario())

    assert len(history_hits) == 1
    assert history_hits[0].source_type == "history"
    assert "sqlite fts docs" in history_hits[0].content.lower()
    assert other_chat_hits == []
    assert len(knowledge_hits) == 1
    assert knowledge_hits[0].source_type == "web_fetch"
    assert len(fetch_only_hits) == 1
    assert fetch_only_hits[0].source_type == "web_fetch"
    assert knowledge_hits[0].provider == "web_fetch"
    assert [hit.extractor for hit in knowledge_hits] == ["trafilatura"]
    assert fetch_only_hits[0].status == 200
    assert fetch_only_hits[0].content_type == "text/html"
    assert fetch_only_hits[0].truncated is False
    assert [hit.provider for hit in provider_hits] == ["duckduckgo"]
    assert [hit.extractor for hit in extractor_hits] == ["trafilatura"]
    assert [hit.status for hit in status_hits] == [200]
    assert [hit.content_type for hit in content_type_hits] == ["text/html"]
    assert [hit.truncated for hit in truncated_hits] == [False]
    assert cleared_history_hits == []
    assert [message.content for message in remaining_messages] == ["Please keep sqlite fts docs handy"]
    assert metadata_rows == [
        ("web_search", "duckduckgo", "search", None, "application/json", 0),
        ("web_fetch", "web_fetch", "trafilatura", 200, "text/html", 0),
    ]


def test_sqlite_search_store_sync_backfills_existing_messages(tmp_path):
    db_path = tmp_path / "search.db"

    async def scenario():
        storage = SQLiteStorage(db_path)
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="Please keep sqlite docs handy", timestamp=10.0),
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(
                role="tool",
                content=json.dumps(
                    {
                        "type": "web_search",
                        "query": "sqlite fts5",
                        "url": "",
                        "final_url": "",
                        "title": "",
                        "content": "",
                        "summary": "Search results for: sqlite fts5",
                        "provider": "duckduckgo",
                        "extractor": "search",
                        "status": None,
                        "content_type": "application/json",
                        "items": [
                            {
                                "title": "SQLite FTS5",
                                "url": "https://sqlite.org/fts5.html",
                                "content": "Official full text search docs",
                            }
                        ],
                    }
                ),
                timestamp=11.0,
                tool_name="web_search",
            ),
        )

        search = SQLiteSearchStore(db_path, history_top_k=5, knowledge_top_k=5)
        await search.sync_from_storage(storage)

        history_hits = await search.search_history("chat-a", "sqlite handy")
        knowledge_hits = await search.search_knowledge("chat-a", "official full text docs")

        return history_hits, knowledge_hits

    history_hits, knowledge_hits = asyncio.run(scenario())

    assert history_hits
    assert history_hits[0].source_type == "history"
    assert knowledge_hits
    assert knowledge_hits[0].source_type == "web_search"


def test_sqlite_search_store_sync_rebuilds_when_signature_is_stale(tmp_path):
    db_path = tmp_path / "search.db"

    async def seed():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(db_path, history_top_k=5, knowledge_top_k=5)
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="Please keep sqlite docs handy", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="Please keep sqlite docs handy",
            created_at=10.0,
        )
        return storage, search

    storage, search = asyncio.run(seed())

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE search_metadata SET value = ? WHERE key = ?",
        ("v0:chunk=10:2", "index_signature"),
    )
    conn.commit()
    conn.close()

    asyncio.run(search.sync_from_storage(storage))

    conn = sqlite3.connect(str(db_path))
    signature = conn.execute(
        "SELECT value FROM search_metadata WHERE key = ?",
        ("index_signature",),
    ).fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM search_chunks").fetchone()[0]
    conn.close()

    assert signature == search._index_signature
    assert chunk_count >= 1


def test_sqlite_search_store_persists_embeddings_and_reranks_candidates(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider(
        {
            "sqlite guide basics": [1.0, 0.0],
            "postgres guide basics": [0.0, 1.0],
            "guide": [1.0, 0.0],
        }
    )

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )

        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="sqlite guide basics", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="sqlite guide basics",
            created_at=10.0,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="postgres guide basics", timestamp=20.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="postgres guide basics",
            created_at=20.0,
        )

        await search.wait_for_embedding_idle()

        hits = await search.search_history("chat-a", "guide", limit=2)

        conn = sqlite3.connect(str(db_path))
        embedding_rows = conn.execute(
            "SELECT embedding_provider, embedding_model, embedding_dim, embedding_status FROM chunk_embeddings ORDER BY chunk_id ASC"
        ).fetchall()
        conn.close()
        return hits, embedding_rows

    hits, embedding_rows = asyncio.run(scenario())

    assert [hit.content for hit in hits] == ["sqlite guide basics", "postgres guide basics"]
    assert embedding_rows == [
        ("fake", "fake-embedding", 2, "completed"),
        ("fake", "fake-embedding", 2, "completed"),
    ]


def test_sqlite_search_store_can_use_vector_candidate_strategy(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider(
        {
            "orchard manual": [1.0, 0.0],
            "kitchen recipe": [0.0, 1.0],
            "guide": [1.0, 0.0],
        }
    )

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
            embedding_candidate_strategy="vector",
            vector_candidate_count=10,
        )

        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="orchard manual", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="orchard manual",
            created_at=10.0,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="kitchen recipe", timestamp=20.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="kitchen recipe",
            created_at=20.0,
        )
        await search.wait_for_embedding_idle()

        return await search.search_history("chat-a", "guide", limit=1)

    hits = asyncio.run(scenario())

    assert [hit.content for hit in hits] == ["orchard manual"]


def test_sqlite_search_store_falls_back_to_exact_when_sqlite_vec_is_unavailable(tmp_path, monkeypatch):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider(
        {
            "orchard manual": [1.0, 0.0],
            "kitchen recipe": [0.0, 1.0],
            "guide": [1.0, 0.0],
        }
    )

    monkeypatch.setattr("opensprite.search.sqlite_store.load_sqlite_vec_extension", lambda conn: (False, "missing"))

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
            embedding_candidate_strategy="vector",
            vector_backend="sqlite_vec",
            vector_candidate_count=10,
        )

        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="orchard manual", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="orchard manual",
            created_at=10.0,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="kitchen recipe", timestamp=20.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="kitchen recipe",
            created_at=20.0,
        )
        await search.wait_for_embedding_idle()

        hits = await search.search_history("chat-a", "guide", limit=1)
        status = await search.get_status()
        return hits, status

    hits, status = asyncio.run(scenario())

    assert [hit.content for hit in hits] == ["orchard manual"]
    assert status["vector_backend_requested"] == "sqlite_vec"
    assert status["vector_backend_effective"] == "exact"


def test_sqlite_search_store_uses_sqlite_vec_dispatch_when_available(tmp_path, monkeypatch):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider({"guide": [1.0, 0.0]})
    calls: list[str] = []

    monkeypatch.setattr("opensprite.search.sqlite_store.load_sqlite_vec_extension", lambda conn: (True, None))

    async def fake_sqlite_vec_candidate_rows(self, conn, **kwargs):
        calls.append("sqlite_vec")
        return [
            {
                "id": 1,
                "owner_id": 1,
                "session_id": "chat-a",
                "source_type": "history",
                "content": "orchard manual",
                "created_at": 1.0,
                "role": "user",
                "tool_name": None,
                "title": None,
                "url": None,
                "query": None,
                "summary": None,
                "provider": None,
                "extractor": None,
                "status": None,
                "content_type": None,
                "truncated": None,
                "embedding_similarity": 0.95,
            }
        ]

    async def fake_exact_vector_candidate_rows(self, conn, **kwargs):
        calls.append("exact")
        return []

    monkeypatch.setattr(SQLiteSearchStore, "_sqlite_vec_candidate_rows", fake_sqlite_vec_candidate_rows)
    monkeypatch.setattr(SQLiteSearchStore, "_exact_vector_candidate_rows", fake_exact_vector_candidate_rows)

    async def scenario():
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
            embedding_candidate_strategy="vector",
            vector_backend="sqlite_vec",
            vector_candidate_count=10,
        )
        hits = await search.search_history("chat-a", "guide", limit=1)
        status = await search.get_status()
        return hits, status

    hits, status = asyncio.run(scenario())

    assert [hit.content for hit in hits] == ["orchard manual"]
    assert calls == ["sqlite_vec"]
    assert status["vector_backend_requested"] == "sqlite_vec"
    assert status["vector_backend_effective"] == "sqlite_vec"


def test_sqlite_search_store_processes_embeddings_in_background(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = BlockingEmbeddingProvider()

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )

        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="background embedding", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="background embedding",
            created_at=10.0,
        )

        await asyncio.wait_for(embedder.started.wait(), timeout=1.0)
        status_during = await search.get_status()

        embedder.release.set()
        status_after = await search.wait_for_embedding_idle()
        return status_during, status_after

    status_during, status_after = asyncio.run(scenario())

    assert status_during["processing"] == 1
    assert status_after["completed"] == 1
    assert status_after["pending"] == 0


def test_sqlite_search_store_can_retry_failed_embeddings(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FailingThenPassingEmbeddingProvider()

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )

        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="retry embeddings", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="retry embeddings",
            created_at=10.0,
        )

        failed_status = await search.wait_for_embedding_idle()
        embedder.should_fail = False
        retried_status = await search.retry_failed_embeddings(session_id="chat-a", wait=True)

        conn = sqlite3.connect(str(db_path))
        embedding_rows = conn.execute(
            "SELECT embedding_status, embedding_dim FROM chunk_embeddings ORDER BY chunk_id ASC"
        ).fetchall()
        conn.close()
        return failed_status, retried_status, embedding_rows

    failed_status, retried_status, embedding_rows = asyncio.run(scenario())

    assert failed_status["failed"] == 1
    assert retried_status["retried"] == 1
    assert retried_status["failed"] == 0
    assert retried_status["completed"] == 1
    assert embedding_rows == [("completed", 2)]


def test_sqlite_search_store_requeues_processing_embeddings_on_startup(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider({"startup recovery": [1.0, 0.0]})

    async def seed():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="startup recovery", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="startup recovery",
            created_at=10.0,
        )
        await search.wait_for_embedding_idle()
        return storage

    storage = asyncio.run(seed())

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE chunk_embeddings SET embedding_status = 'processing', embedded_at = NULL"
    )
    conn.commit()
    conn.close()

    async def recover():
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await search.sync_from_storage(storage)
        return await search.wait_for_embedding_idle()

    status = asyncio.run(recover())

    assert status["processing"] == 0
    assert status["pending"] == 0
    assert status["completed"] == 1


def test_sqlite_search_store_can_retry_failed_embeddings_on_startup(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FailingThenPassingEmbeddingProvider()

    async def seed():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="startup failed retry", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="startup failed retry",
            created_at=10.0,
        )
        failed_status = await search.wait_for_embedding_idle()
        return storage, failed_status

    storage, failed_status = asyncio.run(seed())
    assert failed_status["failed"] == 1

    embedder.should_fail = False

    async def recover():
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
            retry_failed_on_startup=True,
        )
        await search.sync_from_storage(storage)
        return await search.wait_for_embedding_idle()

    status = asyncio.run(recover())

    assert status["failed"] == 0
    assert status["completed"] == 1


def test_sqlite_search_store_refreshes_missing_and_stale_embeddings(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider({"refresh stale vector": [1.0, 0.0]})

    async def seed():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="refresh stale vector", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="refresh stale vector",
            created_at=10.0,
        )
        await search.wait_for_embedding_idle()
        return search

    search = asyncio.run(seed())

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE chunk_embeddings SET embedding_provider = 'legacy', embedding_model = 'legacy-model', embedding_status = 'completed', embedded_at = NULL"
    )
    conn.commit()
    conn.close()

    refreshed_status = asyncio.run(search.refresh_embeddings(force=False, wait=True))

    conn = sqlite3.connect(str(db_path))
    embedding_rows = conn.execute(
        "SELECT embedding_provider, embedding_model, embedding_status, embedding_dim FROM chunk_embeddings ORDER BY chunk_id ASC"
    ).fetchall()
    conn.close()

    assert refreshed_status["refreshed"] == 1
    assert refreshed_status["stale"] == 0
    assert refreshed_status["completed"] == 1
    assert embedding_rows == [("fake", "fake-embedding", "completed", 2)]


def test_sqlite_search_store_status_reports_missing_embeddings(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider({"missing embedding row": [1.0, 0.0]})

    async def seed():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="missing embedding row", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="missing embedding row",
            created_at=10.0,
        )
        await search.wait_for_embedding_idle()
        return search

    search = asyncio.run(seed())

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM chunk_embeddings")
    conn.commit()
    conn.close()

    status = asyncio.run(search.get_status())

    assert status["embedding_total"] == 0
    assert status["missing"] == 1
    assert status["stale"] == 0


def test_sqlite_search_store_sync_refreshes_stale_embeddings(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider({"sync stale vector": [1.0, 0.0]})

    async def seed():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="sync stale vector", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="sync stale vector",
            created_at=10.0,
        )
        await search.wait_for_embedding_idle()
        return storage

    storage = asyncio.run(seed())

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE chunk_embeddings SET embedding_provider = 'legacy', embedding_model = 'legacy-model'"
    )
    conn.commit()
    conn.close()

    async def recover():
        search = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await search.sync_from_storage(storage)
        return await search.wait_for_embedding_idle()

    status = asyncio.run(recover())

    assert status["stale"] == 0
    assert status["completed"] == 1


def test_sqlite_search_store_run_queue_records_last_run_metadata(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider({"queue run metadata": [1.0, 0.0]})

    async def scenario():
        storage = SQLiteStorage(db_path)
        indexing_store = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
        )
        worker_store = SQLiteSearchStore(
            db_path,
            history_top_k=2,
            knowledge_top_k=2,
            embedding_provider=embedder,
            hybrid_candidate_count=4,
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="queue run metadata", timestamp=10.0),
        )
        await indexing_store.index_message(
            "chat-a",
            role="user",
            content="queue run metadata",
            created_at=10.0,
        )

        status = await worker_store.run_queue(once=True)
        final_status = await worker_store.get_status()
        return status, final_status

    status, final_status = asyncio.run(scenario())

    assert status["processed_chunks"] == 1
    assert final_status["worker_running"] is False
    assert final_status["last_run_mode"] == "once"
    assert final_status["last_run_processed"] == 1
    assert final_status["last_run_finished_at"] is not None


def test_sqlite_search_store_run_queue_rejects_active_worker_lease(tmp_path):
    db_path = tmp_path / "search.db"
    embedder = FakeEmbeddingProvider({})

    search = SQLiteSearchStore(
        db_path,
        history_top_k=2,
        knowledge_top_k=2,
        embedding_provider=embedder,
        hybrid_candidate_count=4,
    )

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO search_metadata (key, value, updated_at) VALUES (?, ?, ?)",
        (
            "embedding_worker_lock",
            json.dumps({"owner": "other-worker", "expires_at": 9999999999.0}),
            9999999999.0,
        ),
    )
    conn.commit()
    conn.close()

    async def scenario():
        try:
            await search.run_queue(once=True)
        except RuntimeError as exc:
            return str(exc)
        return ""

    error = asyncio.run(scenario())

    assert "already running" in error
