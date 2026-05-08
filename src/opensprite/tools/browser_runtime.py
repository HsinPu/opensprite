"""Runtime helpers for local browser automation tools."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any


class BrowserRuntimeError(RuntimeError):
    """Raised when browser automation cannot run."""


class AgentBrowserRuntime:
    """Small wrapper around the `agent-browser` CLI JSON interface."""

    def __init__(self, *, command_timeout: int = 30, command: str | None = None):
        self.command_timeout = max(1, int(command_timeout or 30))
        self.command = str(command or "").strip()

    async def run(self, *, session_key: str, command: str, args: list[str] | None = None, timeout: int | None = None) -> dict[str, Any]:
        argv = [
            *self._command_prefix(),
            "--session",
            _browser_session_name(session_key),
            "--json",
            command,
            *(args or []),
        ]
        return await self._run_subprocess(argv, timeout or self.command_timeout)

    def _command_prefix(self) -> list[str]:
        if self.command:
            return [self.command]

        agent_browser = shutil.which("agent-browser") or _local_agent_browser_path()
        if agent_browser:
            return [agent_browser]

        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if npx:
            return [npx, "agent-browser"]

        raise BrowserRuntimeError(
            "agent-browser CLI was not found. Install it with `npm install` in the repo root "
            "or `npm install -g agent-browser && agent-browser install`."
        )

    async def _run_subprocess(self, argv: list[str], timeout: int) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise BrowserRuntimeError(str(exc)) from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=max(1, timeout))
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"success": False, "error": f"browser command timed out after {timeout}s"}

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        payload = _parse_json_payload(stdout_text)
        if payload is not None:
            if proc.returncode and "success" not in payload:
                payload["success"] = False
            if stderr_text and "stderr" not in payload:
                payload["stderr"] = stderr_text[-1200:]
            return payload

        if proc.returncode:
            return {
                "success": False,
                "error": stderr_text or stdout_text or f"browser command exited with code {proc.returncode}",
            }
        return {"success": True, "output": stdout_text}


def _local_agent_browser_path() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    bin_dir = repo_root / "node_modules" / ".bin"
    for name in ("agent-browser.cmd", "agent-browser.exe", "agent-browser"):
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)
    return ""


def _browser_session_name(session_key: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_key or "default")).strip("_")
    return f"opensprite_{normalized or 'default'}"[:80]


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    for line in reversed([line.strip() for line in str(text or "").splitlines() if line.strip()]):
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else None
    return None
