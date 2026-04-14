import asyncio
from pathlib import Path

from opensprite.tools.base import Tool
from opensprite.tools.filesystem import WriteFileTool
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.shell import ExecTool


def test_write_file_reports_missing_required_arguments(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute())

    assert result == (
        "Error: Missing required argument(s) for write_file: path, content. "
        "Call write_file with both 'path' and 'content'."
    )


def test_exec_reports_missing_command_argument(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute())

    assert result == "Error: Missing required argument for exec: command. Call exec with a 'command' string."


def test_exec_rejects_blank_command(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute(command="   "))

    assert result == "Error: Command for exec must be a non-empty string."


def test_exec_blocks_powershell_recursive_delete(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute(command="powershell -Command \"Remove-Item foo -Recurse -Force\""))

    assert result == "Error: Command blocked by safety guard (dangerous pattern detected)"


def test_exec_rejects_overlong_command(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute(command="a" * 2001))

    assert result == "Error: Command too long for exec (max 2000 chars). Please run a shorter command."


def test_exec_timeout_returns_partial_output(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path), timeout=1)
    command = (
        'python -u -c "import time; print(\'waiting for input...\', flush=True); '
        'time.sleep(2)"'
    )

    result = asyncio.run(tool.execute(command=command))

    assert "Error: Command timed out after 1s." in result
    assert "interactive input" in result
    assert "Partial output before timeout:" in result
    assert "waiting for input..." in result


def test_registry_rejects_blank_read_file_path_before_execution():
    class ReadFileStub(Tool):
        @property
        def name(self) -> str:
            return "read_file"

        @property
        def description(self) -> str:
            return "read"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            raise AssertionError("execute should not be called when validation fails")

    registry = ToolRegistry()
    registry.register(ReadFileStub())

    result = asyncio.run(registry.execute("read_file", {"path": "   "}))

    assert result == "Error: Missing required argument(s) for read_file: path. Received: path=<empty-string>."


def test_registry_rejects_non_string_write_file_content_before_execution():
    class WriteFileStub(Tool):
        @property
        def name(self) -> str:
            return "write_file"

        @property
        def description(self) -> str:
            return "write"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            raise AssertionError("execute should not be called when validation fails")

    registry = ToolRegistry()
    registry.register(WriteFileStub())

    result = asyncio.run(registry.execute("write_file", {"path": "out.txt", "content": ["hello"]}))

    assert result == "Error: Missing required argument(s) for write_file: content. Received: content=<array>."
