"""Cron scheduling tool for OpenSprite."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from ..cron import CronManager, CronSchedule
from .base import Tool


class CronTool(Tool):
    """Tool to schedule reminders and recurring agent tasks."""

    def __init__(
        self,
        cron_manager: CronManager | None,
        *,
        get_chat_id: Callable[[], str | None],
        default_timezone: str = "UTC",
    ):
        self._cron_manager = cron_manager
        self._get_chat_id = get_chat_id
        self._default_timezone = default_timezone

    def set_cron_manager(self, cron_manager: CronManager) -> None:
        """Inject the runtime cron manager after tool registration."""
        self._cron_manager = cron_manager

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Manage scheduled reminders and recurring tasks for the current session. "
            "Actions: add, list, remove, pause, enable, run. "
            f"If tz is omitted, cron expressions and naive ISO times default to {self._default_timezone}."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Required. Action to perform on the current session schedule.",
                    "enum": ["add", "list", "remove", "pause", "enable", "run"],
                },
                "name": {
                    "type": "string",
                    "description": "Optional short label for the scheduled job created by add.",
                },
                "message": {
                    "type": "string",
                    "description": "Required for add. Instruction the future agent run should execute when the job triggers.",
                },
                "every_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "For add. Use this for fixed recurring intervals in seconds. Provide exactly one of every_seconds, cron_expr, or at.",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "For add. Cron expression like '0 9 * * *'. Provide exactly one of every_seconds, cron_expr, or at.",
                },
                "tz": {
                    "type": "string",
                    "description": "Optional IANA timezone for cron_expr. Ignored for other schedule types.",
                },
                "at": {
                    "type": "string",
                    "description": "For add. ISO datetime for one-time execution, e.g. 2026-02-12T10:30:00. Provide exactly one of every_seconds, cron_expr, or at.",
                },
                "deliver": {
                    "type": "boolean",
                    "description": "For add. Whether to send the future execution result back to the current chat.",
                    "default": True,
                },
                "job_id": {
                    "type": "string",
                    "description": "Required for remove, pause, enable, and run. Target scheduled job ID.",
                },
            },
            "required": ["action"],
        }

    async def _execute(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        deliver: bool = True,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            return await self._add_job(name, message, every_seconds, cron_expr, tz, at, deliver)
        if action == "list":
            return await self._list_jobs()
        if action == "remove":
            return await self._remove_job(job_id)
        if action == "pause":
            return await self._pause_job(job_id)
        if action == "enable":
            return await self._enable_job(job_id)
        if action == "run":
            return await self._run_job(job_id)
        return f"Unknown action: {action}"

    async def _get_service(self):
        if self._cron_manager is None:
            return None, "Error: cron manager is unavailable"
        chat_id = self._get_chat_id()
        if not chat_id:
            return None, "Error: no active session context"
        return await self._cron_manager.get_or_create_service(chat_id), None

    async def _add_job(
        self,
        name: str | None,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        deliver: bool,
    ) -> str:
        if not message:
            return "Error: message is required for add"

        service, error = await self._get_service()
        if error:
            return error
        assert service is not None

        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz or self._default_timezone)
        elif at:
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime format '{at}'. Expected YYYY-MM-DDTHH:MM:SS"
            if dt.tzinfo is None:
                from zoneinfo import ZoneInfo

                dt = dt.replace(tzinfo=ZoneInfo(self._default_timezone))
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        session_chat_id = self._get_chat_id() or "default"
        if ":" in session_chat_id:
            channel, chat_id = session_chat_id.split(":", 1)
        else:
            channel, chat_id = "default", session_chat_id

        try:
            job = service.add_job(
                name=name or message[:30],
                schedule=schedule,
                message=message,
                deliver=deliver,
                channel=channel,
                chat_id=chat_id,
                delete_after_run=delete_after,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Created job '{job.name}' (id: {job.id})"

    async def _list_jobs(self) -> str:
        service, error = await self._get_service()
        if error:
            return error
        assert service is not None
        jobs = service.list_jobs(include_disabled=True)
        if not jobs:
            return "No scheduled jobs."
        lines = []
        for job in jobs:
            timing = self._format_timing(job.schedule)
            line = f"- {job.name} (id: {job.id}, {timing})"
            if job.state.next_run_at_ms:
                line += f"\n  Next run: {self._format_timestamp(job.state.next_run_at_ms, job.schedule.tz or self._default_timezone)}"
            lines.append(line)
        return "Scheduled jobs:\n" + "\n".join(lines)

    async def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        service, error = await self._get_service()
        if error:
            return error
        assert service is not None
        return f"Removed job {job_id}" if service.remove_job(job_id) else f"Job {job_id} not found"

    async def _pause_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for pause"
        service, error = await self._get_service()
        if error:
            return error
        assert service is not None
        return f"Paused job {job_id}" if service.pause_job(job_id) else f"Job {job_id} not found or already paused"

    async def _enable_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for enable"
        service, error = await self._get_service()
        if error:
            return error
        assert service is not None
        return f"Enabled job {job_id}" if service.enable_job(job_id) else f"Job {job_id} not found or already enabled"

    async def _run_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for run"
        service, error = await self._get_service()
        if error:
            return error
        assert service is not None
        return f"Ran job {job_id}" if await service.run_job(job_id) else f"Job {job_id} not found"

    @staticmethod
    def _format_timestamp(ms: int, tz_name: str) -> str:
        from zoneinfo import ZoneInfo

        dt = datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz_name))
        return f"{dt.isoformat()} ({tz_name})"

    def _format_timing(self, schedule: CronSchedule) -> str:
        if schedule.kind == "cron":
            tz = f" ({schedule.tz})" if schedule.tz else ""
            return f"cron: {schedule.expr}{tz}"
        if schedule.kind == "every" and schedule.every_ms:
            if schedule.every_ms % 3_600_000 == 0:
                return f"every {schedule.every_ms // 3_600_000}h"
            if schedule.every_ms % 60_000 == 0:
                return f"every {schedule.every_ms // 60_000}m"
            if schedule.every_ms % 1000 == 0:
                return f"every {schedule.every_ms // 1000}s"
            return f"every {schedule.every_ms}ms"
        if schedule.kind == "at" and schedule.at_ms:
            return f"at {self._format_timestamp(schedule.at_ms, schedule.tz or self._default_timezone)}"
        return schedule.kind
