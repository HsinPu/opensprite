"""Filesystem tools for reading and writing files."""

from pathlib import Path
from typing import Any

from minibot.tools.base import Tool


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file from the filesystem."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read"
                }
            },
            "required": ["path"]
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            file_path = Path(path)
            # Security: restrict to workspace if absolute path
            if file_path.is_absolute() and not str(file_path).startswith(str(self.workspace)):
                return f"Error: Access denied. Path must be within workspace: {self.workspace}"
            
            if not file_path.exists():
                return f"Error: File not found: {path}"
            
            content = file_path.read_text(encoding="utf-8")
            # Limit output size
            if len(content) > 5000:
                content = content[:5000] + f"\n\n... (truncated, total {len(content)} chars)"
            return content
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file. Creates the file if it doesn't exist."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write"
                }
            },
            "required": ["path", "content"]
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            file_path = Path(path)
            # Security: restrict to workspace
            if file_path.is_absolute() and not str(file_path).startswith(str(self.workspace)):
                return f"Error: Access denied. Path must be within workspace: {self.workspace}"
            
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote to {path} ({len(content)} chars)"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List files and directories in a given path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list (default: current directory)"
                }
            }
        }

    async def execute(self, path: str = ".", **kwargs: Any) -> str:
        try:
            dir_path = Path(path)
            if not dir_path.is_absolute():
                dir_path = self.workspace / dir_path
            
            # Security check
            if not str(dir_path).startswith(str(self.workspace)):
                return f"Error: Access denied. Path must be within workspace: {self.workspace}"
            
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"
            
            items = []
            for item in sorted(dir_path.iterdir()):
                suffix = "/" if item.is_dir() else ""
                items.append(f"{item.name}{suffix}")
            
            return "\n".join(items) if items else "(empty)"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
