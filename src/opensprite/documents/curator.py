"""Background curation orchestration for post-response maintenance and skill review."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Generic, Sequence, TypeVar

from ..config.schema import DocumentLlmConfig
from ..llms import ChatMessage
from ..llms import LLMProvider
from ..runs.events import (
    CURATOR_COMPLETED_EVENT,
    CURATOR_FAILED_EVENT,
    CURATOR_JOB_COMPLETED_EVENT,
    CURATOR_JOB_SKIPPED_EVENT,
    CURATOR_JOB_STARTED_EVENT,
    CURATOR_STARTED_EVENT,
)
from ..storage import StorageProvider, StoredMessage
from ..storage.base import get_storage_message_count, get_storage_messages_slice
from ..tool_names import CONFIGURE_SKILL_TOOL_NAME, READ_SKILL_TOOL_NAME, SKILL_REVIEW_TOOL_NAMES
from ..tools import ToolRegistry
from ..tools.result_status import classify_tool_result_status
from ..utils import count_messages_tokens
from ..utils.log import logger
from .active_task import ActiveTaskConsolidator
from .memory import MemoryStore, consolidate
from .user_profile import UserProfileConsolidator

if TYPE_CHECKING:
    from ..agent.execution import ExecutionResult


K = TypeVar("K", bound=Hashable)
SnapshotReader = Callable[[str], str]
SessionRunner = Callable[[str], Awaitable[Any]]
RunEventEmitter = Callable[[str, str, str, dict[str, Any], str | None, str | None], Awaitable[None]]
SkillReviewDecider = Callable[["ExecutionResult"], bool]
LearningRecorder = Callable[[str, str, str, str, str | None, dict[str, Any] | None], None]
CURATOR_STATE_SCHEMA_VERSION = 1
CURATOR_HISTORY_LIMIT = 20
CURATOR_MAINTENANCE_JOB_KEYS = ("memory", "recent_summary", "user_profile", "active_task")
CURATOR_SCOPE_CHOICES = ("maintenance", "skills", *CURATOR_MAINTENANCE_JOB_KEYS)
CURATOR_NO_RUNNING_EVENT_LOOP_REASON = "no-running-event-loop"
SKILL_REVIEW_TRANSCRIPT_TOO_SHORT_REASON = "transcript-too-short"
SKILL_REVIEW_SYSTEM = f"""You are OpenSprite's background skill curator. The main assistant already replied to the user; your work is invisible to them.

You may ONLY use these tools: `{READ_SKILL_TOOL_NAME}`, `{CONFIGURE_SKILL_TOOL_NAME}`.

Goal: decide whether the recent conversation contains a reusable procedural workflow worth saving as a skill (SKILL.md), or an update to an existing skill.

Rules:
- Prefer `action=upsert` on an existing skill when refining; use `action=add` only for a genuinely new skill id. Use `{READ_SKILL_TOOL_NAME}` with `skill-creator-design` before authoring a new skill.
- If nothing is worth persisting, reply with exactly this single line and stop (no tools): Nothing to save.
- Do not narrate, apologize, or mention this background pass.
- Use `{CONFIGURE_SKILL_TOOL_NAME}` for the session workspace `skills/` folder only. Bundled skills live read-only under `~/.opensprite/skills/<id>/`.
"""


class CoalescingTaskScheduler(Generic[K]):
    """Run at most one background task per key, then rerun once if requested."""

    def __init__(
        self,
        *,
        on_exception: Callable[[K, Exception], None] | None = None,
        on_rerun: Callable[[K], None] | None = None,
        on_schedule_error: Callable[[K, RuntimeError], None] | None = None,
    ):
        self._tasks: dict[K, asyncio.Task[None]] = {}
        self._rerun: set[K] = set()
        self._on_exception = on_exception
        self._on_rerun = on_rerun
        self._on_schedule_error = on_schedule_error

    @property
    def tasks(self) -> dict[K, asyncio.Task[None]]:
        """Expose current task bookkeeping for existing diagnostics/tests."""
        return self._tasks

    @property
    def rerun_keys(self) -> set[K]:
        """Expose pending rerun bookkeeping for existing diagnostics/tests."""
        return self._rerun

    def schedule(self, key: K, runner: Callable[[], Awaitable[None]]) -> bool:
        """Schedule a keyed runner, coalescing concurrent calls into one rerun."""
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            self._rerun.add(key)
            return False

        task: asyncio.Task[None] | None = None

        async def _run() -> None:
            try:
                while True:
                    self._rerun.discard(key)
                    try:
                        await runner()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if self._on_exception is None:
                            raise
                        self._on_exception(key, exc)
                    if key not in self._rerun:
                        break
                    if self._on_rerun is not None:
                        self._on_rerun(key)
            except asyncio.CancelledError:
                pass
            finally:
                if task is not None and self._tasks.get(key) is task:
                    self._tasks.pop(key, None)
                self._rerun.discard(key)

        try:
            task = asyncio.get_running_loop().create_task(_run())
        except RuntimeError as exc:
            if self._on_schedule_error is None:
                raise
            self._on_schedule_error(key, exc)
            return False
        self._tasks[key] = task
        return True

    async def wait(self) -> None:
        """Wait until all currently scheduled tasks and coalesced reruns finish."""
        while True:
            tasks = [task for task in self._tasks.values() if not task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Cancel and drain any in-flight tasks."""
        tasks = [task for task in self._tasks.values() if not task.done()]
        self._tasks.clear()
        self._rerun.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _ordered_maintenance_job_keys(job_keys: tuple[str, ...] | list[str] | set[str]) -> tuple[str, ...]:
    requested = {str(item or "").strip() for item in job_keys if str(item or "").strip()}
    return tuple(job_key for job_key in CURATOR_MAINTENANCE_JOB_KEYS if job_key in requested)


