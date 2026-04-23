"""In-memory background session management for exec/process tools."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field

from .shell_runtime import CapturedOutputChunk, drain_process_output, format_captured_output
from ..utils.processes import terminate_process_tree


@dataclass(slots=True)
class BackgroundSession:
    """One managed background shell session."""

    session_id: str
    command: str
    cwd: str | None
    process: asyncio.subprocess.Process
    read_tasks: list[asyncio.Task[None]]
    output_chunks: list[CapturedOutputChunk]
    timeout_seconds: float
    drain_timeout: float
    started_at: float = field(default_factory=time.monotonic)
    state: str = "running"
    termination_reason: str | None = None
    exit_code: int | None = None
    output_drained: bool = True
    error: str | None = None
    watch_task: asyncio.Task[None] | None = None
    last_polled_chars: int = 0

    @property
    def pid(self) -> int:
        return self.process.pid


class BackgroundProcessManager:
    """Track background exec sessions for one agent runtime."""

    def __init__(self) -> None:
        self._sessions: dict[str, BackgroundSession] = {}

    def register_session(
        self,
        *,
        command: str,
        cwd: str | None,
        process: asyncio.subprocess.Process,
        read_tasks: list[asyncio.Task[None]],
        output_chunks: list[CapturedOutputChunk],
        timeout_seconds: float,
        drain_timeout: float,
    ) -> BackgroundSession:
        session = BackgroundSession(
            session_id=uuid.uuid4().hex[:12],
            command=command,
            cwd=cwd,
            process=process,
            read_tasks=read_tasks,
            output_chunks=output_chunks,
            timeout_seconds=max(0.001, float(timeout_seconds)),
            drain_timeout=max(0.001, float(drain_timeout)),
        )
        session.watch_task = asyncio.create_task(self._watch_session(session))
        self._sessions[session.session_id] = session
        return session

    async def _watch_session(self, session: BackgroundSession) -> None:
        try:
            await asyncio.wait_for(session.process.wait(), timeout=session.timeout_seconds)
            if session.termination_reason is None:
                session.termination_reason = "exit"
        except asyncio.TimeoutError:
            if session.termination_reason is None:
                session.termination_reason = "timeout"
            await terminate_process_tree(session.process, wait_timeout=session.drain_timeout)
        except asyncio.CancelledError:
            if session.termination_reason is None:
                session.termination_reason = "cancelled"
            await terminate_process_tree(session.process, wait_timeout=session.drain_timeout)
            raise
        except Exception as exc:
            session.error = str(exc)
            if session.termination_reason is None:
                session.termination_reason = "error"
            await terminate_process_tree(session.process, wait_timeout=session.drain_timeout)
        finally:
            session.output_drained = await drain_process_output(
                session.read_tasks,
                timeout=session.drain_timeout,
            )
            session.exit_code = session.process.returncode
            session.state = "exited"

    async def _settle_session(self, session: BackgroundSession) -> BackgroundSession:
        watch_task = session.watch_task
        if watch_task is not None and (watch_task.done() or session.process.returncode is not None):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watch_task
        return session

    async def list_sessions(self) -> list[BackgroundSession]:
        sessions = sorted(self._sessions.values(), key=lambda session: session.started_at)
        for session in sessions:
            await self._settle_session(session)
        return sessions

    async def get_session(self, session_id: str) -> BackgroundSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return await self._settle_session(session)

    async def poll_session(self, session_id: str) -> tuple[BackgroundSession, str] | None:
        session = await self.get_session(session_id)
        if session is None:
            return None

        full_output = format_captured_output(
            session.output_chunks,
            max_chars=None,
            empty_placeholder="",
        )
        if session.last_polled_chars > len(full_output):
            session.last_polled_chars = 0

        new_output = full_output[session.last_polled_chars :]
        session.last_polled_chars = len(full_output)

        if not full_output and session.last_polled_chars == 0:
            new_output = "(no output)"
        elif not new_output:
            new_output = "(no new output)"

        return session, new_output

    async def kill_session(self, session_id: str) -> BackgroundSession | None:
        session = await self.get_session(session_id)
        if session is None:
            return None

        if session.state == "running":
            if session.termination_reason is None:
                session.termination_reason = "killed"
            await terminate_process_tree(session.process, wait_timeout=session.drain_timeout)
            if session.watch_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await session.watch_task

        return session

    @staticmethod
    def render_output(
        session: BackgroundSession,
        *,
        max_chars: int | None = 3000,
        empty_placeholder: str = "(no output)",
    ) -> str:
        return format_captured_output(
            session.output_chunks,
            max_chars=max_chars,
            empty_placeholder=empty_placeholder,
        )
