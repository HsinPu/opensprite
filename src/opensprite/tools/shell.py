"""Shell execution tool."""

import asyncio
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


WorkspaceResolver = Callable[[], Path]


def _resolve_workspace_root(workspace: Path) -> Path:
    """Resolve and ensure the workspace root directory exists."""
    root = Path(workspace).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_workspace_resolver(
    workspace: Path | None = None,
    workspace_resolver: WorkspaceResolver | None = None,
) -> WorkspaceResolver:
    """Build a normalized workspace resolver."""
    if workspace_resolver is not None:
        return lambda: _resolve_workspace_root(workspace_resolver())

    if workspace is None:
        raise ValueError("workspace or workspace_resolver is required")

    root = _resolve_workspace_root(workspace)
    return lambda: root


def _strip_shell_line_background_ampersand(stripped: str) -> tuple[str, bool]:
    """
    Detect a single shell line that ends in a background job `&` (not `&&`).

    Returns (text_before_trailing_ampersand, is_background_line).
    """
    if not stripped or stripped.startswith("#"):
        return stripped, False
    if stripped.endswith("&&"):
        return stripped, False
    if not stripped.endswith("&"):
        return stripped, False
    return stripped[:-1].rstrip(), True


def _rewrite_background_command(command: str) -> str:
    """
    On Unix, rewrite lines that end with shell background `&` so the job does not
    inherit the exec tool's stdout/stderr pipes (avoids hung pipe drain).

    Uses `setsid` when available, otherwise `sh -c`, with stdin/stdout/stderr
    attached to /dev/null for the detached wrapper; foreground lines are unchanged.
    """
    if sys.platform == "win32":
        return command

    lines = command.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        if line.endswith("\r\n"):
            nl, core = "\r\n", line[:-2]
        elif line.endswith("\n"):
            nl, core = "\n", line[:-1]
        else:
            nl, core = "", line

        stripped = core.strip()
        inner, is_bg = _strip_shell_line_background_ampersand(stripped)
        if not is_bg or not inner.strip():
            out.append(line)
            continue

        lead = core[: len(core) - len(core.lstrip())]
        inner_q = shlex.quote(inner)
        setsid_path = shutil.which("setsid")
        if setsid_path:
            wrapped = f"{shlex.quote(setsid_path)} sh -c {inner_q} </dev/null >/dev/null 2>&1 &"
        else:
            wrapped = f"sh -c {inner_q} </dev/null >/dev/null 2>&1 &"
        out.append(f"{lead}{wrapped}{nl}")

    return "".join(out)


class ExecTool(Tool):
    """Tool to execute shell commands."""

    MAX_COMMAND_LENGTH = 2000

    # Dangerous command patterns that are blocked
    DENY_PATTERNS = [
        r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
        r"\bdel\s+/[fq]\b",              # del /f, del /q
        r"\berase\s+/(?:[fq]|qf)\b",     # erase /f, erase /q
        r"\brmdir\s+/s\b",               # rmdir /s
        r"\bremove-item\b.*(?:-recurse|-force)",  # powershell recursive delete
        r"\bgit\s+clean\b(?:[^\n]*\s)?-[^-\n]*f",  # git clean -f / -fd / -fdx
        r"\bgit\s+reset\s+--hard\b",    # destructive git reset
        r"(?:^|[;&|]\s*)format\b",       # format
        r"\b(mkfs|diskpart)\b",          # disk operations
        r"\bdd\s+if=",                   # dd
        r">\s*/dev/sd",                  # write to disk
        r"\b(shutdown|reboot|poweroff)\b",  # system power
        r":\(\)\s*\{.*\};\s*:",          # fork bomb
    ]

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        timeout: int = 60,
        deny_patterns: list[str] | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)
        self.timeout = timeout
        self.deny_patterns = deny_patterns or self.DENY_PATTERNS

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @staticmethod
    async def _read_stream(stream: asyncio.StreamReader | None, chunks: list[bytes]) -> None:
        if stream is None:
            return

        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            chunks.append(chunk)

    @staticmethod
    def _format_output(stdout: bytes | None, stderr: bytes | None) -> str:
        result = []
        if stdout:
            result.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            result.append(f"[stderr] {stderr.decode('utf-8', errors='replace')}")

        output = "".join(result).strip()
        if not output:
            output = "(no output)"

        if len(output) > 3000:
            output = output[:3000] + f"\n\n... (truncated, total {len(output)} chars)"

        return output

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return (
            "Execute one shell command inside the current workspace and return its output. "
            "Always provide a non-empty 'command' string containing the full command to run."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Required. Full shell command to execute inside the current workspace.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                    "maxLength": self.MAX_COMMAND_LENGTH,
                }
            },
            "required": ["command"]
        }

    async def _execute(self, **kwargs: Any) -> str:
        command = str(kwargs["command"]).strip()
        
        # Check for dangerous patterns
        for pattern in self.deny_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"
        
        try:
            workspace = self._get_workspace()
            command = _rewrite_background_command(command)
            # Security: run in workspace directory
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(workspace)
            )
            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []
            stdout_task = asyncio.create_task(self._read_stream(process.stdout, stdout_chunks))
            stderr_task = asyncio.create_task(self._read_stream(process.stderr, stderr_chunks))
            
            try:
                await asyncio.wait_for(process.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                stdout = b"".join(stdout_chunks)
                stderr = b"".join(stderr_chunks)
                output = self._format_output(stdout, stderr)
                return (
                    f"Error: Command timed out after {self.timeout}s. "
                    "The command may be waiting for interactive input or may be stuck. "
                    f"Partial output before timeout:\n{output}"
                )

            # If the shell exited but a background child still inherits stdout/stderr
            # pipes (e.g. `uvicorn ... &`), EOF never arrives and gather would hang
            # forever. Cap post-exit pipe draining.
            drain_timeout = max(5, min(30, self.timeout))
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task),
                    timeout=drain_timeout,
                )
            except asyncio.TimeoutError:
                for t in (stdout_task, stderr_task):
                    t.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                stdout = b"".join(stdout_chunks)
                stderr = b"".join(stderr_chunks)
                output = self._format_output(stdout, stderr)
                return (
                    f"{output}\n\n"
                    f"[exec] Warning: output pipes did not close within {drain_timeout}s after "
                    "the shell exited. A background process may still be writing to the same "
                    "stdout/stderr as the shell (e.g. `server &` without redirect). Redirect "
                    "long-running servers to a file or /dev/null, or run them in a separate step."
                )

            stdout = b"".join(stdout_chunks)
            stderr = b"".join(stderr_chunks)

            return self._format_output(stdout, stderr)
        except Exception as e:
            return f"Error executing command: {str(e)}"
