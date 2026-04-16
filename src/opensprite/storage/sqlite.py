"""SQLite-backed storage with integrated FTS-ready search tables."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..search.indexing import (
    KnowledgeDocument,
    SearchChunkPayload,
    build_history_chunks,
    build_knowledge_chunks,
    build_knowledge_documents_from_message,
)
from ..utils.log import logger
from .base import StorageProvider, StoredMessage

SQLITE_SCHEMA_VERSION = 2

SCHEMA_SCRIPT = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_state (
    chat_id TEXT PRIMARY KEY REFERENCES chats(chat_id) ON DELETE CASCADE,
    consolidated_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_consolidated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_created
    ON messages(chat_id, created_at, id);

CREATE TABLE IF NOT EXISTS knowledge_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    query TEXT,
    title TEXT,
    url TEXT,
    raw_result TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chat_created
    ON knowledge_sources(chat_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_knowledge_chat_source
    ON knowledge_sources(chat_id, source_type);

CREATE TABLE IF NOT EXISTS search_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL,
    owner_id INTEGER NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL,
    role TEXT,
    tool_name TEXT,
    query TEXT,
    title TEXT,
    url TEXT,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_chat_source_created
    ON search_chunks(chat_id, source_type, created_at, id);

CREATE INDEX IF NOT EXISTS idx_chunks_owner
    ON search_chunks(owner_type, owner_id, chunk_index);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES search_chunks(id) ON DELETE CASCADE,
    embedding_provider TEXT,
    embedding_model TEXT,
    embedding_dim INTEGER,
    embedding_format TEXT NOT NULL DEFAULT 'blob',
    embedding BLOB,
    embedding_status TEXT NOT NULL DEFAULT 'pending',
    embedded_at REAL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_status
    ON chunk_embeddings(embedding_status, embedding_model);

CREATE VIRTUAL TABLE IF NOT EXISTS search_chunks_fts USING fts5(
    content,
    title,
    query,
    url,
    content='search_chunks',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS search_chunks_ai AFTER INSERT ON search_chunks BEGIN
    INSERT INTO search_chunks_fts(rowid, content, title, query, url)
    VALUES (new.id, new.content, COALESCE(new.title, ''), COALESCE(new.query, ''), COALESCE(new.url, ''));
END;

CREATE TRIGGER IF NOT EXISTS search_chunks_ad AFTER DELETE ON search_chunks BEGIN
    INSERT INTO search_chunks_fts(search_chunks_fts, rowid, content, title, query, url)
    VALUES ('delete', old.id, old.content, COALESCE(old.title, ''), COALESCE(old.query, ''), COALESCE(old.url, ''));
END;

CREATE TRIGGER IF NOT EXISTS search_chunks_au AFTER UPDATE ON search_chunks BEGIN
    INSERT INTO search_chunks_fts(search_chunks_fts, rowid, content, title, query, url)
    VALUES ('delete', old.id, old.content, COALESCE(old.title, ''), COALESCE(old.query, ''), COALESCE(old.url, ''));
    INSERT INTO search_chunks_fts(rowid, content, title, query, url)
    VALUES (new.id, new.content, COALESCE(new.title, ''), COALESCE(new.query, ''), COALESCE(new.url, ''));
END;
"""


