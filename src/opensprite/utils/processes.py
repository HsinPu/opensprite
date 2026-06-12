"""Process lifecycle helpers shared across the codebase."""

import asyncio
import contextlib
import os
import signal
import subprocess
from typing import Any


_WINDOWS_FORCE_FOLLOWUP_DELAY = 0.25


def windows_hidden_process_kwargs(*, new_process_group: bool = False) -> dict[str, Any]:
    """Return Windows subprocess kwargs that suppress console windows."""
    if os.name != "nt":
        return {}

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_process_group:
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    kwargs: dict[str, Any] = {"creationflags": creationflags}
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    return kwargs


def detached_process_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return windows_hidden_process_kwargs(new_process_group=True)
    return {"start_new_session": True}


async def _run_taskkill(pid: int, *, force: bool, wait_timeout: float) -> None:
    """Best-effort invoke taskkill for a Windows process tree."""
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")

    try:
        killer = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **windows_hidden_process_kwargs(),
        )
    except Exception:
        return

    try:
        await asyncio.wait_for(killer.wait(), timeout=wait_timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            killer.kill()
        with contextlib.suppress(Exception):
            await killer.wait()


async def _wait_for_process_exit(process: asyncio.subprocess.Process, *, wait_timeout: float) -> bool:
    """Wait for a process to exit, returning False on timeout."""
    if process.returncode is not None:
        return True

    try:
        await asyncio.wait_for(process.wait(), timeout=wait_timeout)
        return True
    except asyncio.TimeoutError:
        return process.returncode is not None


def _signal_unix_process_tree(process: asyncio.subprocess.Process, sig: int) -> None:
    """Send a Unix signal to the process group, falling back to the direct process."""
    try:
        os.killpg(process.pid, sig)
        return
    except ProcessLookupError:
        return
    except Exception:
        pass

    try:
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        return
    except Exception:
        return


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    *,
    wait_timeout: float = 5,
) -> None:
    """Best-effort terminate a process and its descendants."""
    if process.returncode is not None:
        return

    if os.name == "nt":
        await _run_taskkill(process.pid, force=False, wait_timeout=wait_timeout)
        # cmd.exe can exit before its descendants are fully gone, so keep the
        # grace window short and follow with a forced tree kill for reliability.
        settle_timeout = min(wait_timeout, _WINDOWS_FORCE_FOLLOWUP_DELAY)
        if settle_timeout > 0:
            await _wait_for_process_exit(process, wait_timeout=settle_timeout)

        await _run_taskkill(process.pid, force=True, wait_timeout=wait_timeout)
        if await _wait_for_process_exit(process, wait_timeout=wait_timeout):
            return

        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=wait_timeout)
        return

    _signal_unix_process_tree(process, signal.SIGTERM)

    if await _wait_for_process_exit(process, wait_timeout=wait_timeout):
        return

    _signal_unix_process_tree(process, signal.SIGKILL)

    if await _wait_for_process_exit(process, wait_timeout=wait_timeout):
        return

    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=wait_timeout)