def resolve_curator_scope(scope: str | None) -> tuple[tuple[str, ...], bool]:
    normalized = str(scope or "").strip().lower()
    if not normalized:
        return CURATOR_MAINTENANCE_JOB_KEYS, True
    if normalized == "maintenance":
        return CURATOR_MAINTENANCE_JOB_KEYS, False
    if normalized == "skills":
        return (), True
    if normalized in CURATOR_MAINTENANCE_JOB_KEYS:
        return (normalized,), False
    raise ValueError(f"Unknown curator scope: {normalized}")


@dataclass(frozen=True)
class CuratorRequest:
    """Latest pending background curation request for one session."""

    session_id: str
    run_id: str | None = None
    channel: str | None = None
    external_chat_id: str | None = None
    result: ExecutionResult | None = None
    maintenance_job_keys: tuple[str, ...] = ()
    run_skill_review: bool = False


@dataclass(frozen=True)
class CuratorJob:
    """One snapshot-backed background job."""

    key: str
    label: str
    snapshot_reader: SnapshotReader
    runner: SessionRunner


def fingerprint_text_directory(root: Path | None) -> str:
    """Return a stable content fingerprint for one directory tree."""
    directory = Path(root).expanduser().resolve(strict=False) if root is not None else None
    if directory is None or not directory.is_dir():
        return ""

    digest = hashlib.sha256()
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def format_stored_messages_for_transcript(
    messages: Sequence[Any],
    *,
    per_message_max_chars: int = 6000,
    transcript_max_chars: int = 100_000,
) -> str:
    """Turn stored session rows into a plain-text transcript for the review model."""
    lines: list[str] = []
    total = 0
    for m in messages:
        role = str(getattr(m, "role", "") or "?").strip()
        tool_name = getattr(m, "tool_name", None)
        prefix = role.upper()
        if tool_name:
            prefix = f"{prefix} [tool:{tool_name}]"
        body = str(getattr(m, "content", "") or "").strip()
        if len(body) > per_message_max_chars:
            body = body[:per_message_max_chars] + "\n… (truncated)"
        block = f"{prefix}\n{body}\n"
        if total + len(block) > transcript_max_chars:
            lines.append("… (transcript truncated)")
            break
        lines.append(block)
        total += len(block)
    return "\n".join(lines).strip()


def build_skill_review_user_content(transcript: str) -> str:
    """User turn for the review-only LLM run."""
    return (
        "Below is a plain-text transcript of recent messages in this session (including tools when logged).\n\n"
        f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---\n\n"
        "Review the transcript. If a reusable how-to should be saved or updated as a skill, use the tools. "
        "Otherwise reply with exactly: Nothing to save."
    )


