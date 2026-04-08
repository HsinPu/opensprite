"""Per-session cron service for scheduled OpenSprite tasks."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from croniter import croniter
from loguru import logger

from .types import CronJob, CronJobState, CronPayload, CronRunRecord, CronSchedule, CronStore


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute the next run time in milliseconds."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            base_dt = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo)
            return int(croniter(schedule.expr, base_dt).get_next(datetime).timestamp() * 1000)
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """Validate schedule fields before a job is created."""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(schedule.tz)
        except Exception as exc:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from exc


class CronService:
    """Manage scheduled jobs stored in one jobs.json file."""

    _MAX_RUN_HISTORY = 20

    def __init__(
        self,
        store_path: Path,
        *,
        session_chat_id: str = "",
        on_job: Callable[[CronJob], Awaitable[str | None]] | None = None,
    ):
        self.store_path = Path(store_path)
        self.session_chat_id = session_chat_id
        self.on_job = on_job
        self._store: CronStore | None = None
        self._last_mtime: float = 0.0
        self._timer_task: asyncio.Task | None = None
        self._running = False

    def _load_store(self) -> CronStore:
        """Load jobs from disk and reload when the store changes externally."""
        if self._store and self.store_path.exists():
            mtime = self.store_path.stat().st_mtime
            if mtime != self._last_mtime:
                logger.info("Cron: jobs.json modified externally, reloading")
                self._store = None
        if self._store is not None:
            return self._store

        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                jobs = []
                for item in data.get("jobs", []):
                    jobs.append(
                        CronJob(
                            id=item["id"],
                            name=item["name"],
                            enabled=item.get("enabled", True),
                            schedule=CronSchedule(
                                kind=item["schedule"]["kind"],
                                at_ms=item["schedule"].get("atMs"),
                                every_ms=item["schedule"].get("everyMs"),
                                expr=item["schedule"].get("expr"),
                                tz=item["schedule"].get("tz"),
                            ),
                            payload=CronPayload(
                                message=item["payload"].get("message", ""),
                                deliver=item["payload"].get("deliver", False),
                                channel=item["payload"].get("channel"),
                                chat_id=item["payload"].get("chatId"),
                            ),
                            state=CronJobState(
                                next_run_at_ms=item.get("state", {}).get("nextRunAtMs"),
                                last_run_at_ms=item.get("state", {}).get("lastRunAtMs"),
                                last_status=item.get("state", {}).get("lastStatus"),
                                last_error=item.get("state", {}).get("lastError"),
                                run_history=[
                                    CronRunRecord(
                                        run_at_ms=record["runAtMs"],
                                        status=record["status"],
                                        duration_ms=record.get("durationMs", 0),
                                        error=record.get("error"),
                                    )
                                    for record in item.get("state", {}).get("runHistory", [])
                                ],
                            ),
                            created_at_ms=item.get("createdAtMs", 0),
                            updated_at_ms=item.get("updatedAtMs", 0),
                            delete_after_run=item.get("deleteAfterRun", False),
                        )
                    )
                self._store = CronStore(
                    session_chat_id=data.get("sessionChatId", self.session_chat_id),
                    jobs=jobs,
                )
            except Exception as exc:
                logger.warning("Failed to load cron store '{}': {}", self.store_path, exc)
                self._store = CronStore(session_chat_id=self.session_chat_id)
        else:
            self._store = CronStore(session_chat_id=self.session_chat_id)

        if not self._store.session_chat_id:
            self._store.session_chat_id = self.session_chat_id

        return self._store

    def _save_store(self) -> None:
        """Persist jobs to disk."""
        if self._store is None:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "version": self._store.version,
            "sessionChatId": self._store.session_chat_id,
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "enabled": job.enabled,
                    "schedule": {
                        "kind": job.schedule.kind,
                        "atMs": job.schedule.at_ms,
                        "everyMs": job.schedule.every_ms,
                        "expr": job.schedule.expr,
                        "tz": job.schedule.tz,
                    },
                    "payload": {
                        "message": job.payload.message,
                        "deliver": job.payload.deliver,
                        "channel": job.payload.channel,
                        "chatId": job.payload.chat_id,
                    },
                    "state": {
                        "nextRunAtMs": job.state.next_run_at_ms,
                        "lastRunAtMs": job.state.last_run_at_ms,
                        "lastStatus": job.state.last_status,
                        "lastError": job.state.last_error,
                        "runHistory": [
                            {
                                "runAtMs": record.run_at_ms,
                                "status": record.status,
                                "durationMs": record.duration_ms,
                                "error": record.error,
                            }
                            for record in job.state.run_history
                        ],
                    },
                    "createdAtMs": job.created_at_ms,
                    "updatedAtMs": job.updated_at_ms,
                    "deleteAfterRun": job.delete_after_run,
                }
                for job in self._store.jobs
            ],
        }
        self.store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._last_mtime = self.store_path.stat().st_mtime

    async def start(self) -> None:
        """Start the cron timer loop."""
        self._running = True
        self._load_store()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()

    def stop(self) -> None:
        """Stop the cron timer loop."""
        self._running = False
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        if self._store is None:
            return
        now_ms = _now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now_ms)

    def _get_next_wake_ms(self) -> int | None:
        if self._store is None:
            return None
        times = [job.state.next_run_at_ms for job in self._store.jobs if job.enabled and job.state.next_run_at_ms]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        if self._timer_task is not None:
            self._timer_task.cancel()

        next_wake = self._get_next_wake_ms()
        if not self._running or not next_wake:
            return

        delay_s = max(0, next_wake - _now_ms()) / 1000

        async def tick() -> None:
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        self._load_store()
        if self._store is None:
            return

        now_ms = _now_ms()
        due_jobs = [
            job
            for job in self._store.jobs
            if job.enabled and job.state.next_run_at_ms and now_ms >= job.state.next_run_at_ms
        ]
        for job in due_jobs:
            await self._execute_job(job)

        self._save_store()
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)
        try:
            if self.on_job is not None:
                await self.on_job(job)
            job.state.last_status = "ok"
            job.state.last_error = None
        except Exception as exc:
            job.state.last_status = "error"
            job.state.last_error = str(exc)
            logger.error("Cron: job '{}' failed: {}", job.name, exc)

        end_ms = _now_ms()
        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = end_ms
        job.state.run_history.append(
            CronRunRecord(
                run_at_ms=start_ms,
                status=job.state.last_status or "error",
                duration_ms=end_ms - start_ms,
                error=job.state.last_error,
            )
        )
        job.state.run_history = job.state.run_history[-self._MAX_RUN_HISTORY :]

        if job.schedule.kind == "at":
            if job.delete_after_run and self._store is not None:
                self._store.jobs = [existing for existing in self._store.jobs if existing.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        store = self._load_store()
        jobs = store.jobs if include_disabled else [job for job in store.jobs if job.enabled]
        return sorted(jobs, key=lambda job: job.state.next_run_at_ms or float("inf"))

    def get_job(self, job_id: str) -> CronJob | None:
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                return job
        return None

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        *,
        deliver: bool = False,
        channel: str | None = None,
        chat_id: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        store = self._load_store()
        _validate_schedule_for_add(schedule)
        now_ms = _now_ms()
        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                message=message,
                deliver=deliver,
                channel=channel,
                chat_id=chat_id,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now_ms)),
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            delete_after_run=delete_after_run,
        )
        store.jobs.append(job)
        self._save_store()
        self._arm_timer()
        return job

    def remove_job(self, job_id: str) -> bool:
        store = self._load_store()
        before = len(store.jobs)
        store.jobs = [job for job in store.jobs if job.id != job_id]
        removed = len(store.jobs) != before
        if removed:
            self._save_store()
            self._arm_timer()
        return removed

    async def run_job(self, job_id: str) -> bool:
        self._load_store()
        if self._store is None:
            return False
        job = self.get_job(job_id)
        if job is None:
            return False
        await self._execute_job(job)
        self._save_store()
        self._arm_timer()
        return True
