"""Runtime manager for per-session cron services."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from ..context.paths import get_chat_workspace
from .service import CronService
from .types import CronJob


class CronManager:
    """Manage per-session cron services under the workspace root."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        on_job: Callable[[str, CronJob], Awaitable[str | None]],
    ):
        self.workspace_root = Path(workspace_root)
        self._on_job = on_job
        self._services: dict[str, CronService] = {}
        self._lock = asyncio.Lock()

    def _jobs_path(self, session_chat_id: str) -> Path:
        return get_chat_workspace(session_chat_id, workspace_root=self.workspace_root) / "cron" / "jobs.json"

    async def _build_service(self, session_chat_id: str) -> CronService:
        async def on_job(job: CronJob) -> str | None:
            return await self._on_job(session_chat_id, job)

        service = CronService(
            self._jobs_path(session_chat_id),
            session_chat_id=session_chat_id,
            on_job=on_job,
        )
        await service.start()
        return service

    async def get_or_create_service(self, session_chat_id: str) -> CronService:
        async with self._lock:
            service = self._services.get(session_chat_id)
            if service is not None:
                return service
            service = await self._build_service(session_chat_id)
            self._services[session_chat_id] = service
            return service

    async def start(self) -> None:
        chats_root = self.workspace_root / "chats"
        if not chats_root.exists():
            return

        for jobs_path in chats_root.glob("*/*/cron/jobs.json"):
            try:
                import json

                session_chat_id = json.loads(jobs_path.read_text(encoding="utf-8")).get("sessionChatId", "")
            except Exception:
                session_chat_id = ""
            if not session_chat_id:
                continue
            await self.get_or_create_service(session_chat_id)

    async def stop(self) -> None:
        async with self._lock:
            services = list(self._services.values())
            self._services.clear()
        for service in services:
            service.stop()


__all__ = ["CronManager"]