class SkillReviewService:
    """Runs the background skill persistence review pass."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        tools: ToolRegistry,
        transcript_message_limit_getter: Callable[[], int],
        max_tool_iterations_getter: Callable[[], int],
        build_system_prompt: Callable[[str], str],
        execute_messages: Callable[..., Awaitable[Any]],
    ):
        self.storage = storage
        self.tools = tools
        self._transcript_message_limit_getter = transcript_message_limit_getter
        self._max_tool_iterations_getter = max_tool_iterations_getter
        self._build_system_prompt = build_system_prompt
        self._execute_messages = execute_messages

    def tool_registry(self) -> ToolRegistry | None:
        """Return the restricted tool registry allowed during background skill review."""
        allowed = SKILL_REVIEW_TOOL_NAMES
        available = set(self.tools.tool_names)
        if not allowed.issubset(available):
            return None
        excluded = available - allowed
        return self.tools.filtered(exclude_names=excluded)

    async def run(self, session_id: str, *, tool_registry: ToolRegistry) -> list[dict[str, str]]:
        """Execute one review pass for a session using the restricted skill tool registry."""
        stored = await self.storage.get_messages(session_id, limit=self._transcript_message_limit_getter())
        transcript = format_stored_messages_for_transcript(stored)
        if len(transcript) < 80:
            logger.info("[%s] skill.review.skip | reason=%s", session_id, SKILL_REVIEW_TRANSCRIPT_TOO_SHORT_REASON)
            return []

        user_content = build_skill_review_user_content(transcript)
        chat_messages = [
            ChatMessage(role="system", content=SKILL_REVIEW_SYSTEM),
            ChatMessage(role="user", content=user_content),
        ]
        touched_skills: list[dict[str, str]] = []

        async def on_tool_after_execute(tool_name: str, tool_args: dict[str, Any], result: str, *args: Any) -> None:
            if tool_name != CONFIGURE_SKILL_TOOL_NAME:
                return
            action = str((tool_args or {}).get("action") or "").strip()
            if action not in {"add", "upsert"}:
                return
            if not classify_tool_result_status(result).ok:
                return
            skill_name = str((tool_args or {}).get("skill_name") or "").strip()
            if not skill_name:
                return
            touched_skills.append(
                {
                    "skill_name": skill_name,
                    "action": action,
                    "description": str((tool_args or {}).get("description") or "").strip(),
                }
            )

        await self._execute_messages(
            f"{session_id}:skill-review",
            chat_messages,
            allow_tools=True,
            tool_result_session_id=None,
            tool_registry=tool_registry,
            on_tool_before_execute=None,
            on_tool_after_execute=on_tool_after_execute,
            refresh_system_prompt=lambda: self._build_system_prompt(session_id),
            max_tool_iterations=self._max_tool_iterations_getter(),
        )
        logger.info("[%s] skill.review.done", session_id)
        return touched_skills


class MemoryConsolidationService:
    """Coordinate incremental long-term memory consolidation."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        memory_store: MemoryStore,
        provider: LLMProvider,
        threshold: int,
        token_threshold: int,
        memory_llm: DocumentLlmConfig,
    ):
        self.storage = storage
        self.memory_store = memory_store
        self.provider = provider
        self.threshold = threshold
        self.token_threshold = token_threshold
        self.memory_llm = memory_llm

    @staticmethod
    def _to_message_dicts(messages: list[StoredMessage | dict[str, Any]]) -> list[dict[str, str]]:
        """Normalize stored messages for the memory consolidation prompt."""
        normalized: list[dict[str, str]] = []
        for message in messages:
            if isinstance(message, dict):
                normalized.append({
                    "role": message.get("role", "?"),
                    "content": message.get("content", ""),
                })
                continue

            normalized.append({
                "role": message.role,
                "content": message.content,
            })
        return normalized

    async def maybe_consolidate(self, session_id: str) -> None:
        """Consolidate pending session history into long-term memory when needed."""
        message_count = await get_storage_message_count(self.storage, session_id)
        last_consolidated = await self.storage.get_consolidated_index(session_id)
        if last_consolidated > message_count:
            await self.storage.set_consolidated_index(session_id, message_count)
            return
        pending_messages = self._to_message_dicts(
            await get_storage_messages_slice(
                self.storage,
                session_id,
                start_index=last_consolidated,
            )
        )
        unconsolidated = len(pending_messages)
        pending_tokens = count_messages_tokens(pending_messages, model=self.provider.get_default_model()) if pending_messages else 0

        should_consolidate_by_count = self.threshold > 0 and unconsolidated >= self.threshold
        should_consolidate_by_tokens = self.token_threshold > 0 and pending_tokens >= self.token_threshold
        if not should_consolidate_by_count and not should_consolidate_by_tokens:
            return

        logger.info(
            f"[{session_id}] memory.consolidate | pending_messages={unconsolidated} pending_tokens={pending_tokens} "
            f"threshold={self.threshold} token_threshold={self.token_threshold}"
        )
        try:
            success = await consolidate(
                memory_store=self.memory_store,
                session_id=session_id,
                messages=pending_messages,
                provider=self.provider,
                model=self.provider.get_default_model(),
                memory_llm=self.memory_llm,
            )
            if success:
                await self.storage.set_consolidated_index(session_id, message_count)
                logger.info(f"[{session_id}] memory.consolidated | total_messages={message_count}")
        except Exception as exc:
            logger.error(f"[{session_id}] memory.consolidate.error | error={exc}")


