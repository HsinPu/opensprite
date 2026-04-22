"""Shell execution tool."""

import asyncio
import re
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


# ---------------------------------------------------------------------------
# Bash `A && B &` compound-background rewrite
# Adapted from hermes-agent/tools/terminal_tool.py::_rewrite_compound_background
# (same project author; keeps `&&` / `||` semantics while avoiding subshell-wait).
# ---------------------------------------------------------------------------


def _read_shell_token(command: str, start: int) -> tuple[str, int]:
    """Read one shell token, preserving quotes/escapes, starting at *start*."""
    i = start
    n = len(command)

    while i < n:
        ch = command[i]
        if ch.isspace() or ch in ";|&()":
            break
        if ch == "'":
            i += 1
            while i < n and command[i] != "'":
                i += 1
            if i < n:
                i += 1
            continue
        if ch == '"':
            i += 1
            while i < n:
                inner = command[i]
                if inner == "\\" and i + 1 < n:
                    i += 2
                    continue
                if inner == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        i += 1

    return command[start:i], i


def _rewrite_compound_background(command: str) -> str:
    """Wrap `A && B &` (or `A || B &`) to `A && { B & }` at depth 0.

    Bash parses ``A && B &`` with `&&` tighter than `&`, so it forks a
    subshell for the whole `A && B` compound and backgrounds it. Inside
    the subshell, `B` runs foreground, so the subshell waits for `B` to
    finish. When `B` is long-running, the subshell never exits and can
    hold stdout/stderr pipes open.

    Rewriting the tail to `A && { B & }` preserves `&&`'s error semantics
    while replacing the subshell with a brace group.
    """
    n = len(command)
    i = 0
    paren_depth = 0
    brace_depth = 0
    last_chain_op_end = -1
    rewrites: list[tuple[int, int]] = []

    while i < n:
        ch = command[i]

        if ch == "\n" and paren_depth == 0 and brace_depth == 0:
            last_chain_op_end = -1
            i += 1
            continue

        if ch.isspace():
            i += 1
            continue

        if ch == "#":
            nl = command.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue

        if ch == "\\" and i + 1 < n:
            i += 2
            continue

        if ch in ("'", '"'):
            _, next_i = _read_shell_token(command, i)
            i = max(next_i, i + 1)
            continue

        if ch == "(":
            paren_depth += 1
            i += 1
            continue

        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue

        if ch == "{" and i + 1 < n and (command[i + 1].isspace() or command[i + 1] == "\n"):
            brace_depth += 1
            i += 1
            continue
        if ch == "}" and brace_depth > 0:
            brace_depth -= 1
            last_chain_op_end = -1
            i += 1
            continue

        if paren_depth > 0 or brace_depth > 0:
            i += 1
            continue

        if command.startswith("&&", i) or command.startswith("||", i):
            last_chain_op_end = i + 2
            i += 2
            continue

        if ch == ";":
            last_chain_op_end = -1
            i += 1
            continue

        if ch == "|":
            last_chain_op_end = -1
            i += 1
            continue

        if ch == "&":
            if i + 1 < n and command[i + 1] == ">":
                i += 2
                continue
            j = i - 1
            while j >= 0 and command[j].isspace():
                j -= 1
            if j >= 0 and command[j] in "<>":
                i += 1
                continue
            if last_chain_op_end >= 0:
                rewrites.append((last_chain_op_end, i))
            last_chain_op_end = -1
            i += 1
            continue

        _, next_i = _read_shell_token(command, i)
        i = max(next_i, i + 1)

    if not rewrites:
        return command

    result = command
    for chain_end, amp_pos in reversed(rewrites):
        insert_pos = chain_end
        while insert_pos < amp_pos and result[insert_pos].isspace():
            insert_pos += 1
        prefix = result[:insert_pos]
        middle = result[insert_pos:amp_pos]
        suffix = result[amp_pos + 1 :]
        result = prefix + "{ " + middle + "& }" + suffix

    return result


# ---------------------------------------------------------------------------
# Foreground guardrails (aligned with hermes-agent terminal_tool policy)
# ---------------------------------------------------------------------------

_SHELL_LEVEL_BACKGROUND_RE = re.compile(r"\b(?:nohup|disown|setsid)\b", re.IGNORECASE)
_INLINE_BACKGROUND_AMP_RE = re.compile(r"\s&\s")
_TRAILING_BACKGROUND_AMP_RE = re.compile(r"\s&\s*(?:#.*)?$")
_LONG_LIVED_FOREGROUND_PATTERNS = (
    re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|watch)\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+compose\s+up\b", re.IGNORECASE),
    re.compile(r"\bnext\s+dev\b", re.IGNORECASE),
    re.compile(r"\bvite(?:\s|$)", re.IGNORECASE),
    re.compile(r"\bnodemon\b", re.IGNORECASE),
    re.compile(r"\buvicorn\b", re.IGNORECASE),
    re.compile(r"\bgunicorn\b", re.IGNORECASE),
    re.compile(r"\bpython(?:3)?\s+-m\s+http\.server\b", re.IGNORECASE),
)


def _looks_like_help_or_version_command(command: str) -> bool:
    """Return True for informational invocations that should never be blocked."""
    normalized = " ".join(command.lower().split())
    return (
        " --help" in normalized
        or normalized.endswith(" -h")
        or " --version" in normalized
        or normalized.endswith(" -v")
    )


def _foreground_exec_guidance(command: str) -> str | None:
    """Return a human-readable reason to refuse exec, or None if allowed."""
    if _looks_like_help_or_version_command(command):
        return None

    if _SHELL_LEVEL_BACKGROUND_RE.search(command):
        return (
            "exec cannot run commands that use nohup, disown, or setsid as shell-level "
            "background wrappers. Start long-lived processes outside exec (host service, "
            "tmux, systemd, or another terminal), then use exec only for short checks."
        )

    if _INLINE_BACKGROUND_AMP_RE.search(command) or _TRAILING_BACKGROUND_AMP_RE.search(command):
        return (
            "exec cannot mix shell background '&' with this tool's captured stdout/stderr "
            "(the subprocess would hang or lose output). Start the server outside exec "
            "with logs redirected to a file, then run curl/tests in a separate exec call."
        )

    for pattern in _LONG_LIVED_FOREGROUND_PATTERNS:
        if pattern.search(command):
            return (
                "This command looks like it starts a long-lived dev server or watcher; "
                "exec is meant for short foreground commands. Start it outside OpenSprite, "
                "then verify with a separate short exec (curl, wget, etc.)."
            )

    return None


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

        if not _looks_like_help_or_version_command(command):
            guidance = _foreground_exec_guidance(command)
            if guidance is not None:
                return f"Error: {guidance}"

        command = _rewrite_compound_background(command)

        try:
            workspace = self._get_workspace()
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
            # pipes (e.g. a stray `&` in a subshell), EOF may never arrive. Cap draining.
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
                    "stdout/stderr as the shell. Redirect long-running servers to a file or "
                    "/dev/null, or run them outside exec."
                )

            stdout = b"".join(stdout_chunks)
            stderr = b"".join(stderr_chunks)

            return self._format_output(stdout, stderr)
        except Exception as e:
            return f"Error executing command: {str(e)}"
