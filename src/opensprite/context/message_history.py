"""Conversation history load/save and retrieval helpers for AgentLoop."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..llms import ChatMessage
from ..runs.events import SEARCH_INDEX_MESSAGE_FAILED_EVENT
from ..search.base import SearchHit, SearchStore
from ..storage import StorageProvider, StoredMessage
from ..utils.log import logger

HISTORY_SEARCH_TOOL_NAME = "search_history"
HISTORY_RESULT_COUNT_METADATA_KEYS = ("result_count", "hit_count", "hits", "count")
HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON = "assistant did not provide enough recalled items"
LEARNING_LEDGER_SCHEMA_VERSION = 1
LEARNING_LEDGER_LIMIT = 200
LEARNING_RELEVANT_LIMIT = 4
_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}")
_KIND_LABELS = {
    "skill": "Skill",
    "memory": "Memory",
    "user_profile": "User profile",
    "recent_summary": "Recent summary",
    "active_task": "Active task",
}
_TARGET_LABELS = {
    "memory": "Session memory",
    "user_profile": "Session profile",
    "recent_summary": "Recent summary",
    "active_task": "Active task",
}


class LearningLedger:
    """Persistent session-scoped ledger for learned artifacts and reuse outcomes."""

    def __init__(
        self,
        state_path: Path | None = None,
        *,
        state_path_for_session: Callable[[str], Path] | None = None,
    ):
        self._state_path = Path(state_path).expanduser() if state_path is not None else None
        self._state_path_for_session = state_path_for_session
        self._memory_sessions: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {"schema_version": LEARNING_LEDGER_SCHEMA_VERSION, "entries": []}

    def _state_file_for_session(self, session_id: str) -> Path | None:
        if self._state_path_for_session is not None:
            return Path(self._state_path_for_session(session_id)).expanduser()
        return self._state_path

    def _load_entries(self, session_id: str) -> list[dict[str, Any]]:
        state_path = self._state_file_for_session(session_id)
        if state_path is None:
            entries = self._memory_sessions.setdefault(session_id, [])
            entries[:] = [self._normalize_entry(item) for item in entries if isinstance(item, dict)][-LEARNING_LEDGER_LIMIT:]
            return entries
        if not state_path.exists():
            return []
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("learning.state.load_failed | path=%s error=%s", state_path, exc)
            return []
        if not isinstance(raw, dict):
            return []
        raw_entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
        return [self._normalize_entry(item) for item in raw_entries if isinstance(item, dict)][-LEARNING_LEDGER_LIMIT:]

    def _save_entries(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        state_path = self._state_file_for_session(session_id)
        normalized_entries = [self._normalize_entry(item) for item in entries if isinstance(item, dict)][-LEARNING_LEDGER_LIMIT:]
        if state_path is None:
            self._memory_sessions[session_id] = normalized_entries
            return
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(state_path.parent),
                prefix=f".{state_path.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "schema_version": LEARNING_LEDGER_SCHEMA_VERSION,
                            "entries": normalized_entries,
                        },
                        handle,
                        indent=2,
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, state_path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.warning("learning.state.save_failed | path=%s error=%s", state_path, exc)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _session_entries(self, session_id: str) -> list[dict[str, Any]]:
        return self._load_entries(session_id)

    def _normalize_entry(self, entry: dict[str, Any], *, kind: str = "", target_id: str = "") -> dict[str, Any]:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        return {
            "kind": str(entry.get("kind") or kind or "other").strip(),
            "target_id": str(entry.get("target_id") or target_id or "item").strip(),
            "summary": str(entry.get("summary") or "").strip(),
            "source_run_id": str(entry.get("source_run_id") or "").strip() or None,
            "metadata": dict(metadata),
            "created_at": str(entry.get("created_at") or self._now_iso()).strip() or self._now_iso(),
            "updated_at": str(entry.get("updated_at") or entry.get("created_at") or self._now_iso()).strip() or self._now_iso(),
            "last_used_at": str(entry.get("last_used_at") or "").strip() or None,
            "use_count": self._safe_int(entry.get("use_count")),
            "last_outcome": str(entry.get("last_outcome") or "").strip() or None,
        }

    def _find_entry(self, session_id: str, *, kind: str, target_id: str) -> dict[str, Any] | None:
        for entry in self._session_entries(session_id):
            if entry["kind"] == kind and entry["target_id"] == target_id:
                return entry
        return None

    def record_learning(
        self,
        session_id: str,
        *,
        kind: str,
        target_id: str,
        summary: str,
        source_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entries = self._session_entries(session_id)
        entry = self._find_entry(session_id, kind=kind, target_id=target_id)
        now = self._now_iso()
        if entry is None:
            entry = self._normalize_entry({}, kind=kind, target_id=target_id)
            entry["created_at"] = now
            entries.append(entry)
        entry["summary"] = str(summary or entry.get("summary") or "").strip()
        entry["source_run_id"] = source_run_id or entry.get("source_run_id")
        if metadata:
            entry["metadata"] = {**entry.get("metadata", {}), **metadata}
        entry["updated_at"] = now
        self._save_entries(session_id, entries)
        return dict(entry)

    def mark_used(
        self,
        session_id: str,
        *,
        kind: str,
        target_id: str,
        outcome: str,
        summary: str | None = None,
        source_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entries = self._session_entries(session_id)
        entry = self._find_entry(session_id, kind=kind, target_id=target_id)
        now = self._now_iso()
        if entry is None:
            entry = self._normalize_entry({}, kind=kind, target_id=target_id)
            entry["created_at"] = now
            entries.append(entry)
        if summary:
            entry["summary"] = str(summary).strip()
        if source_run_id:
            entry["source_run_id"] = source_run_id
        if metadata:
            entry["metadata"] = {**entry.get("metadata", {}), **metadata}
        entry["last_used_at"] = now
        entry["last_outcome"] = str(outcome or "success").strip() or "success"
        entry["use_count"] = self._safe_int(entry.get("use_count")) + 1
        entry["updated_at"] = now
        self._save_entries(session_id, entries)
        return dict(entry)

    def recent_entries(self, session_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        entries = sorted(
            self._session_entries(session_id),
            key=lambda item: (str(item.get("updated_at") or ""), str(item.get("created_at") or "")),
            reverse=True,
        )
        return [dict(entry) for entry in entries[: max(1, int(limit or 1))]]

    def clear_session(self, session_id: str) -> None:
        """Delete all learned artifacts for one session."""
        state_path = self._state_file_for_session(session_id)
        self._memory_sessions.pop(session_id, None)
        if state_path is None:
            return
        try:
            if state_path.exists():
                state_path.unlink()
        except OSError as exc:
            logger.warning("learning.state.delete_failed | path=%s error=%s", state_path, exc)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        seen: list[str] = []
        for token in _TOKEN_PATTERN.findall(str(text or "").lower()):
            if token not in seen:
                seen.append(token)
        return seen

    @staticmethod
    def _entry_title(entry: dict[str, Any]) -> str:
        kind = str(entry.get("kind") or "other").strip()
        target_id = str(entry.get("target_id") or "item").strip()
        if kind == "skill":
            return target_id
        return _TARGET_LABELS.get(target_id, target_id.replace("_", " "))

    def _score_entry(self, entry: dict[str, Any], tokens: list[str]) -> int:
        haystack = " ".join(
            [
                str(entry.get("target_id") or ""),
                str(entry.get("summary") or ""),
                json.dumps(entry.get("metadata") or {}, ensure_ascii=False),
            ]
        ).lower()
        score = 0
        for token in tokens:
            if token and token in haystack:
                score += 6
        score += min(self._safe_int(entry.get("use_count")), 5)
        if entry.get("last_outcome") == "success":
            score += 2
        if entry.get("last_used_at"):
            score += 1
        if entry.get("kind") == "skill":
            score += 1
        return score

    def relevant_entries(self, session_id: str, current_message: str, *, limit: int = LEARNING_RELEVANT_LIMIT) -> list[dict[str, Any]]:
        entries = self.recent_entries(session_id, limit=LEARNING_LEDGER_LIMIT)
        if not entries:
            return []
        tokens = self._tokenize(current_message)
        scored = [
            (self._score_entry(entry, tokens), index, entry)
            for index, entry in enumerate(entries)
        ]
        matching = [item for item in scored if item[0] > 0]
        if not matching:
            return []
        matching.sort(
            key=lambda item: (
                item[0],
                str(item[2].get("updated_at") or ""),
                -item[1],
            ),
            reverse=True,
        )
        return [dict(item[2]) for item in matching[: max(1, int(limit or 1))]]

    def build_relevant_context(self, session_id: str, current_message: str, *, limit: int = LEARNING_RELEVANT_LIMIT) -> str:
        entries = self.relevant_entries(session_id, current_message, limit=limit)
        if not entries:
            return ""
        lines = [
            "# Relevant Learned Context",
            "",
            "These items were learned earlier in this session and may help with the current request.",
            "",
        ]
        for entry in entries:
            kind_label = _KIND_LABELS.get(str(entry.get("kind") or "").strip(), str(entry.get("kind") or "item"))
            title = self._entry_title(entry)
            summary = str(entry.get("summary") or "").strip() or title
            extras: list[str] = []
            use_count = self._safe_int(entry.get("use_count"))
            if use_count > 0:
                extras.append(f"used {use_count}x")
            last_outcome = str(entry.get("last_outcome") or "").strip()
            if last_outcome:
                extras.append(f"last outcome: {last_outcome}")
            detail = f" ({'; '.join(extras)})" if extras else ""
            lines.append(f"- [{kind_label}] {title}: {summary}{detail}")
        return "\n".join(lines)


def is_history_retrieval_tool_name(tool_name: str | None) -> bool:
    """Return whether a tool name represents chat-history retrieval."""
    return str(tool_name or "").strip() == HISTORY_SEARCH_TOOL_NAME


def history_retrieval_metadata_reports_empty(metadata: dict[str, Any] | None) -> bool:
    """Return whether search-history metadata explicitly reports zero matches."""
    if not isinstance(metadata, dict):
        return False
    saw_count_field = False
    for key in HISTORY_RESULT_COUNT_METADATA_KEYS:
        if key not in metadata:
            continue
        value = metadata.get(key)
        if _metadata_value_has_results(value):
            return False
        saw_count_field = True
    return saw_count_field


def history_retrieval_metadata_has_results(metadata: dict[str, Any] | None) -> bool:
    """Return whether search-history metadata explicitly reports one or more matches."""
    if not isinstance(metadata, dict):
        return False
    return any(
        _metadata_value_has_results(metadata.get(key))
        for key in HISTORY_RESULT_COUNT_METADATA_KEYS
        if key in metadata
    )


def _metadata_value_has_results(value: object) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    return _coerce_int(value, default=0) > 0


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(float(stripped))
            except ValueError:
                return default
    return default


def _reasoning_details_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]] | None:
    details = metadata.get("llm_reasoning_details")
    return details if isinstance(details, list) else None


class MessageHistoryService:
    """Loads session history and persists messages with optional search indexing."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        search_store: SearchStore | None,
        max_history_getter: Callable[[], int],
        emit_index_failure: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self.storage = storage
        self.search_store = search_store
        self._max_history_getter = max_history_getter
        self._emit_index_failure = emit_index_failure

    async def load_history(self, session_id: str) -> list[ChatMessage]:
        """Load conversation history as ChatMessage objects for LLM consumption."""
        stored_messages = await self.storage.get_messages(
            session_id,
            limit=self._max_history_getter(),
        )

        chat_messages = []
        for message in stored_messages:
            if isinstance(message, dict):
                metadata = message.get("metadata", {}) if isinstance(message.get("metadata", {}), dict) else {}
                chat_messages.append(ChatMessage(
                    role=message.get("role", "?"),
                    content=message.get("content", ""),
                    reasoning_details=_reasoning_details_from_metadata(metadata),
                ))
            else:
                metadata = message.metadata if isinstance(message.metadata, dict) else {}
                chat_messages.append(ChatMessage(
                    role=message.role,
                    content=message.content,
                    reasoning_details=_reasoning_details_from_metadata(metadata),
                ))

        return chat_messages

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save one message to storage and index it when search is configured."""
        created_at = time.time()
        await self.storage.add_message(
            session_id,
            StoredMessage(
                role=role,
                content=content,
                timestamp=created_at,
                tool_name=tool_name,
                metadata=dict(metadata or {}),
            ),
        )
        if self.search_store is None:
            return

        try:
            await self.search_store.index_message(
                session_id=session_id,
                role=role,
                content=content,
                tool_name=tool_name,
                created_at=created_at,
            )
        except Exception as e:
            logger.warning("[{}] Failed to index message for search: {}", session_id, e)
            if self._emit_index_failure is not None:
                await self._emit_index_failure(
                    session_id,
                    SEARCH_INDEX_MESSAGE_FAILED_EVENT,
                    {
                        "role": role,
                        "tool_name": tool_name,
                        "content_len": len(str(content or "")),
                        "error": str(e),
                    },
                )


class HistoryResetService:
    """Clears session history and related per-session derived state."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        search_store: SearchStore | None,
        clear_session_artifacts: Callable[[str], Awaitable[None]],
    ):
        self.storage = storage
        self.search_store = search_store
        self._clear_session_artifacts = clear_session_artifacts

    async def reset(self, session_id: str | None = None) -> None:
        """Clear one session or all sessions from storage and derived indexes."""
        if session_id:
            await self._clear_one(session_id)
            return

        all_sessions = await self.storage.get_all_sessions()
        for current_session_id in all_sessions:
            await self._clear_one(current_session_id)

    async def _clear_one(self, session_id: str) -> None:
        await self.storage.clear_messages(session_id)
        await self._clear_session_artifacts(session_id)
        if self.search_store is None:
            return
        try:
            await self.search_store.clear_session(session_id)
        except Exception as e:
            logger.warning("[{}] Failed to clear search index: {}", session_id, e)


class ProactiveRetrievalService:
    """Fetch compact prior context when the task-context resolver asks for it."""

    def __init__(self, *, search_store: SearchStore | None):
        self.search_store = search_store

    async def build_context(
        self,
        *,
        session_id: str,
        current_message: str,
        should_retrieve: bool | None = None,
    ) -> str:
        if self.search_store is None or not bool(should_retrieve):
            return ""

        history_hits = await self.search_store.search_history(session_id=session_id, query=current_message, limit=3)
        if not history_hits:
            return ""

        sections = [
            "# Proactive Retrieval Context",
            "The task-context resolver selected prior chat retrieval. Use the snippets below before asking the user to restate information.",
            "",
            "## Retrieved History",
            *self._format_history_hits(history_hits),
        ]
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
