"""Background curation orchestration for post-response maintenance and skill review."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..utils.log import logger
from .background_tasks import CoalescingTaskScheduler
from .execution import ExecutionResult


SnapshotReader = Callable[[str], str]
SessionRunner = Callable[[str], Awaitable[None]]
RunEventEmitter = Callable[[str, str, str, dict[str, Any], str | None, str | None], Awaitable[None]]
SkillReviewDecider = Callable[[ExecutionResult], bool]
CURATOR_STATE_SCHEMA_VERSION = 1
CURATOR_MAINTENANCE_JOB_KEYS = ("memory", "recent_summary", "user_profile", "active_task")
CURATOR_SCOPE_CHOICES = ("maintenance", "skills", *CURATOR_MAINTENANCE_JOB_KEYS)


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
        state_path: Path | None = None,
    ):
        self._memory_runner = maybe_consolidate_memory
        self._recent_summary_runner = maybe_update_recent_summary
        self._user_profile_runner = maybe_update_user_profile
        self._active_task_runner = maybe_update_active_task
        self._skill_review_runner = run_skill_review
        self._should_run_skill_review = should_run_skill_review
        self._emit_run_event = emit_run_event
        self._state_path = Path(state_path).expanduser() if state_path is not None else None
        self._state = self._load_state()
        self._requests: dict[str, CuratorRequest] = {}
        self._active_requests: dict[str, CuratorRequest] = {}
        self._runtime_state: dict[str, dict[str, Any]] = {}
        self._paused_sessions: set[str] = self._load_paused_sessions()
        self._scheduler = CoalescingTaskScheduler[str](
            on_exception=lambda session_id, _exc: logger.exception("[%s] curator.failed", session_id),
            on_rerun=lambda session_id: logger.info("[%s] curator.rerun", session_id),
            on_schedule_error=lambda session_id, _exc: logger.warning(
                "[%s] curator.skip | reason=no-running-event-loop",
                session_id,
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
        return {"schema_version": CURATOR_STATE_SCHEMA_VERSION, "sessions": {}}

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _load_state(self) -> dict[str, Any]:
        if self._state_path is None or not self._state_path.exists():
            return self._default_state()
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("curator.state.load_failed | path=%s error=%s", self._state_path, exc)
            return self._default_state()
        if not isinstance(raw, dict):
            return self._default_state()
        sessions = raw.get("sessions") if isinstance(raw.get("sessions"), dict) else {}
        normalized: dict[str, dict[str, Any]] = {}
        for session_id, value in sessions.items():
            if isinstance(session_id, str) and isinstance(value, dict):
                normalized[session_id] = dict(value)
        return {"schema_version": CURATOR_STATE_SCHEMA_VERSION, "sessions": normalized}

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._state_path.parent),
                prefix=f".{self._state_path.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._state, handle, indent=2, sort_keys=True, ensure_ascii=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self._state_path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.warning("curator.state.save_failed | path=%s error=%s", self._state_path, exc)

    def _session_state(self, session_id: str) -> dict[str, Any]:
        sessions = self._state.setdefault("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
            self._state["sessions"] = sessions
        state = sessions.get(session_id)
        if not isinstance(state, dict):
            state = {}
            sessions[session_id] = state
        return state

    def _load_paused_sessions(self) -> set[str]:
        sessions = self._state.get("sessions") if isinstance(self._state.get("sessions"), dict) else {}
        return {
            session_id
            for session_id, state in sessions.items()
            if isinstance(session_id, str) and isinstance(state, dict) and bool(state.get("paused"))
        }

    def _set_paused(self, session_id: str, paused: bool) -> None:
        if paused:
            self._paused_sessions.add(session_id)
        else:
            self._paused_sessions.discard(session_id)
        self._session_state(session_id)["paused"] = paused
        self._save_state()

    def _record_run(
        self,
        session_id: str,
        *,
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
        self._save_state()

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
        return session_id in self._paused_sessions

    def status(self, session_id: str) -> dict[str, Any]:
        """Return coarse runtime status for one session."""
        pending_request = self._requests.get(session_id)
        active_request = self._active_requests.get(session_id)
        runtime_state = self._runtime_state.get(session_id) or {}
        task = self.tasks.get(session_id)
        running = task is not None and not task.done()
        rerun_pending = session_id in self.rerun_keys
        queued = pending_request is not None and not running
        paused = session_id in self._paused_sessions
        session_state = self._session_state(session_id)
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
        if request.session_id in self._paused_sessions:
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

    async def _run_snapshot_job(self, session_id: str, job: CuratorJob) -> bool:
        before = job.snapshot_reader(session_id)
        await job.runner(session_id)
        after = job.snapshot_reader(session_id)
        return before != after

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
            if session_id in self._paused_sessions:
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
            try:
                await self._emit_event(
                    request,
                    "curator.started",
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
                        "curator.job.started",
                        {
                            "status": "running",
                            "job": job.key,
                            "label": job.label,
                            "index": index,
                            "total_jobs": len(job_keys),
                            "message": f"Running curator job: {job.label}.",
                        },
                    )
                    changed = await self._run_snapshot_job(session_id, job)
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
                            "curator.job.completed",
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
                            "curator.job.skipped",
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
                    "curator.completed",
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
                    started_at=started_at,
                    duration_seconds=(datetime.now(timezone.utc) - started_at).total_seconds(),
                    jobs=job_keys,
                    changed=changed_keys,
                    summary=summary,
                )
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                runtime_state = self._runtime_state.get(session_id) or {}
                await self._emit_event(
                    request,
                    "curator.failed",
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
