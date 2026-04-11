"""Shell execution tool."""

import asyncio
from pathlib import Path
from typing import Any, Callable

from .base import Tool


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


class ExecTool(Tool):
    """Tool to execute shell commands."""

    # Dangerous command patterns that are blocked
    DENY_PATTERNS = [
        r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
        r"\bdel\s+/[fq]\b",              # del /f, del /q
        r"\brmdir\s+/s\b",               # rmdir /s
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

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                }
            },
            "required": ["command"]
        }

    async def execute(self, **kwargs: Any) -> str:
        import re
        if "command" not in kwargs:
            return "Error: Missing required argument for exec: command. Call exec with a 'command' string."
        command = str(kwargs["command"])
        
        # Check for dangerous patterns
        for pattern in self.deny_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"
        
        try:
            workspace = self._get_workspace()
            # Security: run in workspace directory
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace)
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), 
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Command timed out after {self.timeout}s"
            
            result = []
            if stdout:
                result.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                result.append(f"[stderr] {stderr.decode('utf-8', errors='replace')}")
            
            output = "".join(result).strip()
            if not output:
                output = "(no output)"
            
            # Limit output size
            if len(output) > 3000:
                output = output[:3000] + f"\n\n... (truncated, total {len(output)} chars)"
            
            return output
        except Exception as e:
            return f"Error executing command: {str(e)}"