class UserProfileUpdateService:
    """Wrap optional USER.md profile updates behind a stable interface."""

    def __init__(self, consolidator: UserProfileConsolidator | None = None):
        self.consolidator = consolidator

    async def maybe_update(self, session_id: str) -> None:
        """Refresh this session's USER.md managed block when enough new history exists."""
        if self.consolidator is None:
            return

        try:
            await self.consolidator.maybe_update(session_id)
        except Exception as exc:
            logger.error(f"[{session_id}] profile.update.error | error={exc}")


class RecentSummaryUpdateService:
    """Wrap optional RECENT_SUMMARY.md updates behind a stable interface."""

    def __init__(self, consolidator: Any | None = None):
        self.consolidator = consolidator

    async def maybe_update(self, session_id: str) -> None:
        if self.consolidator is None:
            return

        try:
            await self.consolidator.maybe_update(session_id)
        except Exception as exc:
            logger.error(f"[{session_id}] recent_summary.update.error | error={exc}")


class ActiveTaskUpdateService:
    """Wrap optional ACTIVE_TASK.md updates behind a stable interface."""

    def __init__(self, consolidator: ActiveTaskConsolidator | None = None):
        self.consolidator = consolidator

    async def maybe_update(self, session_id: str) -> None:
        if self.consolidator is None:
            return

        try:
            await self.consolidator.maybe_update(session_id)
        except Exception as exc:
            logger.error(f"[{session_id}] active_task.update.error | error={exc}")


