import asyncio
import json
import sqlite3

from opensprite.search.sqlite_store import SQLiteSearchStore
from opensprite.storage.base import StoredMessage
from opensprite.storage.sqlite import SQLiteStorage


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
        knowledge_hits = await search.search_knowledge("chat-a", "full text docs")
        fetch_only_hits = await search.search_knowledge("chat-a", "examples", source_type="web_fetch")

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        metadata_rows = conn.execute(
            "SELECT source_type, provider, extractor, status, content_type, truncated FROM knowledge_sources ORDER BY id ASC"
        ).fetchall()
        conn.close()

        await search.clear_chat("chat-a")
        cleared_history_hits = await search.search_history("chat-a", "sqlite docs")
        remaining_messages = await storage.get_messages("chat-a")

        return (
            history_hits,
            other_chat_hits,
            knowledge_hits,
            fetch_only_hits,
            cleared_history_hits,
            remaining_messages,
            metadata_rows,
        )

    (
        history_hits,
        other_chat_hits,
        knowledge_hits,
        fetch_only_hits,
        cleared_history_hits,
        remaining_messages,
        metadata_rows,
    ) = asyncio.run(scenario())

    assert len(history_hits) == 1
    assert history_hits[0].source_type == "history"
    assert "sqlite fts docs" in history_hits[0].content.lower()
    assert other_chat_hits == []
    assert {hit.source_type for hit in knowledge_hits} == {"web_search", "web_fetch"}
    assert len(fetch_only_hits) == 1
    assert fetch_only_hits[0].source_type == "web_fetch"
    assert knowledge_hits[0].provider in {"duckduckgo", "web_fetch"}
    assert {hit.extractor for hit in knowledge_hits} == {"search", "trafilatura"}
    assert fetch_only_hits[0].status == 200
    assert fetch_only_hits[0].content_type == "text/html"
    assert fetch_only_hits[0].truncated is False
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
