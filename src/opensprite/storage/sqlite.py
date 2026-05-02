"""SQLite-backed storage with integrated FTS-ready search tables."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
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
from ..utils.json_safe import json_safe_value as json_safe
from ..utils.log import logger
from .base import (
    StorageProvider,
    StoredMessage,
    StoredRun,
    StoredRunEvent,
    StoredRunFileChange,
    StoredRunPart,
    StoredWorkState,
    coerce_stored_delegated_tasks,
    legacy_delegated_tasks,
    selected_delegated_task,
)

SQLITE_SCHEMA_VERSION = 11

SCHEMA_SCRIPT = """
CREATE TABLE IF NOT EXISTS chats (
    session_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_state (
    session_id TEXT PRIMARY KEY REFERENCES chats(session_id) ON DELETE CASCADE,
    consolidated_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES chats(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_consolidated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_created
    ON messages(session_id, created_at, id);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chats(session_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);

CREATE INDEX IF NOT EXISTS idx_runs_chat_created
    ON runs(session_id, created_at, run_id);

CREATE INDEX IF NOT EXISTS idx_runs_status
    ON runs(status, updated_at);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES chats(session_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_created
    ON run_events(run_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_run_events_chat_created
    ON run_events(session_id, created_at, id);

CREATE TABLE IF NOT EXISTS run_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES chats(session_id) ON DELETE CASCADE,
    part_type TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_parts_run_created
    ON run_parts(run_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_run_parts_chat_created
    ON run_parts(session_id, created_at, id);

CREATE TABLE IF NOT EXISTS run_file_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES chats(session_id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    before_sha256 TEXT,
    after_sha256 TEXT,
    before_content TEXT,
    after_content TEXT,
    diff TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_file_changes_run_created
    ON run_file_changes(run_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_run_file_changes_chat_path
    ON run_file_changes(session_id, path, created_at, id);

CREATE TABLE IF NOT EXISTS work_states (
    session_id TEXT PRIMARY KEY REFERENCES chats(session_id) ON DELETE CASCADE,
    objective TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '[]',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    done_criteria_json TEXT NOT NULL DEFAULT '[]',
    long_running INTEGER NOT NULL DEFAULT 0,
    coding_task INTEGER NOT NULL DEFAULT 0,
    expects_code_change INTEGER NOT NULL DEFAULT 0,
    expects_verification INTEGER NOT NULL DEFAULT 0,
    current_step TEXT NOT NULL DEFAULT 'not set',
    next_step TEXT NOT NULL DEFAULT 'not set',
    completed_steps_json TEXT NOT NULL DEFAULT '[]',
    pending_steps_json TEXT NOT NULL DEFAULT '[]',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    verification_targets_json TEXT NOT NULL DEFAULT '[]',
    resume_hint TEXT NOT NULL DEFAULT '',
    last_progress_signals_json TEXT NOT NULL DEFAULT '[]',
    file_change_count INTEGER NOT NULL DEFAULT 0,
    touched_paths_json TEXT NOT NULL DEFAULT '[]',
    verification_attempted INTEGER NOT NULL DEFAULT 0,
    verification_passed INTEGER NOT NULL DEFAULT 0,
    last_next_action TEXT NOT NULL DEFAULT '',
    delegated_tasks_json TEXT NOT NULL DEFAULT '[]',
    active_delegate_task_id TEXT,
    active_delegate_prompt_type TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_states_status
    ON work_states(status, updated_at);

CREATE TABLE IF NOT EXISTS knowledge_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES chats(session_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    query TEXT,
    title TEXT,
    url TEXT,
    summary TEXT,
    provider TEXT,
    extractor TEXT,
    status INTEGER,
    content_type TEXT,
    truncated INTEGER,
    raw_result TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chat_created
    ON knowledge_sources(session_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_knowledge_chat_source
    ON knowledge_sources(session_id, source_type);

CREATE TABLE IF NOT EXISTS search_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES chats(session_id) ON DELETE CASCADE,
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
    ON search_chunks(session_id, source_type, created_at, id);

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

CREATE TABLE IF NOT EXISTS search_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

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
    ensure_schema_upgrades(conn)
    conn.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
    conn.commit()


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the normalized schema used by storage and search."""
    conn.executescript(SCHEMA_SCRIPT)


def ensure_schema_upgrades(conn: sqlite3.Connection) -> None:
    """Apply additive schema upgrades for existing normalized databases."""
    if not table_exists(conn, "knowledge_sources"):
        return
    existing_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(knowledge_sources)").fetchall()
    }
    required_columns = {
        "summary": "TEXT",
        "provider": "TEXT",
        "extractor": "TEXT",
        "status": "INTEGER",
        "content_type": "TEXT",
        "truncated": "INTEGER",
    }
    for column_name, column_type in required_columns.items():
        if column_name in existing_columns:
            continue
        conn.execute(f"ALTER TABLE knowledge_sources ADD COLUMN {column_name} {column_type}")

    if table_exists(conn, "run_file_changes"):
        file_change_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(run_file_changes)").fetchall()
        }
        for column_name in ("before_content", "after_content"):
            if column_name in file_change_columns:
                continue
            conn.execute(f"ALTER TABLE run_file_changes ADD COLUMN {column_name} TEXT")

    if table_exists(conn, "work_states"):
        work_state_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(work_states)").fetchall()
        }
        required_work_state_columns = {
            "pending_steps_json": "TEXT NOT NULL DEFAULT '[]'",
            "blockers_json": "TEXT NOT NULL DEFAULT '[]'",
            "verification_targets_json": "TEXT NOT NULL DEFAULT '[]'",
            "resume_hint": "TEXT NOT NULL DEFAULT ''",
            "last_progress_signals_json": "TEXT NOT NULL DEFAULT '[]'",
            "delegated_tasks_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column_name, column_type in required_work_state_columns.items():
            if column_name in work_state_columns:
                continue
            conn.execute(f"ALTER TABLE work_states ADD COLUMN {column_name} {column_type}")


def ensure_chat_row(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    created_at: float,
    updated_at: float | None = None,
) -> None:
    """Ensure the chat metadata row exists before inserting related records."""
    current_updated_at = updated_at if updated_at is not None else created_at
    conn.execute(
        """
        INSERT INTO chats (session_id, created_at, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (session_id, created_at, current_updated_at),
    )


def insert_message_row(conn: sqlite3.Connection, session_id: str, message: StoredMessage) -> int:
    """Insert one stored message row and return its numeric id."""
    created_at = float(message.timestamp or time.time())
    ensure_chat_row(conn, session_id, created_at=created_at, updated_at=created_at)
    cursor = conn.execute(
        """
        INSERT INTO messages (session_id, role, content, tool_name, metadata_json, is_consolidated, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
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
    session_id: str,
    owner_type: str,
    owner_id: int,
    chunks: list[SearchChunkPayload],
) -> None:
    """Insert one batch of search chunks into the shared index table."""
    if not chunks:
        return []

    chunk_ids: list[int] = []
    for chunk in chunks:
        cursor = conn.execute(
            """
            INSERT INTO search_chunks (
                session_id,
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
            (
                session_id,
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
            ),
        )
        chunk_ids.append(int(cursor.lastrowid))
    return chunk_ids


def pack_embedding(values: list[float]) -> bytes:
    """Serialize one embedding vector to a portable little-endian blob."""
    if not values:
        return b""
    return struct.pack(f"<{len(values)}f", *[float(value) for value in values])


def unpack_embedding(blob: bytes, dim: int) -> list[float]:
    """Deserialize one embedding vector blob."""
    if not blob or dim <= 0:
        return []
    return list(struct.unpack(f"<{dim}f", blob))


def upsert_chunk_embedding(
    conn: sqlite3.Connection,
    *,
    chunk_id: int,
    provider: str,
    model: str,
    values: list[float] | None,
    status: str,
    embedded_at: float | None = None,
) -> None:
    """Insert or update one chunk embedding row."""
    current_time = embedded_at
    if current_time is None and status in {"completed", "failed"}:
        current_time = time.time()
    conn.execute(
        """
        INSERT INTO chunk_embeddings (
            chunk_id,
            embedding_provider,
            embedding_model,
            embedding_dim,
            embedding_format,
            embedding,
            embedding_status,
            embedded_at,
            version
        )
        VALUES (?, ?, ?, ?, 'blob', ?, ?, ?, 1)
        ON CONFLICT(chunk_id) DO UPDATE SET
            embedding_provider = excluded.embedding_provider,
            embedding_model = excluded.embedding_model,
            embedding_dim = excluded.embedding_dim,
            embedding_format = excluded.embedding_format,
            embedding = excluded.embedding,
            embedding_status = excluded.embedding_status,
            embedded_at = excluded.embedded_at,
            version = excluded.version
        """,
        (
            chunk_id,
            provider,
            model,
            len(values or []),
            pack_embedding(values or []),
            status,
            current_time,
        ),
    )


def insert_knowledge_document(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    document: KnowledgeDocument,
    created_at: float,
) -> tuple[int, list[int]]:
    """Insert one knowledge source and its searchable chunks."""
    ensure_chat_row(conn, session_id, created_at=created_at, updated_at=created_at)
    cursor = conn.execute(
        """
        INSERT INTO knowledge_sources (
            session_id,
            source_type,
            tool_name,
            query,
            title,
            url,
            summary,
            provider,
            extractor,
            status,
            content_type,
            truncated,
            raw_result,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            document.source_type,
            document.tool_name,
            document.query,
            document.title,
            document.url,
            document.summary,
            document.provider,
            document.extractor,
            document.status,
            document.content_type,
            1 if document.truncated is True else 0 if document.truncated is False else None,
            document.raw_result,
            created_at,
        ),
    )
    source_id = int(cursor.lastrowid)
    chunk_ids = insert_search_chunks(
        conn,
        session_id=session_id,
        owner_type="knowledge",
        owner_id=source_id,
        chunks=build_knowledge_chunks(document, created_at=created_at),
    )
    return source_id, chunk_ids


def find_message_owner_id(
    conn: sqlite3.Connection,
    *,
    session_id: str,
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
        WHERE session_id = ?
          AND role = ?
          AND content = ?
          AND created_at = ?
          AND ((tool_name IS NULL AND ? IS NULL) OR tool_name = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id, role, content, created_at, tool_name, tool_name),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    fallback = conn.execute(
        """
        SELECT id
        FROM messages
        WHERE session_id = ?
          AND role = ?
          AND content = ?
          AND ((tool_name IS NULL AND ? IS NULL) OR tool_name = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id, role, content, tool_name, tool_name),
    ).fetchone()
    return int(fallback["id"]) if fallback is not None else 0


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
        "SELECT session_id, messages, consolidated_index, created_at, updated_at FROM sessions ORDER BY session_id"
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
    session_id = str(row["session_id"])
    now = time.time()
    session_created_at = float(row["created_at"] or 0) or now
    session_updated_at = float(row["updated_at"] or 0) or session_created_at
    ensure_chat_row(conn, session_id, created_at=session_created_at, updated_at=session_updated_at)
    conn.execute(
        "INSERT INTO chat_state (session_id, consolidated_index) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET consolidated_index = excluded.consolidated_index",
        (session_id, int(row["consolidated_index"] or 0)),
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
        message_id = insert_message_row(conn, session_id, message)
        insert_search_chunks(
            conn,
            session_id=session_id,
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
            insert_knowledge_document(conn, session_id=session_id, document=document, created_at=created_at)


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

    @staticmethod
    def _rows_to_messages(rows: list[sqlite3.Row]) -> list[StoredMessage]:
        """Convert selected message rows into StoredMessage objects."""
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

    @staticmethod
    def _row_to_run(row: sqlite3.Row | None) -> StoredRun | None:
        """Convert one run row into a StoredRun object."""
        if row is None:
            return None
        return StoredRun(
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            status=str(row["status"]),
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
            finished_at=None if row["finished_at"] is None else float(row["finished_at"]),
            metadata=_load_metadata(row["metadata_json"]),
        )

    @staticmethod
    def _rows_to_run_events(rows: list[sqlite3.Row]) -> list[StoredRunEvent]:
        """Convert selected run event rows into StoredRunEvent objects."""
        return [
            StoredRunEvent(
                event_id=int(row["id"]),
                run_id=str(row["run_id"]),
                session_id=str(row["session_id"]),
                event_type=str(row["event_type"]),
                payload=_load_metadata(row["payload_json"]),
                created_at=float(row["created_at"] or 0),
            )
            for row in rows
        ]

    @staticmethod
    def _rows_to_run_parts(rows: list[sqlite3.Row]) -> list[StoredRunPart]:
        """Convert selected run part rows into StoredRunPart objects."""
        return [
            StoredRunPart(
                part_id=int(row["id"]),
                run_id=str(row["run_id"]),
                session_id=str(row["session_id"]),
                part_type=str(row["part_type"]),
                content=str(row["content"] or ""),
                tool_name=row["tool_name"],
                metadata=_load_metadata(row["metadata_json"]),
                created_at=float(row["created_at"] or 0),
            )
            for row in rows
        ]

    @staticmethod
    def _rows_to_run_file_changes(rows: list[sqlite3.Row]) -> list[StoredRunFileChange]:
        """Convert selected file-change rows into StoredRunFileChange objects."""
        return [
            StoredRunFileChange(
                change_id=int(row["id"]),
                run_id=str(row["run_id"]),
                session_id=str(row["session_id"]),
                tool_name=str(row["tool_name"]),
                path=str(row["path"]),
                action=str(row["action"]),
                before_sha256=row["before_sha256"],
                after_sha256=row["after_sha256"],
                before_content=row["before_content"],
                after_content=row["after_content"],
                diff=str(row["diff"] or ""),
                metadata=_load_metadata(row["metadata_json"]),
                created_at=float(row["created_at"] or 0),
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_work_state(row: sqlite3.Row | None) -> StoredWorkState | None:
        """Convert one work-state row into a StoredWorkState object."""
        if row is None:
            return None
        metadata = _load_metadata(row["metadata_json"])
        legacy_workboard = _load_legacy_workboard(metadata)
        delegated_tasks = coerce_stored_delegated_tasks(_load_json_list_or_fallback(row, "delegated_tasks_json"))
        if not delegated_tasks:
            delegated_tasks = legacy_delegated_tasks(row["active_delegate_task_id"], row["active_delegate_prompt_type"])
        selected_task = selected_delegated_task(delegated_tasks)
        return StoredWorkState(
            session_id=str(row["session_id"]),
            objective=str(row["objective"] or ""),
            kind=str(row["kind"] or "task"),
            status=str(row["status"] or "active"),
            steps=tuple(_load_string_list(row["steps_json"])),
            constraints=tuple(_load_string_list(row["constraints_json"])),
            done_criteria=tuple(_load_string_list(row["done_criteria_json"])),
            long_running=bool(row["long_running"]),
            coding_task=bool(row["coding_task"]),
            expects_code_change=bool(row["expects_code_change"]),
            expects_verification=bool(row["expects_verification"]),
            current_step=str(row["current_step"] or "not set"),
            next_step=str(row["next_step"] or "not set"),
            completed_steps=tuple(_load_string_list(row["completed_steps_json"])),
            pending_steps=tuple(_load_string_list_or_fallback(row, "pending_steps_json", legacy_workboard.get("pending_steps"))),
            blockers=tuple(_load_string_list_or_fallback(row, "blockers_json", legacy_workboard.get("blockers"))),
            verification_targets=tuple(
                _load_string_list_or_fallback(row, "verification_targets_json", legacy_workboard.get("verification_targets"))
            ),
            resume_hint=_load_string_or_fallback(row, "resume_hint", legacy_workboard.get("resume_hint")),
            last_progress_signals=tuple(
                _load_string_list_or_fallback(row, "last_progress_signals_json", legacy_workboard.get("last_progress_signals"))
            ),
            file_change_count=int(row["file_change_count"] or 0),
            touched_paths=tuple(_load_string_list(row["touched_paths_json"])),
            verification_attempted=bool(row["verification_attempted"]),
            verification_passed=bool(row["verification_passed"]),
            last_next_action=str(row["last_next_action"] or ""),
            delegated_tasks=delegated_tasks,
            active_delegate_task_id=selected_task.task_id if selected_task is not None else row["active_delegate_task_id"],
            active_delegate_prompt_type=(
                selected_task.prompt_type if selected_task is not None else row["active_delegate_prompt_type"]
            ),
            metadata=metadata,
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
        )

    async def get_messages(self, session_id: str, limit: int | None = None) -> list[StoredMessage]:
        """Return the persisted messages for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                if limit:
                    rows = conn.execute(
                        """
                        SELECT role, content, created_at, tool_name, is_consolidated, metadata_json
                        FROM messages
                        WHERE session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (session_id, limit),
                    ).fetchall()
                    rows = list(reversed(rows))
                else:
                    rows = conn.execute(
                        """
                        SELECT role, content, created_at, tool_name, is_consolidated, metadata_json
                        FROM messages
                        WHERE session_id = ?
                        ORDER BY id ASC
                        """,
                        (session_id,),
                    ).fetchall()

                return self._rows_to_messages(rows)
            finally:
                conn.close()

    async def get_message_count(self, session_id: str) -> int:
        """Return the total persisted message count for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                return int(row["count"] if row is not None else 0)
            finally:
                conn.close()

    async def get_messages_slice(
        self,
        session_id: str,
        *,
        start_index: int = 0,
        end_index: int | None = None,
    ) -> list[StoredMessage]:
        """Return one ordered message slice for a chat."""
        start = max(0, int(start_index))
        stop = None if end_index is None else max(start, int(end_index))
        if stop is not None and stop <= start:
            return []

        async with self._lock:
            conn = self._get_conn()
            try:
                if stop is None:
                    rows = conn.execute(
                        """
                        SELECT role, content, created_at, tool_name, is_consolidated, metadata_json
                        FROM messages
                        WHERE session_id = ?
                        ORDER BY id ASC
                        LIMIT -1 OFFSET ?
                        """,
                        (session_id, start),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT role, content, created_at, tool_name, is_consolidated, metadata_json
                        FROM messages
                        WHERE session_id = ?
                        ORDER BY id ASC
                        LIMIT ? OFFSET ?
                        """,
                        (session_id, stop - start, start),
                    ).fetchall()
                return self._rows_to_messages(rows)
            finally:
                conn.close()

    async def add_message(self, session_id: str, message: StoredMessage) -> None:
        """Persist one message in the normalized schema."""
        async with self._lock:
            conn = self._get_conn()
            try:
                insert_message_row(conn, session_id, message)
                conn.commit()
            finally:
                conn.close()

    async def clear_messages(self, session_id: str) -> None:
        """Delete all persisted data for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM chats WHERE session_id = ?", (session_id,))
                conn.commit()
            finally:
                conn.close()

    async def get_consolidated_index(self, session_id: str) -> int:
        """Return the last consolidated message index for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT consolidated_index FROM chat_state WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                return int(row["consolidated_index"]) if row is not None else 0
            finally:
                conn.close()

    async def set_consolidated_index(self, session_id: str, index: int) -> None:
        """Persist the latest consolidated index for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                current_time = time.time()
                ensure_chat_row(conn, session_id, created_at=current_time, updated_at=current_time)
                conn.execute(
                    """
                    INSERT INTO chat_state (session_id, consolidated_index)
                    VALUES (?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET consolidated_index = excluded.consolidated_index
                    """,
                    (session_id, int(index)),
                )
                conn.commit()
            finally:
                conn.close()

    async def create_run(
        self,
        session_id: str,
        run_id: str,
        *,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRun | None:
        """Persist the start of one user-facing run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                now = float(created_at or time.time())
                ensure_chat_row(conn, session_id, created_at=now, updated_at=now)
                conn.execute(
                    """
                    INSERT INTO runs (run_id, session_id, status, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        status = excluded.status,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        run_id,
                        session_id,
                        status,
                        json.dumps(json_safe(metadata or {}), ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                return self._row_to_run(row)
            finally:
                conn.close()

    async def update_run_status(
        self,
        session_id: str,
        run_id: str,
        status: str,
        *,
        metadata: dict[str, Any] | None = None,
        finished_at: float | None = None,
    ) -> StoredRun | None:
        """Update the lifecycle status for one run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                now = time.time()
                row = conn.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                if row is None:
                    return None
                merged_metadata = _load_metadata(row["metadata_json"])
                if metadata:
                    merged_metadata.update(metadata)
                conn.execute(
                    """
                    UPDATE runs
                    SET status = ?, metadata_json = ?, updated_at = ?, finished_at = COALESCE(?, finished_at)
                    WHERE run_id = ? AND session_id = ?
                    """,
                    (
                        status,
                        json.dumps(json_safe(merged_metadata), ensure_ascii=False),
                        now,
                        finished_at,
                        run_id,
                        session_id,
                    ),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                return self._row_to_run(updated)
            finally:
                conn.close()

    async def get_runs(self, session_id: str, limit: int | None = None) -> list[StoredRun]:
        """Return persisted runs for one chat from newest to oldest."""
        async with self._lock:
            conn = self._get_conn()
            try:
                params: tuple[Any, ...]
                query = """
                    SELECT *
                    FROM runs
                    WHERE session_id = ?
                    ORDER BY created_at DESC, run_id DESC
                """
                params = (session_id,)
                if limit is not None:
                    query += " LIMIT ?"
                    params = (session_id, int(limit))
                rows = conn.execute(query, params).fetchall()
                return [run for run in (self._row_to_run(row) for row in rows) if run is not None]
            finally:
                conn.close()

    async def get_run(self, session_id: str, run_id: str) -> StoredRun | None:
        """Return one persisted run for a chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM runs WHERE session_id = ? AND run_id = ?",
                    (session_id, run_id),
                ).fetchone()
                return self._row_to_run(row)
            finally:
                conn.close()

    async def get_work_state(self, session_id: str) -> StoredWorkState | None:
        """Return the persisted structured work state for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM work_states WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                return self._row_to_work_state(row)
            finally:
                conn.close()

    async def upsert_work_state(self, state: StoredWorkState) -> StoredWorkState | None:
        """Create or replace structured work state for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                created_at = float(state.created_at or time.time())
                updated_at = float(state.updated_at or time.time())
                delegated_tasks = coerce_stored_delegated_tasks(state.delegated_tasks) or legacy_delegated_tasks(
                    state.active_delegate_task_id,
                    state.active_delegate_prompt_type,
                )
                selected_task = selected_delegated_task(delegated_tasks)
                ensure_chat_row(conn, state.session_id, created_at=created_at, updated_at=updated_at)
                existing = conn.execute(
                    "SELECT created_at FROM work_states WHERE session_id = ?",
                    (state.session_id,),
                ).fetchone()
                if existing is not None and existing["created_at"] is not None:
                    created_at = float(existing["created_at"])
                conn.execute(
                    """
                    INSERT INTO work_states (
                        session_id,
                        objective,
                        kind,
                        status,
                        steps_json,
                        constraints_json,
                        done_criteria_json,
                        long_running,
                        coding_task,
                        expects_code_change,
                        expects_verification,
                        current_step,
                        next_step,
                        completed_steps_json,
                        pending_steps_json,
                        blockers_json,
                        verification_targets_json,
                        resume_hint,
                        last_progress_signals_json,
                        file_change_count,
                        touched_paths_json,
                        verification_attempted,
                        verification_passed,
                        last_next_action,
                        delegated_tasks_json,
                        active_delegate_task_id,
                        active_delegate_prompt_type,
                        metadata_json,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        objective = excluded.objective,
                        kind = excluded.kind,
                        status = excluded.status,
                        steps_json = excluded.steps_json,
                        constraints_json = excluded.constraints_json,
                        done_criteria_json = excluded.done_criteria_json,
                        long_running = excluded.long_running,
                        coding_task = excluded.coding_task,
                        expects_code_change = excluded.expects_code_change,
                        expects_verification = excluded.expects_verification,
                        current_step = excluded.current_step,
                        next_step = excluded.next_step,
                        completed_steps_json = excluded.completed_steps_json,
                        pending_steps_json = excluded.pending_steps_json,
                        blockers_json = excluded.blockers_json,
                        verification_targets_json = excluded.verification_targets_json,
                        resume_hint = excluded.resume_hint,
                        last_progress_signals_json = excluded.last_progress_signals_json,
                        file_change_count = excluded.file_change_count,
                        touched_paths_json = excluded.touched_paths_json,
                        verification_attempted = excluded.verification_attempted,
                        verification_passed = excluded.verification_passed,
                        last_next_action = excluded.last_next_action,
                        delegated_tasks_json = excluded.delegated_tasks_json,
                        active_delegate_task_id = excluded.active_delegate_task_id,
                        active_delegate_prompt_type = excluded.active_delegate_prompt_type,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        state.session_id,
                        state.objective,
                        state.kind,
                        state.status,
                        json.dumps(json_safe(list(state.steps)), ensure_ascii=False),
                        json.dumps(json_safe(list(state.constraints)), ensure_ascii=False),
                        json.dumps(json_safe(list(state.done_criteria)), ensure_ascii=False),
                        int(bool(state.long_running)),
                        int(bool(state.coding_task)),
                        int(bool(state.expects_code_change)),
                        int(bool(state.expects_verification)),
                        state.current_step,
                        state.next_step,
                        json.dumps(json_safe(list(state.completed_steps)), ensure_ascii=False),
                        json.dumps(json_safe(list(state.pending_steps)), ensure_ascii=False),
                        json.dumps(json_safe(list(state.blockers)), ensure_ascii=False),
                        json.dumps(json_safe(list(state.verification_targets)), ensure_ascii=False),
                        state.resume_hint,
                        json.dumps(json_safe(list(state.last_progress_signals)), ensure_ascii=False),
                        int(state.file_change_count),
                        json.dumps(json_safe(list(state.touched_paths)), ensure_ascii=False),
                        int(bool(state.verification_attempted)),
                        int(bool(state.verification_passed)),
                        state.last_next_action,
                        json.dumps(json_safe([task.to_payload() for task in delegated_tasks]), ensure_ascii=False),
                        selected_task.task_id if selected_task is not None else None,
                        selected_task.prompt_type if selected_task is not None else None,
                        json.dumps(json_safe(state.metadata or {}), ensure_ascii=False),
                        created_at,
                        updated_at,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM work_states WHERE session_id = ?",
                    (state.session_id,),
                ).fetchone()
                return self._row_to_work_state(row)
            finally:
                conn.close()

    async def clear_work_state(self, session_id: str) -> None:
        """Remove structured work state for one chat."""
        async with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM work_states WHERE session_id = ?", (session_id,))
                conn.commit()
            finally:
                conn.close()

    async def add_run_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRunEvent | None:
        """Persist one structured event for a run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                now = float(created_at or time.time())
                ensure_chat_row(conn, session_id, created_at=now, updated_at=now)
                if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                    conn.execute(
                        """
                        INSERT INTO runs (run_id, session_id, status, metadata_json, created_at, updated_at)
                        VALUES (?, ?, 'running', '{}', ?, ?)
                        """,
                        (run_id, session_id, now, now),
                    )
                cursor = conn.execute(
                    """
                    INSERT INTO run_events (run_id, session_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        session_id,
                        event_type,
                        json.dumps(json_safe(payload or {}), ensure_ascii=False),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM run_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
                events = self._rows_to_run_events([row]) if row is not None else []
                return events[0] if events else None
            finally:
                conn.close()

    async def get_run_events(self, session_id: str, run_id: str) -> list[StoredRunEvent]:
        """Return all events persisted for one run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT id, run_id, session_id, event_type, payload_json, created_at
                    FROM run_events
                    WHERE session_id = ? AND run_id = ?
                    ORDER BY id ASC
                    """,
                    (session_id, run_id),
                ).fetchall()
                return self._rows_to_run_events(rows)
            finally:
                conn.close()

    async def add_run_part(
        self,
        session_id: str,
        run_id: str,
        part_type: str,
        *,
        content: str = "",
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRunPart | None:
        """Persist one ordered execution artifact for a run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                now = float(created_at or time.time())
                ensure_chat_row(conn, session_id, created_at=now, updated_at=now)
                if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                    conn.execute(
                        """
                        INSERT INTO runs (run_id, session_id, status, metadata_json, created_at, updated_at)
                        VALUES (?, ?, 'running', '{}', ?, ?)
                        """,
                        (run_id, session_id, now, now),
                    )
                cursor = conn.execute(
                    """
                    INSERT INTO run_parts (run_id, session_id, part_type, content, tool_name, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        session_id,
                        part_type,
                        str(content or ""),
                        tool_name,
                        json.dumps(json_safe(metadata or {}), ensure_ascii=False),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM run_parts WHERE id = ?", (cursor.lastrowid,)).fetchone()
                parts = self._rows_to_run_parts([row]) if row is not None else []
                return parts[0] if parts else None
            finally:
                conn.close()

    async def get_run_parts(self, session_id: str, run_id: str) -> list[StoredRunPart]:
        """Return all durable parts persisted for one run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT id, run_id, session_id, part_type, content, tool_name, metadata_json, created_at
                    FROM run_parts
                    WHERE session_id = ? AND run_id = ?
                    ORDER BY id ASC
                    """,
                    (session_id, run_id),
                ).fetchall()
                return self._rows_to_run_parts(rows)
            finally:
                conn.close()

    async def add_run_file_change(
        self,
        session_id: str,
        run_id: str,
        tool_name: str,
        path: str,
        action: str,
        *,
        before_sha256: str | None = None,
        after_sha256: str | None = None,
        before_content: str | None = None,
        after_content: str | None = None,
        diff: str = "",
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRunFileChange | None:
        """Persist one file mutation captured during a run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                now = float(created_at or time.time())
                ensure_chat_row(conn, session_id, created_at=now, updated_at=now)
                if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                    conn.execute(
                        """
                        INSERT INTO runs (run_id, session_id, status, metadata_json, created_at, updated_at)
                        VALUES (?, ?, 'running', '{}', ?, ?)
                        """,
                        (run_id, session_id, now, now),
                    )
                cursor = conn.execute(
                    """
                    INSERT INTO run_file_changes (
                        run_id,
                        session_id,
                        tool_name,
                        path,
                        action,
                        before_sha256,
                        after_sha256,
                        before_content,
                        after_content,
                        diff,
                        metadata_json,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        session_id,
                        tool_name,
                        path,
                        action,
                        before_sha256,
                        after_sha256,
                        before_content,
                        after_content,
                        str(diff or ""),
                        json.dumps(json_safe(metadata or {}), ensure_ascii=False),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM run_file_changes WHERE id = ?", (cursor.lastrowid,)).fetchone()
                changes = self._rows_to_run_file_changes([row]) if row is not None else []
                return changes[0] if changes else None
            finally:
                conn.close()

    async def get_run_file_changes(self, session_id: str, run_id: str) -> list[StoredRunFileChange]:
        """Return file mutations captured for one run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT id, run_id, session_id, tool_name, path, action, before_sha256, after_sha256, before_content, after_content, diff, metadata_json, created_at
                    FROM run_file_changes
                    WHERE session_id = ? AND run_id = ?
                    ORDER BY id ASC
                    """,
                    (session_id, run_id),
                ).fetchall()
                return self._rows_to_run_file_changes(rows)
            finally:
                conn.close()

    async def get_run_file_change(
        self,
        session_id: str,
        run_id: str,
        change_id: int,
    ) -> StoredRunFileChange | None:
        """Return one captured file mutation for a run."""
        async with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    """
                    SELECT id, run_id, session_id, tool_name, path, action, before_sha256, after_sha256, before_content, after_content, diff, metadata_json, created_at
                    FROM run_file_changes
                    WHERE session_id = ? AND run_id = ? AND id = ?
                    """,
                    (session_id, run_id, int(change_id)),
                ).fetchone()
                changes = self._rows_to_run_file_changes([row]) if row is not None else []
                return changes[0] if changes else None
            finally:
                conn.close()

    async def get_all_sessions(self) -> list[str]:
        """Return all known session ids."""
        async with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("SELECT session_id FROM chats ORDER BY session_id ASC").fetchall()
                return [str(row["session_id"]) for row in rows]
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


def _load_legacy_workboard(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return legacy workboard content still stored under metadata for old rows."""
    payload = metadata.get("workboard") if isinstance(metadata, dict) else None
    return payload if isinstance(payload, dict) else {}


def _load_string_or_fallback(row: sqlite3.Row, key: str, fallback: Any = None) -> str:
    """Read one optional text column, using fallback when the column is absent or empty."""
    keys = row.keys() if hasattr(row, "keys") else []
    if key in keys:
        value = str(row[key] or "")
        if value:
            return value
    return str(fallback or "")


def _load_string_list_or_fallback(row: sqlite3.Row, key: str, fallback: Any = None) -> list[str]:
    """Read one optional JSON string-list column, using fallback when absent or empty."""
    keys = row.keys() if hasattr(row, "keys") else []
    if key in keys:
        value = _load_string_list(row[key])
        if value:
            return value
    if isinstance(fallback, list):
        return [str(item) for item in fallback if str(item).strip()]
    return []


def _load_json_list_or_fallback(row: sqlite3.Row, key: str, fallback: Any = None) -> list[Any]:
    """Read one optional JSON list column, using fallback when absent or invalid."""
    keys = row.keys() if hasattr(row, "keys") else []
    if key in keys and row[key]:
        try:
            payload = json.loads(row[key])
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, list):
            return payload
    return list(fallback) if isinstance(fallback, (list, tuple)) else []


def _load_string_list(raw: str | None) -> list[str]:
    """Parse one stored JSON list into a normalized list of strings."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]
