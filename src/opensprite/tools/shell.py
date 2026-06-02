"""Shell execution tool."""

import ast
import asyncio
import re
from pathlib import Path
from typing import Any, Callable

from .base import Tool
from .process_runtime import BackgroundProcessManager, BackgroundSession, SessionExitNotifier
from .result_status import tool_error_result
from .shell_runtime import (
    CapturedOutputChunk,
    drain_process_output,
    format_captured_output,
    start_shell_process,
)
from .validation import NON_EMPTY_STRING_PATTERN
from ..utils.processes import terminate_process_tree


WorkspaceResolver = Callable[[], Path]
BackgroundNotificationFactory = Callable[[], SessionExitNotifier | None]
BackgroundSessionOwnerFactory = Callable[[], dict[str, str | None] | None]


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


def _has_shell_background_operator(command: str) -> bool:
    """Return True when the command uses shell backgrounding with `&`."""
    i = 0
    n = len(command)

    while i < n:
        ch = command[i]

        if ch.isspace():
            i += 1
            continue

        if ch == "#":
            nl = command.find("\n", i)
            if nl == -1:
                return False
            i = nl + 1
            continue

        if ch == "\\" and i + 1 < n:
            i += 2
            continue

        if ch in ("'", '"'):
            _, next_i = _read_shell_token(command, i)
            i = max(next_i, i + 1)
            continue

        if ch == "&":
            next_ch = command[i + 1] if i + 1 < n else ""
            if next_ch in {"&", ">"}:
                i += 2
                continue

            j = i - 1
            while j >= 0 and command[j].isspace():
                j -= 1
            if j >= 0 and command[j] in "<>":
                i += 1
                continue

            return True

        i += 1

    return False


# ---------------------------------------------------------------------------
# Foreground guardrails (aligned with hermes-agent terminal_tool policy)
# ---------------------------------------------------------------------------

_DANGEROUS_COMMAND_ERROR_PREFIX = "Error: Command blocked by safety guard"
_DANGEROUS_COMMAND_ERROR = f"{_DANGEROUS_COMMAND_ERROR_PREFIX}: dangerous pattern detected"
_SHELL_LEVEL_BACKGROUND_RE = re.compile(r"\b(?:nohup|disown|setsid)\b", re.IGNORECASE)
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
_BACKGROUND_WRAPPER_GUIDANCE = (
    "exec cannot run commands that use nohup, disown, or setsid as shell-level "
    "background wrappers. Use exec with background=true or yield_ms instead so OpenSprite "
    "can keep the session managed and inspectable."
)
_BACKGROUND_OPERATOR_GUIDANCE = (
    "exec cannot mix shell background '&' with this tool's captured stdout/stderr "
    "(the subprocess would hang or lose output). Use exec with background=true or "
    "yield_ms instead of shell '&' so the session stays managed."
)
_LONG_LIVED_FOREGROUND_GUIDANCE = (
    "This command looks like it starts a long-lived dev server or watcher; "
    "exec is meant for short foreground commands. If you want OpenSprite to keep tracking "
    "it, run the command with background=true or yield_ms and then inspect it with process."
)


