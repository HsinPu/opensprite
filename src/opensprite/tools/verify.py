"""Project verification tool for focused post-edit checks."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..utils.processes import terminate_process_tree
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


WorkspaceResolver = Callable[[], Path]

_EXCLUDED_PYTHON_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "venv",
    }
)


@dataclass(frozen=True)
class VerifyCommandResult:
    """Captured result from one verification subprocess."""

    command: list[str]
    cwd: Path
    exit_code: int | None
    output: str
    timed_out: bool = False


def classify_verification_result(result: str) -> dict[str, Any]:
    """Classify one verify tool result string into structured outcome fields."""
    text = str(result or "").strip()
    first_line = text.splitlines()[0].strip() if text else ""
    for prefix, status, ok in (
        ("Verification passed: ", "passed", True),
        ("Verification skipped: ", "skipped", False),
        ("Error: Verification timed out: ", "timed_out", False),
        ("Error: Verification failed: ", "failed", False),
    ):
        if first_line.startswith(prefix):
            return {
                "status": status,
                "ok": ok,
                "attempted": True,
                "name": first_line[len(prefix):].strip() or None,
            }
    if first_line.startswith("Error:"):
        return {"status": "error", "ok": False, "attempted": True, "name": None}
    return {"status": "unknown", "ok": False, "attempted": bool(text), "name": None}


def _resolve_workspace_root(workspace: Path) -> Path:
    root = Path(workspace).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_workspace_resolver(
    workspace: Path | None = None,
    workspace_resolver: WorkspaceResolver | None = None,
) -> WorkspaceResolver:
    if workspace_resolver is not None:
        return lambda: _resolve_workspace_root(workspace_resolver())
    if workspace is None:
        raise ValueError("workspace or workspace_resolver is required")
    root = _resolve_workspace_root(workspace)
    return lambda: root


def _resolve_workspace_path(workspace: Path, path: str) -> Path | None:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate


def _display_path(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _command_display(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _process_creation_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _format_streams(stdout: bytes | None, stderr: bytes | None, *, max_chars: int = 6000) -> str:
    parts: list[str] = []
    stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append("[stderr] " + stderr_text.replace("\n", "\n[stderr] "))
    output = "\n".join(parts).strip() or "(no output)"
    if len(output) > max_chars:
        return output[:max_chars] + f"\n... (truncated, total {len(output)} chars)"
    return output


class VerifyTool(Tool):
    """Run fixed verification checks for the current workspace."""

    DEFAULT_TIMEOUT = 120
    MAX_TIMEOUT = 600

    def __init__(self, workspace: Path | None = None, workspace_resolver: WorkspaceResolver | None = None):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)

    @property
    def name(self) -> str:
        return "verify"

    @property
    def description(self) -> str:
        return (
            "Run fixed project verification checks after code changes. Supports action=python_compile for syntax checks, "
            "action=pytest for Python tests, action=web_build for package.json build scripts, and action=auto for safe detected checks. "
            "Use focused pytest_args when possible. This tool does not run arbitrary shell commands."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["auto", "python_compile", "pytest", "web_build"],
                    "description": "Verification mode. Defaults to auto.",
                },
                "path": {
                    "type": "string",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                    "description": "Workspace-relative file or directory to verify. Defaults to the workspace root.",
                },
                "pytest_args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional focused pytest arguments, such as ['tests/test_file.py::test_name'].",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": self.MAX_TIMEOUT,
                    "description": "Timeout in seconds for subprocess-based checks. Defaults to 120.",
                },
            },
        }

    async def _execute(
        self,
        action: str = "auto",
        path: str = ".",
        pytest_args: list[str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        **kwargs: Any,
    ) -> str:
        workspace = self._workspace_resolver()
        target = _resolve_workspace_path(workspace, path or ".")
        if target is None:
            return f"Error: Verification path is outside the workspace: {path}"

        current_timeout = min(max(int(timeout or self.DEFAULT_TIMEOUT), 1), self.MAX_TIMEOUT)
        mode = (action or "auto").strip().lower()
        if mode == "python_compile":
            return self._verify_python_compile(workspace, target)
        if mode == "pytest":
            return await self._verify_pytest(workspace, target, pytest_args or [], current_timeout)
        if mode == "web_build":
            return await self._verify_web_build(workspace, target, current_timeout)
        if mode == "auto":
            return await self._verify_auto(workspace, target, current_timeout)
        return f"Error: Unknown verification action: {action}"

    async def _verify_auto(self, workspace: Path, target: Path, timeout: int) -> str:
        results: list[str] = []
        if self._python_files(target):
            results.append(self._verify_python_compile(workspace, target))

        package_dir = self._find_package_dir(workspace, target)
        if package_dir is not None:
            results.append(await self._verify_web_build(workspace, package_dir, timeout))

        if not results:
            return "Verification skipped: no supported Python or package.json build checks were detected."
        if any(result.startswith("Error:") for result in results):
            return "\n\n".join(results)
        return "\n\n".join(results)

    def _verify_python_compile(self, workspace: Path, target: Path) -> str:
        files = self._python_files(target)
        if not files:
            return f"Verification skipped: no Python files found under {_display_path(workspace, target)}."

        failures: list[str] = []
        for file_path in files:
            try:
                with tokenize.open(str(file_path)) as handle:
                    source = handle.read()
                compile(source, str(file_path), "exec")
            except SyntaxError as exc:
                display = _display_path(workspace, file_path)
                location = f"{display}:{exc.lineno or 0}:{exc.offset or 0}"
                failures.append(f"- {location}: {exc.msg}")
            except Exception as exc:
                display = _display_path(workspace, file_path)
                failures.append(f"- {display}: {type(exc).__name__}: {exc}")

        if failures:
            shown = failures[:20]
            extra = len(failures) - len(shown)
            if extra > 0:
                shown.append(f"... ({extra} more failure(s) omitted)")
            return "\n".join([f"Error: Python compile verification failed for {len(failures)} file(s).", *shown])

        return f"Verification passed: python_compile\nFiles checked: {len(files)}"

    async def _verify_pytest(
        self,
        workspace: Path,
        target: Path,
        pytest_args: list[str],
        timeout: int,
    ) -> str:
        args = [str(item) for item in pytest_args]
        if not args and target != workspace:
            args = [_display_path(workspace, target)]
        result = await self._run_command([sys.executable, "-m", "pytest", *args], workspace, timeout)
        return self._format_command_verification("pytest", result)

    async def _verify_web_build(self, workspace: Path, target: Path, timeout: int) -> str:
        package_dir = self._find_package_dir(workspace, target)
        if package_dir is None:
            return f"Error: No package.json found for web_build near {_display_path(workspace, target)}."

        package_json = package_dir / "package.json"
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"Error: Could not read { _display_path(workspace, package_json) }: {exc}"

        scripts = payload.get("scripts") if isinstance(payload, dict) else None
        if not isinstance(scripts, dict) or "build" not in scripts:
            return f"Error: { _display_path(workspace, package_json) } does not define scripts.build."

        npm = self._resolve_npm_executable()
        if npm is None:
            return "Error: npm was not found on PATH; cannot run web_build verification."

        result = await self._run_command([npm, "run", "build"], package_dir, timeout)
        return self._format_command_verification("web_build", result)

    def _python_files(self, target: Path) -> list[Path]:
        if target.is_file():
            return [target] if target.suffix == ".py" else []
        if not target.is_dir():
            return []

        files: list[Path] = []
        for file_path in target.rglob("*.py"):
            try:
                relative_parts = file_path.relative_to(target).parts
            except ValueError:
                continue
            if any(part in _EXCLUDED_PYTHON_DIRS for part in relative_parts[:-1]):
                continue
            files.append(file_path)
        return sorted(files)

    def _find_package_dir(self, workspace: Path, target: Path) -> Path | None:
        candidates: list[Path] = []
        if target.is_file() and target.name == "package.json":
            candidates.append(target.parent)
        elif target.is_dir():
            candidates.append(target)

        candidates.append(workspace / "apps" / "web")
        candidates.append(workspace)

        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                resolved.relative_to(workspace)
            except ValueError:
                continue
            if (resolved / "package.json").is_file():
                return resolved
        return None

    def _resolve_npm_executable(self) -> str | None:
        preferred = "npm.cmd" if os.name == "nt" else "npm"
        return shutil.which(preferred) or shutil.which("npm")

    async def _run_command(self, command: list[str], cwd: Path, timeout: int) -> VerifyCommandResult:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_process_creation_kwargs(),
        )
        communicate_task = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.wait_for(communicate_task, timeout=timeout)
        except asyncio.TimeoutError:
            await terminate_process_tree(process)
            communicate_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await communicate_task
            return VerifyCommandResult(
                command=command,
                cwd=cwd,
                exit_code=None,
                output=f"Command timed out after {timeout}s.",
                timed_out=True,
            )

        return VerifyCommandResult(
            command=command,
            cwd=cwd,
            exit_code=process.returncode,
            output=_format_streams(stdout, stderr),
        )

    def _format_command_verification(self, name: str, result: VerifyCommandResult) -> str:
        cwd_display = _display_path(self._workspace_resolver(), result.cwd)
        command = _command_display(result.command)
        heading = f"Verification {'failed' if result.exit_code else 'passed'}: {name}"
        if result.timed_out:
            heading = f"Error: Verification timed out: {name}"
        elif result.exit_code:
            heading = f"Error: Verification failed: {name}"

        return "\n".join(
            [
                heading,
                f"Command: {command}",
                f"CWD: {cwd_display or '.'}",
                f"Exit code: {result.exit_code if result.exit_code is not None else '-'}",
                "Output:",
                result.output,
            ]
        )
