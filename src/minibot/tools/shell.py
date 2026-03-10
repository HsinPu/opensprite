"""Shell execution tool."""

import asyncio
from pathlib import Path
from typing import Any

from minibot.tools.base import Tool


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
        workspace: Path,
        timeout: int = 60,
        deny_patterns: list[str] | None = None,
    ):
        self.workspace = workspace
        self.timeout = timeout
        self.deny_patterns = deny_patterns or self.DENY_PATTERNS

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

    async def execute(self, command: str, **kwargs: Any) -> str:
        import re
        
        # Check for dangerous patterns
        for pattern in self.deny_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"
        
        try:
            # Security: run in workspace directory
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace)
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