def _strip_shell_quotes(token: str) -> str:
    token = str(token or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        token = token[1:-1]
    return token.strip()


def _command_basename(token: str) -> str:
    token = _strip_shell_quotes(token).replace("\\", "/")
    return token.rsplit("/", 1)[-1].lower()


def _shell_tokens(command: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch.isspace() or ch in ";|&()":
            i += 1
            continue
        token, next_i = _read_shell_token(command, i)
        if token:
            tokens.append(_strip_shell_quotes(token))
        i = max(next_i, i + 1)
    return tokens


def _shell_segments(command: str) -> list[str]:
    segments: list[str] = []
    start = 0
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch in {"'", '"'}:
            _, i = _read_shell_token(command, i)
            continue
        if ch in ";|&":
            segment = command[start:i].strip()
            if segment:
                segments.append(segment)
            if ch == "&" and i + 1 < n and command[i + 1] == "&":
                i += 2
            else:
                i += 1
            start = i
            continue
        i += 1
    tail = command[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def _is_rm_recursive_or_forced(tokens: list[str], index: int) -> bool:
    flags = [token.lower() for token in tokens[index + 1 :]]
    for flag in flags:
        if flag in {"-recurse", "-recursive", "--recursive", "-force", "--force"}:
            return True
        if flag.startswith("-") and "r" in flag[1:]:
            return True
    return False


def _has_windows_delete_flags(tokens: list[str], index: int) -> bool:
    flags = {token.lower() for token in tokens[index + 1 :]}
    return bool(flags & {"/f", "/q", "/s"})


def _dangerous_command_error(reason: str | None = None) -> str:
    detail = str(reason or "").strip() or "dangerous pattern detected"
    return f"{_DANGEROUS_COMMAND_ERROR_PREFIX}: {detail}"


def _reason_from_nested_command(prefix: str, nested: str) -> str | None:
    reason = classify_destructive_shell_command(nested)
    if reason is None:
        return None
    return f"{prefix} -> {reason}"


def _first_inline_arg_after_flag(tokens: list[str], lowered: list[str], index: int, flags: set[str]) -> str | None:
    for flag_index in range(index + 1, len(lowered)):
        flag = lowered[flag_index]
        if flag in flags and flag_index + 1 < len(tokens):
            return tokens[flag_index + 1]
    return None


def _shell_wrapper_inline_command(tokens: list[str], lowered: list[str], index: int) -> str | None:
    for flag_index in range(index + 1, len(lowered)):
        flag = lowered[flag_index]
        if flag.startswith("-") and "c" in flag.lstrip("-") and flag_index + 1 < len(tokens):
            return tokens[flag_index + 1]
    return None


def _python_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _python_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _python_constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _python_string_sequence(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for item in node.elts:
        value = _python_constant_string(item)
        if value is None:
            return None
        values.append(value)
    return values


def _python_keyword_is_true(call: ast.Call, name: str) -> bool:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value is True
    return False


def _classify_python_inline_code(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    subprocess_calls = {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "Popen",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _python_call_name(node.func)
        if call_name == "os.system" and node.args:
            command = _python_constant_string(node.args[0])
            if command and (reason := _reason_from_nested_command("python -c os.system", command)):
                return reason
            continue
        if call_name not in subprocess_calls or not node.args:
            continue
        command = _python_constant_string(node.args[0])
        if command and _python_keyword_is_true(node, "shell"):
            if reason := _reason_from_nested_command("python -c subprocess shell", command):
                return reason
        sequence = _python_string_sequence(node.args[0])
        if sequence:
            command = " ".join(sequence)
            if reason := _reason_from_nested_command("python -c subprocess argv", command):
                return reason
    return None


def _read_quoted_js_string(code: str, start: int) -> tuple[str | None, int]:
    quote = code[start]
    if quote not in {"'", '"'}:
        return None, start
    chars: list[str] = []
    i = start + 1
    while i < len(code):
        ch = code[i]
        if ch == "\\" and i + 1 < len(code):
            chars.append(code[i + 1])
            i += 2
            continue
        if ch == quote:
            return "".join(chars), i + 1
        chars.append(ch)
        i += 1
    return None, i


def _skip_js_string_or_comment(code: str, index: int) -> int | None:
    ch = code[index]
    if ch in {"'", '"'}:
        _, next_index = _read_quoted_js_string(code, index)
        return max(next_index, index + 1)
    if ch == "`":
        i = index + 1
        while i < len(code):
            if code[i] == "\\" and i + 1 < len(code):
                i += 2
                continue
            if code[i] == "`":
                return i + 1
            i += 1
        return i
    if code.startswith("//", index):
        newline = code.find("\n", index + 2)
        return len(code) if newline == -1 else newline + 1
    if code.startswith("/*", index):
        end = code.find("*/", index + 2)
        return len(code) if end == -1 else end + 2
    return None


def _classify_node_inline_code(code: str) -> str | None:
    i = 0
    while i < len(code):
        skipped = _skip_js_string_or_comment(code, i)
        if skipped is not None:
            i = skipped
            continue
        for function_name in ("execSync", "exec"):
            if not code.startswith(function_name, i):
                continue
            before = code[i - 1] if i > 0 else ""
            after_index = i + len(function_name)
            after = code[after_index] if after_index < len(code) else ""
            if before and (before.isalnum() or before in {"_", "$"}):
                continue
            if after and (after.isalnum() or after in {"_", "$"}):
                continue
            j = after_index
            while j < len(code) and code[j].isspace():
                j += 1
            if j >= len(code) or code[j] != "(":
                continue
            j += 1
            while j < len(code) and code[j].isspace():
                j += 1
            if j >= len(code) or code[j] not in {"'", '"'}:
                continue
            command, _ = _read_quoted_js_string(code, j)
            if command and (reason := _reason_from_nested_command(f"node -e {function_name}", command)):
                return reason
        i += 1
    return None


def classify_destructive_shell_command(command: str) -> str | None:
    """Return a stable reason when a shell command is unambiguously destructive."""
    segments = _shell_segments(command)
    if len(segments) > 1:
        for segment in segments:
            if reason := classify_destructive_shell_command(segment):
                return reason
        return None

    tokens = _shell_tokens(command)
    lowered = [token.lower() for token in tokens]
    if not lowered:
        return None
    if lowered[0] in {"echo", "printf"}:
        return None

    basenames = [_command_basename(token) for token in tokens]

    for index, token in enumerate(basenames):
        if token in {"cmd", "cmd.exe"} and index + 2 < len(lowered) and lowered[index + 1] in {"/c", "/k"}:
            nested = " ".join(tokens[index + 2 :])
            if reason := _reason_from_nested_command("cmd /c", nested):
                return reason
        if token in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
            for flag_index in range(index + 1, len(lowered)):
                if lowered[flag_index] in {"-command", "-c", "/command", "/c"} and flag_index + 1 < len(tokens):
                    nested = " ".join(tokens[flag_index + 1 :])
                    if reason := _reason_from_nested_command("powershell -Command", nested):
                        return reason
        if token in {"bash", "bash.exe", "sh", "sh.exe", "zsh", "zsh.exe", "dash", "dash.exe"}:
            nested = _shell_wrapper_inline_command(tokens, lowered, index)
            if nested and (reason := _reason_from_nested_command(f"{token} -c", nested)):
                return reason
        if token in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
            code = _first_inline_arg_after_flag(tokens, lowered, index, {"-c"})
            if code and (reason := _classify_python_inline_code(code)):
                return reason
        if token in {"node", "node.exe"}:
            code = _first_inline_arg_after_flag(tokens, lowered, index, {"-e", "--eval"})
            if code and (reason := _classify_node_inline_code(code)):
                return reason

    for index, token in enumerate(lowered):
        if token == "git" and index + 2 < len(lowered):
            subcommand = lowered[index + 1]
            args = lowered[index + 2 :]
            if subcommand == "reset" and "--hard" in args:
                return "git reset --hard"
            if subcommand == "clean":
                clean_flags = [arg for arg in args if arg.startswith("-")]
                if any("f" in flag.lstrip("-") for flag in clean_flags):
                    return "git clean force"

        if token in {"rm", "remove-item"} and _is_rm_recursive_or_forced(lowered, index):
            return f"{token} recursive/forced delete"
        if token in {"del", "erase", "rmdir"} and _has_windows_delete_flags(lowered, index):
            return f"{token} forced delete"
        if token in {"format", "format.com", "diskpart", "mkfs", "shutdown", "reboot", "poweroff"}:
            return token
        if token == "dd" and any(arg.startswith("if=") for arg in lowered[index + 1 :]):
            return "dd raw disk copy"

    return None


def _looks_like_help_or_version_command(command: str) -> bool:
    """Return True for informational invocations that should never be blocked."""
    normalized = " ".join(command.lower().split())
    return (
        " --help" in normalized
        or normalized.endswith(" -h")
        or " --version" in normalized
        or normalized.endswith(" -v")
    )


def _foreground_exec_violation(command: str, *, allow_long_lived: bool) -> str | None:
    """Return the foreground-exec policy violation for a command, if any."""
    if _SHELL_LEVEL_BACKGROUND_RE.search(command):
        return _BACKGROUND_WRAPPER_GUIDANCE

    if _has_shell_background_operator(command):
        return _BACKGROUND_OPERATOR_GUIDANCE

    if not allow_long_lived and any(pattern.search(command) for pattern in _LONG_LIVED_FOREGROUND_PATTERNS):
        return _LONG_LIVED_FOREGROUND_GUIDANCE

    return None


def _foreground_exec_guidance(command: str, *, allow_long_lived: bool = False) -> str | None:
    """Return a human-readable reason to refuse exec, or None if allowed."""
    if _looks_like_help_or_version_command(command):
        return None

    return _foreground_exec_violation(command, allow_long_lived=allow_long_lived)


def _build_background_session_result(
    session: BackgroundSession,
    output: str,
    *,
    yield_ms: int | None,
) -> str:
    """Build the response returned when exec moves a command into the background."""
    if yield_ms is None:
        heading = "Background session started."
    else:
        heading = f"Command is still running after {yield_ms}ms; moved to background."

    return "\n".join(
        [
            heading,
            f"Session ID: {session.session_id}",
            f"Status: {session.state}",
            f"PID: {session.pid}",
            *(
                [f"Owner: {session.owner_session_id or '-'} / {session.owner_run_id or '-'}"]
                if session.owner_session_id or session.owner_run_id
                else []
            ),
            "Use process with action=\"poll\" to inspect it or action=\"kill\" to stop it.",
            "Current output:",
            output,
        ]
    )


def _build_timeout_result(timeout: int, output: str, *, drained: bool) -> str:
    """Build the timeout response for exec output collection."""
    if not drained:
        output += (
            "\n\n[exec] Warning: output pipes did not close promptly after timeout; "
            "a descendant process may still have inherited stdout/stderr."
        )

    return (
        f"Error: Command timed out after {timeout}s. "
        "The command may be waiting for interactive input or may be stuck. "
        f"Partial output before timeout:\n{output}"
    )


def _build_pipe_drain_warning_result(output: str, *, drain_timeout: int) -> str:
    """Build the warning shown when output pipes stay open after exit."""
    return (
        f"{output}\n\n"
        f"[exec] Warning: output pipes did not close within {drain_timeout}s after "
        "the shell exited. A background process may still be writing to the same "
        "stdout/stderr as the shell. Redirect long-running servers to a file or "
        "/dev/null, or run them outside exec."
    )


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
        process_manager: BackgroundProcessManager | None = None,
        background_notification_factory: BackgroundNotificationFactory | None = None,
        background_session_owner_factory: BackgroundSessionOwnerFactory | None = None,
        notify_on_exit: bool = True,
        notify_on_exit_empty_success: bool = False,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)
        self.timeout = timeout
        self.deny_patterns = deny_patterns or self.DENY_PATTERNS
        self.process_manager = process_manager or BackgroundProcessManager()
        self._background_notification_factory = background_notification_factory
        self._background_session_owner_factory = background_session_owner_factory
        self.notify_on_exit = notify_on_exit
        self.notify_on_exit_empty_success = notify_on_exit_empty_success

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @staticmethod
    def _output_drain_timeout(timeout_seconds: float) -> float:
        return max(5.0, min(30.0, float(timeout_seconds)))

    def _validate_command(
        self,
        command: str,
        *,
        allow_managed_background: bool,
    ) -> str | None:
        if _looks_like_help_or_version_command(command):
            return None

        destructive_reason = classify_destructive_shell_command(command)
        if destructive_reason:
            return _dangerous_command_error(destructive_reason)

        for pattern in self.deny_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return _dangerous_command_error()

        guidance = _foreground_exec_guidance(
            command,
            allow_long_lived=allow_managed_background,
        )
        if guidance is not None:
            return f"Error: {guidance}"

        return None

    async def _handle_timed_out_process(
        self,
        process: asyncio.subprocess.Process,
        read_tasks: list[asyncio.Task[None]],
        output_chunks: list[CapturedOutputChunk],
        *,
        timeout_seconds: int,
    ) -> str:
        await terminate_process_tree(process)
        drained = await drain_process_output(
            read_tasks,
            timeout=self._output_drain_timeout(timeout_seconds),
        )
        return _build_timeout_result(
            timeout_seconds,
            format_captured_output(output_chunks),
            drained=drained,
        )

    async def _handle_completed_process(
        self,
        read_tasks: list[asyncio.Task[None]],
        output_chunks: list[CapturedOutputChunk],
        *,
        timeout_seconds: int,
    ) -> str:
        drain_timeout = self._output_drain_timeout(timeout_seconds)
        drained = await drain_process_output(read_tasks, timeout=drain_timeout)
        output = format_captured_output(output_chunks)
        if not drained:
            return _build_pipe_drain_warning_result(output, drain_timeout=drain_timeout)
        return output

    def _start_background_session(
        self,
        *,
        command: str,
        workspace: Path,
        process: asyncio.subprocess.Process,
        read_tasks: list[asyncio.Task[None]],
        output_chunks: list[CapturedOutputChunk],
        session_timeout_seconds: float | None,
        drain_timeout_seconds: float,
        yield_ms: int | None,
        notify_on_exit: bool,
        notify_on_exit_empty_success: bool,
    ) -> str:
        owner = self._background_session_owner_factory() if self._background_session_owner_factory is not None else None
        if not isinstance(owner, dict):
            owner = {}
        session = self.process_manager.register_session(
            command=command,
            cwd=str(workspace),
            process=process,
            read_tasks=read_tasks,
            output_chunks=output_chunks,
            timeout_seconds=session_timeout_seconds,
            drain_timeout=self._output_drain_timeout(drain_timeout_seconds),
            exit_notifier=(
                self._background_notification_factory()
                if self._background_notification_factory is not None
                else None
            ),
            notify_on_exit=notify_on_exit,
            notify_on_exit_empty_success=notify_on_exit_empty_success,
            owner_session_id=(
                str(owner.get("session_id"))
                if owner.get("session_id") is not None
                else None
            ),
            owner_run_id=(str(owner.get("run_id")) if owner.get("run_id") is not None else None),
            owner_channel=(str(owner.get("channel")) if owner.get("channel") is not None else None),
            owner_external_chat_id=(
                str(owner.get("external_chat_id"))
                if owner.get("external_chat_id") is not None
                else None
            ),
        )
        output = self.process_manager.render_output(session, max_chars=1200)
        return _build_background_session_result(session, output, yield_ms=yield_ms)

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return (
            "Execute one shell command inside the current workspace and return its output, "
            "or move it into a managed background session when requested."
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
                },
                "background": {
                    "type": "boolean",
                    "description": "Optional. When true, start the command in a managed background session immediately.",
                },
                "yield_ms": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional. Wait this many milliseconds; if the command is still running, move it into a managed background session.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Optional. Override the foreground timeout in seconds. For background "
                        "sessions, also set a maximum runtime; omitted background sessions run "
                        "until they exit, are killed, or the agent shuts down."
                    ),
                },
                "notify_on_exit": {
                    "type": "boolean",
                    "description": "Optional. Override whether a managed background session should publish a completion notification when it exits.",
                },
                "notify_on_complete": {
                    "type": "boolean",
                    "description": "Optional Hermes-compatible alias for notify_on_exit. Prefer this for long-running jobs where the user should be notified when the command finishes.",
                },
                "notify_on_exit_empty_success": {
                    "type": "boolean",
                    "description": "Optional. Override whether successful managed background sessions with no output should still publish a completion notification.",
                }
            },
            "required": ["command"]
        }

    async def _execute(self, **kwargs: Any) -> str:
        command = str(kwargs["command"]).strip()
        background = bool(kwargs.get("background", False))
        yield_ms = kwargs.get("yield_ms")
        timeout_arg = kwargs.get("timeout_seconds")
        timeout_was_supplied = timeout_arg is not None
        timeout_seconds = int(timeout_arg if timeout_was_supplied else self.timeout)
        notify_arg = kwargs.get("notify_on_complete", kwargs.get("notify_on_exit", self.notify_on_exit))
        notify_on_exit = bool(notify_arg)
        notify_on_exit_empty_success = bool(
            kwargs.get("notify_on_exit_empty_success", self.notify_on_exit_empty_success)
        )

        validation_error = self._validate_command(
            command,
            allow_managed_background=background or yield_ms is not None,
        )
        if validation_error is not None:
            return validation_error

        process: asyncio.subprocess.Process | None = None
        read_tasks: list[asyncio.Task[None]] = []
        try:
            workspace = self._get_workspace()
            output_chunks: list[CapturedOutputChunk] = []
            process, read_tasks = await start_shell_process(
                command,
                cwd=str(workspace),
                output_chunks=output_chunks,
            )

            if background:
                return self._start_background_session(
                    command=command,
                    workspace=workspace,
                    process=process,
                    read_tasks=read_tasks,
                    output_chunks=output_chunks,
                    session_timeout_seconds=(
                        float(timeout_seconds) if timeout_was_supplied else None
                    ),
                    drain_timeout_seconds=timeout_seconds,
                    yield_ms=None,
                    notify_on_exit=notify_on_exit,
                    notify_on_exit_empty_success=notify_on_exit_empty_success,
                )

            if yield_ms is not None:
                yield_timeout_seconds = yield_ms / 1000.0
                wait_timeout = min(float(timeout_seconds), yield_timeout_seconds)
                started_at = asyncio.get_running_loop().time()
                try:
                    await asyncio.wait_for(process.wait(), timeout=wait_timeout)
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_running_loop().time() - started_at
                    if timeout_was_supplied and elapsed >= float(timeout_seconds):
                        return await self._handle_timed_out_process(
                            process,
                            read_tasks,
                            output_chunks,
                            timeout_seconds=timeout_seconds,
                        )
                    return self._start_background_session(
                        command=command,
                        workspace=workspace,
                        process=process,
                        read_tasks=read_tasks,
                        output_chunks=output_chunks,
                        session_timeout_seconds=(
                            max(0.001, float(timeout_seconds) - elapsed)
                            if timeout_was_supplied
                            else None
                        ),
                        drain_timeout_seconds=timeout_seconds,
                        yield_ms=yield_ms,
                        notify_on_exit=notify_on_exit,
                        notify_on_exit_empty_success=notify_on_exit_empty_success,
                    )

                return await self._handle_completed_process(
                    read_tasks,
                    output_chunks,
                    timeout_seconds=timeout_seconds,
                )

            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                return await self._handle_timed_out_process(
                    process,
                    read_tasks,
                    output_chunks,
                    timeout_seconds=timeout_seconds,
                )

            return await self._handle_completed_process(
                read_tasks,
                output_chunks,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            if process is not None:
                await terminate_process_tree(process)
            if read_tasks:
                await drain_process_output(
                    read_tasks,
                    timeout=self._output_drain_timeout(timeout_seconds),
                )
            raise
        except Exception as e:
            return tool_error_result(
                str(e),
                error_type="ToolExecutionError",
                metadata={"tool_name": self.name},
            )
