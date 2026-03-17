"""
minibot/storage/sqlite.py - SQLite Storage 實作

用 SQLite 儲存對話歷史 (持久化)
"""

import asyncio
import json
import os
from pathlib import Path

from minibot.storage.base import StorageProvider, StoredMessage


class SQLiteStorage(StorageProvider):
    """SQLite Storage 實作"""

    DEFAULT_DB_PATH = Path.home() / ".minibot" / "data" / "sessions.db"

    def __init__(self, db_path: str | os.PathLike[str] | None = None):
        self.db_path = self._resolve_db_path(db_path)
        self._lock = asyncio.Lock()
        self._init_db()

    @classmethod
    def _resolve_db_path(cls, db_path: str | os.PathLike[str] | None) -> Path:
        """Resolve db path; expands ~ to user home."""
        if db_path is None or str(db_path).strip() == "":
            return cls.DEFAULT_DB_PATH
        return Path(db_path).expanduser()

    def _init_db(self):
        """初始化資料庫"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id TEXT PRIMARY KEY,
                messages TEXT DEFAULT '[]',
                consolidated_index INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            )
        """)
        conn.commit()

    def _get_conn(self):
        """取得連線"""
        import sqlite3
        return sqlite3.connect(str(self.db_path))

    async def get_messages(self, chat_id: str, limit: int | None = None) -> list[StoredMessage]:
        """取得對話歷史"""
        async with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT messages FROM sessions WHERE chat_id = ?", (chat_id,))
            row = cur.fetchone()
            conn.close()

            if not row:
                return []

            messages = json.loads(row[0])
            if limit:
                messages = messages[-limit:]

            return [
                StoredMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    timestamp=m.get("timestamp", 0),
                    tool_name=m.get("tool_name"),
                    is_consolidated=m.get("is_consolidated", False),
                )
                for m in messages
            ]

    async def add_message(self, chat_id: str, message: StoredMessage) -> None:
        """加入訊息"""
        async with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT messages, created_at, consolidated_index FROM sessions WHERE chat_id = ?", (chat_id,))
            row = cur.fetchone()

            import time
            now = time.time()

            if row:
                messages = json.loads(row[0])
                created_at = row[1]
                consolidated_index = row[2] if row[2] is not None else 0
            else:
                messages = []
                created_at = now
                consolidated_index = 0

            messages.append({
                "role": message.role,
                "content": message.content,
                "timestamp": message.timestamp or now,
                "tool_name": message.tool_name,
                "is_consolidated": message.is_consolidated,
            })

            conn.execute(
                "INSERT OR REPLACE INTO sessions (chat_id, messages, created_at, updated_at, consolidated_index) VALUES (?, ?, ?, ?, ?)",
                (chat_id, json.dumps(messages), created_at, now, consolidated_index)
            )
            conn.commit()
            conn.close()

    async def clear_messages(self, chat_id: str) -> None:
        """清除歷史"""
        async with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))
            conn.commit()
            conn.close()

    async def get_consolidated_index(self, chat_id: str) -> int:
        """取得 consolidation 標記"""
        async with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT consolidated_index FROM sessions WHERE chat_id = ?", (chat_id,))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else 0

    async def set_consolidated_index(self, chat_id: str, index: int) -> None:
        """設定 consolidation 標記"""
        async with self._lock:
            conn = self._get_conn()
            conn.execute("UPDATE sessions SET consolidated_index = ? WHERE chat_id = ?", (index, chat_id))
            conn.commit()
            conn.close()

    async def get_all_chats(self) -> list[str]:
        """取得所有聊天室"""
        async with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT chat_id FROM sessions")
            rows = cur.fetchall()
            conn.close()
            return [r[0] for r in rows]
