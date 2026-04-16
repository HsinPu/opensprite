import asyncio
import json
import sqlite3

from opensprite.storage.sqlite import SQLiteStorage


def test_sqlite_storage_migrates_legacy_sessions_and_drops_table(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE sessions (
            chat_id TEXT PRIMARY KEY,
            messages TEXT DEFAULT '[]',
            consolidated_index INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL
        )
        """
    )
    legacy_messages = [
        {"role": "user", "content": "Please keep sqlite fts docs handy", "timestamp": 1.0},
        {
            "role": "tool",
            "content": "Results for: sqlite fts5\n\n1. SQLite FTS5\n   https://sqlite.org/fts5.html\n   Official full text search docs",
            "timestamp": 2.0,
            "tool_name": "web_search",
        },
        {
            "role": "tool",
            "content": json.dumps(
                {
                    "title": "SQLite FTS5",
                    "url": "https://sqlite.org/fts5.html",
                    "finalUrl": "https://sqlite.org/fts5.html",
                    "text": "SQLite FTS5 supports full text search in a single database.",
                }
            ),
            "timestamp": 3.0,
            "tool_name": "web_fetch",
        },
    ]
    conn.execute(
        "INSERT INTO sessions (chat_id, messages, consolidated_index, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("chat-1", json.dumps(legacy_messages), 4, 1.0, 3.0),
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db_path)

    messages = asyncio.run(storage.get_messages("chat-1"))
    consolidated_index = asyncio.run(storage.get_consolidated_index("chat-1"))
    chats = asyncio.run(storage.get_all_chats())

    assert [message.role for message in messages] == ["user", "tool", "tool"]
    assert [message.tool_name for message in messages] == [None, "web_search", "web_fetch"]
    assert consolidated_index == 4
    assert chats == ["chat-1"]

    conn = sqlite3.connect(str(db_path))
    table_names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    knowledge_count = conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM search_chunks").fetchone()[0]
    conn.close()

    assert "sessions" not in table_names
    assert {"chats", "chat_state", "messages", "knowledge_sources", "search_chunks", "search_chunks_fts"}.issubset(table_names)
    assert knowledge_count == 2
    assert chunk_count >= 5