def open_sqlite_connection(db_path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection for the shared app database."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Ensure the normalized schema exists, migrating the legacy table if needed."""
    has_messages = table_exists(conn, "messages")
    has_legacy_sessions = table_exists(conn, "sessions")

    if has_legacy_sessions and not has_messages:
        migrate_legacy_sessions(conn)
        return

    create_schema(conn)
    conn.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
    conn.commit()


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the normalized schema used by storage and search."""
    conn.executescript(SCHEMA_SCRIPT)


def ensure_chat_row(
    conn: sqlite3.Connection,
    chat_id: str,
    *,
    created_at: float,
    updated_at: float | None = None,
) -> None:
    """Ensure the chat metadata row exists before inserting related records."""
    current_updated_at = updated_at if updated_at is not None else created_at
    conn.execute(
        """
        INSERT INTO chats (chat_id, created_at, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (chat_id, created_at, current_updated_at),
    )


def insert_message_row(conn: sqlite3.Connection, chat_id: str, message: StoredMessage) -> int:
    """Insert one stored message row and return its numeric id."""
    created_at = float(message.timestamp or time.time())
    ensure_chat_row(conn, chat_id, created_at=created_at, updated_at=created_at)
    cursor = conn.execute(
        """
        INSERT INTO messages (chat_id, role, content, tool_name, metadata_json, is_consolidated, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            message.role,
            message.content,
            message.tool_name,
            json.dumps(json_safe(message.metadata), ensure_ascii=False),
            1 if message.is_consolidated else 0,
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def insert_search_chunks(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    owner_type: str,
    owner_id: int,
    chunks: list[SearchChunkPayload],
) -> None:
    """Insert one batch of search chunks into the shared index table."""
    if not chunks:
        return
    conn.executemany(
        """
        INSERT INTO search_chunks (
            chat_id,
            owner_type,
            owner_id,
            source_type,
            role,
            tool_name,
            query,
            title,
            url,
            chunk_index,
            content,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                chat_id,
                owner_type,
                owner_id,
                chunk.source_type,
                chunk.role,
                chunk.tool_name,
                chunk.query,
                chunk.title,
                chunk.url,
                chunk.chunk_index,
                chunk.content,
                chunk.created_at,
            )
            for chunk in chunks
        ],
    )