class CuratorService:
    """Coordinate background maintenance and skill review for one session."""

    def __init__(
        self,
        *,
        maybe_consolidate_memory: SessionRunner,
        maybe_update_recent_summary: SessionRunner,
        maybe_update_user_profile: SessionRunner,
        maybe_update_active_task: SessionRunner,
        run_skill_review: SessionRunner,
        should_run_skill_review: SkillReviewDecider,
        read_memory_snapshot: SnapshotReader,
        read_recent_summary_snapshot: SnapshotReader,
        read_user_profile_snapshot: SnapshotReader,
        read_active_task_snapshot: SnapshotReader,
        read_skill_snapshot: SnapshotReader,
        emit_run_event: RunEventEmitter,
        record_learning: LearningRecorder | None = None,
        state_path: Path | None = None,
        state_path_for_session: Callable[[str], Path] | None = None,
    ):
        self._memory_runner = maybe_consolidate_memory
        self._recent_summary_runner = maybe_update_recent_summary
        self._user_profile_runner = maybe_update_user_profile
        self._active_task_runner = maybe_update_active_task
        self._skill_review_runner = run_skill_review
        self._should_run_skill_review = should_run_skill_review
        self._emit_run_event = emit_run_event
        self._record_learning = record_learning
        self._state_path = Path(state_path).expanduser() if state_path is not None else None
        self._state_path_for_session = state_path_for_session
        self._memory_session_states: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, CuratorRequest] = {}
        self._active_requests: dict[str, CuratorRequest] = {}
        self._runtime_state: dict[str, dict[str, Any]] = {}
        self._scheduler = CoalescingTaskScheduler[str](
            on_exception=lambda session_id, _exc: logger.exception("[%s] curator.failed", session_id),
            on_rerun=lambda session_id: logger.info("[%s] curator.rerun", session_id),
            on_schedule_error=lambda session_id, _exc: logger.warning(
                "[%s] curator.skip | reason=%s",
                session_id,
                CURATOR_NO_RUNNING_EVENT_LOOP_REASON,
            ),
        )
        self.tasks = self._scheduler.tasks
        self.rerun_keys = self._scheduler.rerun_keys
        self._maintenance_jobs: tuple[CuratorJob, ...] = (
            CuratorJob("memory", "memory", read_memory_snapshot, self._memory_runner),
            CuratorJob("recent_summary", "recent summary", read_recent_summary_snapshot, self._recent_summary_runner),
            CuratorJob("user_profile", "user profile", read_user_profile_snapshot, self._user_profile_runner),
            CuratorJob("active_task", "active task", read_active_task_snapshot, self._active_task_runner),
        )
        self._skill_job = CuratorJob("skills", "skills", read_skill_snapshot, self._skill_review_runner)

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "schema_version": CURATOR_STATE_SCHEMA_VERSION,
            "paused": False,
            "run_count": 0,
            "last_run_at": None,
            "last_run_duration_seconds": None,
            "last_run_summary": None,
            "last_run_jobs": [],
            "last_run_changed": [],
            "last_error": None,
            "history": [],
        }

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _state_file_for_session(self, session_id: str) -> Path | None:
        if self._state_path_for_session is not None:
            return Path(self._state_path_for_session(session_id)).expanduser()
        return self._state_path

    def _load_session_state(self, session_id: str) -> dict[str, Any]:
        state_path = self._state_file_for_session(session_id)
        if state_path is None:
            state = self._memory_session_states.get(session_id)
            return dict(state) if isinstance(state, dict) else self._default_state()
        if not state_path.exists():
            return self._default_state()
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("curator.state.load_failed | path=%s error=%s", state_path, exc)
            return self._default_state()
        if not isinstance(raw, dict):
            return self._default_state()
        state = self._default_state()
        state["paused"] = bool(raw.get("paused"))
        state["run_count"] = self._safe_int(raw.get("run_count"))
        state["last_run_at"] = raw.get("last_run_at")
        state["last_run_duration_seconds"] = raw.get("last_run_duration_seconds")
        state["last_run_summary"] = raw.get("last_run_summary")
        state["last_error"] = raw.get("last_error")
        state["last_run_jobs"] = [str(item) for item in raw.get("last_run_jobs", []) if str(item).strip()] if isinstance(raw.get("last_run_jobs"), list) else []
        state["last_run_changed"] = [str(item) for item in raw.get("last_run_changed", []) if str(item).strip()] if isinstance(raw.get("last_run_changed"), list) else []
        history = raw.get("history") if isinstance(raw.get("history"), list) else []
        state["history"] = [dict(item) for item in history if isinstance(item, dict)][-CURATOR_HISTORY_LIMIT:]
        return state

    def _save_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        state_path = self._state_file_for_session(session_id)
        normalized_state = self._default_state()
        normalized_state.update(state)
        normalized_state["run_count"] = self._safe_int(normalized_state.get("run_count"))
        normalized_state["last_run_jobs"] = [str(item) for item in normalized_state.get("last_run_jobs", []) if str(item).strip()] if isinstance(normalized_state.get("last_run_jobs"), list) else []
        normalized_state["last_run_changed"] = [str(item) for item in normalized_state.get("last_run_changed", []) if str(item).strip()] if isinstance(normalized_state.get("last_run_changed"), list) else []
        history = normalized_state.get("history") if isinstance(normalized_state.get("history"), list) else []
        normalized_state["history"] = [dict(item) for item in history if isinstance(item, dict)][-CURATOR_HISTORY_LIMIT:]
        if state_path is None:
            self._memory_session_states[session_id] = normalized_state
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
                    json.dump(normalized_state, handle, indent=2, sort_keys=True, ensure_ascii=False)
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
            logger.warning("curator.state.save_failed | path=%s error=%s", state_path, exc)

    def _session_state(self, session_id: str) -> dict[str, Any]:
        return self._load_session_state(session_id)

    def _set_paused(self, session_id: str, paused: bool) -> None:
        state = self._session_state(session_id)
        state["paused"] = paused
        self._save_session_state(session_id, state)

    def _record_run(
        self,
        session_id: str,
        *,
        run_id: str | None,
        started_at: datetime,
        duration_seconds: float,
        jobs: list[str],
        changed: list[str],
        summary: str,
        error: str | None = None,
    ) -> None:
        state = self._session_state(session_id)
        state["last_run_at"] = started_at.isoformat()
        state["last_run_duration_seconds"] = duration_seconds
        state["last_run_jobs"] = jobs
        state["last_run_changed"] = changed
        state["last_run_summary"] = summary
        state["last_error"] = error
        state["run_count"] = self._safe_int(state.get("run_count")) + 1
        history = self._history_entries(session_id)
        history.append(
            {
                "run_id": run_id,
                "run_at": started_at.isoformat(),
                "duration_seconds": duration_seconds,
                "jobs": list(jobs),
                "changed": list(changed),
                "summary": summary,
                "error": error,
                "status": "failed" if error else "completed",
            }
        )
        state["history"] = history[-CURATOR_HISTORY_LIMIT:]
        self._save_session_state(session_id, state)

    def _history_entries(self, session_id: str) -> list[dict[str, Any]]:
        state = self._session_state(session_id)
        raw_history = state.get("history") if isinstance(state.get("history"), list) else []
        history = [dict(item) for item in raw_history if isinstance(item, dict)]
        state["history"] = history[-CURATOR_HISTORY_LIMIT:]
        return state["history"]

    def history(self, session_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        entries = list(reversed(self._history_entries(session_id)))
        return entries[: max(1, int(limit or 1))]

    def clear_session(self, session_id: str) -> None:
        """Delete persisted curator state for one session."""
        self._requests.pop(session_id, None)
        self._active_requests.pop(session_id, None)
        self._runtime_state.pop(session_id, None)
        self._memory_session_states.pop(session_id, None)
        state_path = self._state_file_for_session(session_id)
        if state_path is None:
            return
        try:
            if state_path.exists():
                state_path.unlink()
        except OSError as exc:
            logger.warning("curator.state.delete_failed | path=%s error=%s", state_path, exc)

    @staticmethod
    def _merge_request(current: CuratorRequest | None, incoming: CuratorRequest) -> CuratorRequest:
        if current is None:
            return incoming
        return CuratorRequest(
            session_id=incoming.session_id,
            run_id=incoming.run_id or current.run_id,
            channel=incoming.channel or current.channel,
            external_chat_id=incoming.external_chat_id or current.external_chat_id,
            result=incoming.result or current.result,
            maintenance_job_keys=_ordered_maintenance_job_keys([
                *current.maintenance_job_keys,
                *incoming.maintenance_job_keys,
            ]),
            run_skill_review=current.run_skill_review or incoming.run_skill_review,
        )

    def schedule_after_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        channel: str | None,
        external_chat_id: str | None,
        result: ExecutionResult,
    ) -> bool:
        """Schedule the full curator pass after one visible assistant turn."""
        return self._schedule(
            CuratorRequest(
                session_id=session_id,
                run_id=run_id,
                channel=channel,
                external_chat_id=external_chat_id,
                result=result,
                maintenance_job_keys=CURATOR_MAINTENANCE_JOB_KEYS,
                run_skill_review=self._should_run_skill_review(result),
            )
        )

    def schedule_maintenance(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> bool:
        """Schedule only the maintenance subset for one session."""
        return self._schedule(
            CuratorRequest(
                session_id=session_id,
                run_id=run_id,
                channel=channel,
                external_chat_id=external_chat_id,
                maintenance_job_keys=CURATOR_MAINTENANCE_JOB_KEYS,
            )
        )

    def schedule_skill_review(
        self,
        session_id: str,
        result: ExecutionResult,
        *,
        run_id: str | None = None,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> bool:
        """Schedule only the skill-review subset when the trigger conditions match."""
        if not self._should_run_skill_review(result):
            return False
        return self._schedule(
            CuratorRequest(
                session_id=session_id,
                run_id=run_id,
                channel=channel,
                external_chat_id=external_chat_id,
                result=result,
                run_skill_review=True,
            )
        )

    def schedule_manual_run(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        channel: str | None = None,
        external_chat_id: str | None = None,
        scope: str | None = None,
    ) -> bool:
        """Schedule a manual full curator pass for one session."""
        maintenance_job_keys, run_skill_review = resolve_curator_scope(scope)
        return self._schedule(
            CuratorRequest(
                session_id=session_id,
                run_id=run_id,
                channel=channel,
                external_chat_id=external_chat_id,
                maintenance_job_keys=maintenance_job_keys,
                run_skill_review=run_skill_review,
            )
        )

    def pause(self, session_id: str) -> dict[str, Any]:
        """Pause future curator scheduling for one session."""
        self._set_paused(session_id, True)
        return self.status(session_id)

    def resume(self, session_id: str) -> dict[str, Any]:
        """Resume future curator scheduling for one session."""
        self._set_paused(session_id, False)
        return self.status(session_id)

    def is_paused(self, session_id: str) -> bool:
        """Return whether one session currently suppresses curator scheduling."""
        return bool(self._session_state(session_id).get("paused"))

    def status(self, session_id: str) -> dict[str, Any]:
        """Return coarse runtime status for one session."""
        pending_request = self._requests.get(session_id)
        active_request = self._active_requests.get(session_id)
        runtime_state = self._runtime_state.get(session_id) or {}
        session_state = self._session_state(session_id)
        task = self.tasks.get(session_id)
        running = task is not None and not task.done()
        rerun_pending = session_id in self.rerun_keys
        queued = pending_request is not None and not running
        paused = bool(session_state.get("paused"))
        request = active_request if running else pending_request
        jobs: list[str] = []
        if request is not None:
            jobs.extend(request.maintenance_job_keys)
            if request.run_skill_review:
                jobs.append(self._skill_job.key)
        state = "running" if running else "queued" if queued else "paused" if paused else "idle"
        return {
            "session_id": session_id,
            "state": state,
            "running": running,
            "queued": queued,
            "paused": paused,
            "rerun_pending": rerun_pending,
            "jobs": jobs,
            "run_id": request.run_id if request is not None else None,
            "current_job": runtime_state.get("current_job"),
            "current_job_label": runtime_state.get("current_job_label"),
            "active_jobs": list(runtime_state.get("active_jobs") or []),
            "completed_jobs": list(runtime_state.get("completed_jobs") or []),
            "run_count": self._safe_int(session_state.get("run_count")),
            "last_run_at": session_state.get("last_run_at"),
            "last_run_duration_seconds": session_state.get("last_run_duration_seconds"),
            "last_run_summary": session_state.get("last_run_summary"),
            "last_run_jobs": session_state.get("last_run_jobs") or [],
            "last_run_changed": session_state.get("last_run_changed") or [],
            "last_error": session_state.get("last_error"),
        }

    def _schedule(self, request: CuratorRequest) -> bool:
        if self.is_paused(request.session_id):
            return False
        pending = self._requests.get(request.session_id)
        self._requests[request.session_id] = self._merge_request(pending, request)
        return self._scheduler.schedule(request.session_id, lambda: self._run_request(request.session_id))

    async def _emit_event(self, request: CuratorRequest, event_type: str, payload: dict[str, Any]) -> None:
        if not request.run_id:
            return
        await self._emit_run_event(
            request.session_id,
            request.run_id,
            event_type,
            payload,
            request.channel,
            request.external_chat_id,
        )

    async def _run_snapshot_job(self, session_id: str, job: CuratorJob) -> tuple[bool, Any]:
        before = job.snapshot_reader(session_id)
        runner_result = await job.runner(session_id)
        after = job.snapshot_reader(session_id)
        return before != after, runner_result

    def _record_learning_entries(
        self,
        request: CuratorRequest,
        changed_keys: list[str],
        job_results: dict[str, Any],
    ) -> None:
        if self._record_learning is None or not changed_keys:
            return
        for job_key in changed_keys:
            if job_key == "skills":
                skill_records = job_results.get(job_key)
                if isinstance(skill_records, list) and skill_records:
                    for item in skill_records:
                        skill_name = str(item.get("skill_name") or "").strip() if isinstance(item, dict) else ""
                        if not skill_name:
                            continue
                        description = str(item.get("description") or "").strip() if isinstance(item, dict) else ""
                        summary = description or f"Updated skill {skill_name}."
                        self._record_learning(
                            request.session_id,
                            kind="skill",
                            target_id=skill_name,
                            summary=summary,
                            source_run_id=request.run_id,
                            metadata={
                                "job": "skills",
                                "action": str(item.get("action") or "upsert") if isinstance(item, dict) else "upsert",
                                **({"description": description} if description else {}),
                            },
                        )
                    continue
                self._record_learning(
                    request.session_id,
                    kind="skill",
                    target_id="session_skills",
                    summary="Updated session skills.",
                    source_run_id=request.run_id,
                    metadata={"job": "skills"},
                )
                continue

            summary = {
                "memory": "Updated session memory.",
                "recent_summary": "Updated recent summary.",
                "user_profile": "Updated session user profile.",
                "active_task": "Updated active task.",
            }.get(job_key, f"Updated {job_key}.")
            self._record_learning(
                request.session_id,
                kind=job_key,
                target_id=job_key,
                summary=summary,
                source_run_id=request.run_id,
                metadata={"job": job_key},
            )

    @staticmethod
    def _format_summary(labels: list[str]) -> str:
        if not labels:
            return ""
        if len(labels) == 1:
            return f"Updated {labels[0]}."
        if len(labels) == 2:
            return f"Updated {labels[0]} and {labels[1]}."
        return f"Updated {', '.join(labels[:-1])}, and {labels[-1]}."

    async def _run_request(self, session_id: str) -> None:
        request = self._requests.pop(session_id, None)
        if request is None:
            return
        self._active_requests[session_id] = request
        try:
            if self.is_paused(session_id):
                return

            selected_jobs: list[CuratorJob] = []
            selected_jobs.extend(
                job for job in self._maintenance_jobs if job.key in request.maintenance_job_keys
            )
            if request.run_skill_review:
                selected_jobs.append(self._skill_job)
            if not selected_jobs:
                return

            started_at = datetime.now(timezone.utc)
            job_keys = [job.key for job in selected_jobs]
            self._runtime_state[session_id] = {
                "active_jobs": list(job_keys),
                "completed_jobs": [],
                "current_job": None,
                "current_job_label": "",
            }
            changed_keys: list[str] = []
            changed_labels: list[str] = []
            job_results: dict[str, Any] = {}
            try:
                await self._emit_event(
                    request,
                    CURATOR_STARTED_EVENT,
                    {
                        "status": "running",
                        "message": "Background curator tasks started.",
                        "jobs": job_keys,
                        "total_jobs": len(job_keys),
                    },
                )
                for index, job in enumerate(selected_jobs, start=1):
                    runtime_state = self._runtime_state.get(session_id)
                    if runtime_state is not None:
                        runtime_state["current_job"] = job.key
                        runtime_state["current_job_label"] = job.label
                    await self._emit_event(
                        request,
                        CURATOR_JOB_STARTED_EVENT,
                        {
                            "status": "running",
                            "job": job.key,
                            "label": job.label,
                            "index": index,
                            "total_jobs": len(job_keys),
                            "message": f"Running curator job: {job.label}.",
                        },
                    )
                    changed, runner_result = await self._run_snapshot_job(session_id, job)
                    job_results[job.key] = runner_result
                    runtime_state = self._runtime_state.get(session_id)
                    if runtime_state is not None:
                        runtime_state["completed_jobs"] = [
                            *list(runtime_state.get("completed_jobs") or []),
                            job.key,
                        ]
                    if changed:
                        changed_keys.append(job.key)
                        changed_labels.append(job.label)
                        await self._emit_event(
                            request,
                            CURATOR_JOB_COMPLETED_EVENT,
                            {
                                "status": "completed",
                                "job": job.key,
                                "label": job.label,
                                "index": index,
                                "total_jobs": len(job_keys),
                                "changed": True,
                                "summary": f"Updated {job.label}.",
                            },
                        )
                    else:
                        await self._emit_event(
                            request,
                            CURATOR_JOB_SKIPPED_EVENT,
                            {
                                "status": "skipped",
                                "job": job.key,
                                "label": job.label,
                                "index": index,
                                "total_jobs": len(job_keys),
                                "changed": False,
                                "reason": "no_changes",
                                "message": f"No changes for {job.label}.",
                            },
                        )

                summary = self._format_summary(changed_labels) if changed_keys else "No curator changes."
                runtime_state = self._runtime_state.get(session_id)
                if runtime_state is not None:
                    runtime_state["current_job"] = None
                    runtime_state["current_job_label"] = ""
                await self._emit_event(
                    request,
                    CURATOR_COMPLETED_EVENT,
                    {
                        "status": "completed",
                        "message": "Background curator tasks completed.",
                        "jobs": job_keys,
                        "changed": changed_keys,
                        "summary": summary,
                    },
                )
                self._record_run(
                    session_id,
                    run_id=request.run_id,
                    started_at=started_at,
                    duration_seconds=(datetime.now(timezone.utc) - started_at).total_seconds(),
                    jobs=job_keys,
                    changed=changed_keys,
                    summary=summary,
                )
                self._record_learning_entries(request, changed_keys, job_results)
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                runtime_state = self._runtime_state.get(session_id) or {}
                await self._emit_event(
                    request,
                    CURATOR_FAILED_EVENT,
                    {
                        "status": "failed",
                        "error": error,
                        "job": runtime_state.get("current_job"),
                        "label": runtime_state.get("current_job_label"),
                        "jobs": job_keys,
                        "completed_jobs": list(runtime_state.get("completed_jobs") or []),
                        "message": "Background curator tasks failed.",
                    },
                )
                self._record_run(
                    session_id,
                    run_id=request.run_id,
                    started_at=started_at,
                    duration_seconds=(datetime.now(timezone.utc) - started_at).total_seconds(),
                    jobs=job_keys,
                    changed=changed_keys,
                    summary=f"Curator failed: {error}",
                    error=error,
                )
                raise
        finally:
            if self._active_requests.get(session_id) is request:
                self._active_requests.pop(session_id, None)
            self._runtime_state.pop(session_id, None)

    async def wait(self) -> None:
        """Wait until all currently scheduled curator work completes."""
        await self._scheduler.wait()

    async def close(self) -> None:
        """Cancel any in-flight curator work and clear pending requests."""
        self._requests.clear()
        self._active_requests.clear()
        self._runtime_state.clear()
        await self._scheduler.close()
