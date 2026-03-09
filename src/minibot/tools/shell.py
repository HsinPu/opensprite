"""Shell execution tool."""

import asyncio
from pathlib import Path
from typing import Any

from minibot.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        workspace: Path,
        timeout: int = 60,
    ):
        self.workspace = workspace
        self.timeout = timeout

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