def insert_knowledge_document(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    document: KnowledgeDocument,
    created_at: float,
) -> int:
    """Insert one knowledge source and its searchable chunks."""
    ensure_chat_row(conn, chat_id, created_at=created_at, updated_at=created_at)
    cursor = conn.execute(
        """
        INSERT INTO knowledge_sources (chat_id, source_type, tool_name, query, title, url, raw_result, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            document.source_type,
            document.tool_name,
            document.query,
            document.title,
            document.url,
            document.raw_result,
            created_at,
        ),
    )
    source_id = int(cursor.lastrowid)
    insert_search_chunks(
        conn,
        chat_id=chat_id,
        owner_type="knowledge",
        owner_id=source_id,
        chunks=build_knowledge_chunks(document, created_at=created_at),
    )
    return source_id


def find_message_owner_id(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    role: str,
    content: str,
    tool_name: str | None,
    created_at: float,
) -> int:
    """Resolve the latest message row id for a just-persisted message."""
    row = conn.execute(
        """
        SELECT id
        FROM messages
        WHERE chat_id = ?
          AND role = ?
          AND content = ?
          AND created_at = ?
          AND ((tool_name IS NULL AND ? IS NULL) OR tool_name = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (chat_id, role, content, created_at, tool_name, tool_name),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    fallback = conn.execute(
        """
        SELECT id
        FROM messages
        WHERE chat_id = ?
          AND role = ?
          AND content = ?
          AND ((tool_name IS NULL AND ? IS NULL) OR tool_name = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (chat_id, role, content, tool_name, tool_name),
    ).fetchone()
    return int(fallback["id"]) if fallback is not None else 0


def json_safe(value: Any) -> Any:
    """Convert metadata into JSON-serializable structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Return whether a table or view exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view') LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def migrate_legacy_sessions(conn: sqlite3.Connection) -> None:
    """Migrate the old sessions JSON table into the normalized schema."""
    legacy_rows = conn.execute(
        "SELECT chat_id, messages, consolidated_index, created_at, updated_at FROM sessions ORDER BY chat_id"
    ).fetchall()
    logger.info("Migrating legacy SQLite sessions schema ({} chat(s))", len(legacy_rows))

    try:
        conn.execute("BEGIN")
        create_schema(conn)
        for row in legacy_rows:
            _migrate_legacy_chat(conn, row)
        conn.execute("DROP TABLE sessions")
        conn.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_legacy_chat(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    chat_id = str(row["chat_id"])
    now = time.time()
    session_created_at = float(row["created_at"] or 0) or now
    session_updated_at = float(row["updated_at"] or 0) or session_created_at
    ensure_chat_row(conn, chat_id, created_at=session_created_at, updated_at=session_updated_at)
    conn.execute(
        "INSERT INTO chat_state (chat_id, consolidated_index) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET consolidated_index = excluded.consolidated_index",
        (chat_id, int(row["consolidated_index"] or 0)),
    )

    messages_blob = row["messages"] or "[]"
    try:
        raw_messages = json.loads(messages_blob)
    except json.JSONDecodeError:
        raw_messages = []

    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        created_at = float(raw_message.get("timestamp", 0) or 0) or session_created_at
        message = StoredMessage(
            role=str(raw_message.get("role", "user") or "user"),
            content=str(raw_message.get("content", "") or ""),
            timestamp=created_at,
            tool_name=raw_message.get("tool_name"),
            is_consolidated=bool(raw_message.get("is_consolidated", False)),
            metadata=raw_message.get("metadata", {}) if isinstance(raw_message.get("metadata", {}), dict) else {},
        )
        message_id = insert_message_row(conn, chat_id, message)
        insert_search_chunks(
            conn,
            chat_id=chat_id,
            owner_type="message",
            owner_id=message_id,
            chunks=build_history_chunks(
                role=message.role,
                content=message.content,
                tool_name=message.tool_name,
                created_at=created_at,
            ),
        )
        for document in build_knowledge_documents_from_message(message):
            insert_knowledge_document(conn, chat_id=chat_id, document=document, created_at=created_at)


class SQLiteStorage(StorageProvider):
    """Normalized SQLite storage implementation."""

    DEFAULT_DB_PATH = Path.home() / ".opensprite" / "data" / "sessions.db"

    def __init__(self, db_path: str | os.PathLike[str] | None = None):
        self.db_path = self._resolve_db_path(db_path)
        self._lock = asyncio.Lock()
        self._init_db()

    @classmethod
    def _resolve_db_path(cls, db_path: str | os.PathLike[str] | None) -> Path:
        """Resolve db path; expands ``~`` to the user home directory."""
        if db_path is None or str(db_path).strip() == "":
            return cls.DEFAULT_DB_PATH
        return Path(db_path).expanduser()

    def _init_db(self) -> None:
        """Initialize or migrate the shared SQLite database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        try:
            ensure_sqlite_schema(conn)
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Open a configured connection to the shared database."""
        return open_sqlite_connection(self.db_path)

    async def get_messages(self, chat_id: str, limit: int | None = None) -> list[StoredMessage]:
        """Return the persisted messages for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                if limit:
                    rows = conn.execute(
                        """
                        SELECT role, content, created_at, tool_name, is_consolidated, metadata_json
                        FROM messages
                        WHERE chat_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (chat_id, limit),
                    ).fetchall()
                    rows = list(reversed(rows))
                else:
                    rows = conn.execute(
                        """
                        SELECT role, content, created_at, tool_name, is_consolidated, metadata_json
                        FROM messages
                        WHERE chat_id = ?
                        ORDER BY id ASC
                        """,
                        (chat_id,),
                    ).fetchall()

                return [
                    StoredMessage(
                        role=str(row["role"]),
                        content=str(row["content"]),
                        timestamp=float(row["created_at"] or 0),
                        tool_name=row["tool_name"],
                        is_consolidated=bool(row["is_consolidated"]),
                        metadata=_load_metadata(row["metadata_json"]),
                    )
                    for row in rows
                ]
            finally:
                conn.close()

    async def add_message(self, chat_id: str, message: StoredMessage) -> None:
        """Persist one message in the normalized schema."""
        async with self._lock:
            conn = self._get_conn()
            try:
                insert_message_row(conn, chat_id, message)
                conn.commit()
            finally:
                conn.close()

    async def clear_messages(self, chat_id: str) -> None:
        """Delete all persisted data for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
                conn.commit()
            finally:
                conn.close()

    async def get_consolidated_index(self, chat_id: str) -> int:
        """Return the last consolidated message index for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT consolidated_index FROM chat_state WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()
                return int(row["consolidated_index"]) if row is not None else 0
            finally:
                conn.close()

    async def set_consolidated_index(self, chat_id: str, index: int) -> None:
        """Persist the latest consolidated index for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                current_time = time.time()
                ensure_chat_row(conn, chat_id, created_at=current_time, updated_at=current_time)
                conn.execute(
                    """
                    INSERT INTO chat_state (chat_id, consolidated_index)
                    VALUES (?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET consolidated_index = excluded.consolidated_index
                    """,
                    (chat_id, int(index)),
                )
                conn.commit()
            finally:
                conn.close()

    async def get_all_chats(self) -> list[str]:
        """Return all known chat ids."""
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("SELECT chat_id FROM chats ORDER BY chat_id ASC").fetchall()
                return [str(row["chat_id"]) for row in rows]
            finally:
                conn.close()


def _load_metadata(raw: str | None) -> dict[str, Any]:
    """Parse stored metadata JSON safely."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
