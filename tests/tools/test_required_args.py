import asyncio
from pathlib import Path

from opensprite.tools.base import Tool
from opensprite.tools.filesystem import WriteFileTool
from opensprite.tools.process import ProcessTool
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.shell import ExecTool


class BoundedCountStub(Tool):
    @property
    def name(self) -> str:
        return "count_tool"

    @property
    def description(self) -> str:
        return "count"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["count"],
        }

    async def _execute(self, **kwargs):
        raise AssertionError("_execute should not be called when validation fails")


def test_write_file_reports_missing_required_arguments(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute())

    assert result == "Error: Invalid arguments for write_file: missing required argument(s): path, content."


def test_exec_reports_missing_command_argument(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute())

    assert result == "Error: Invalid arguments for exec: missing required argument(s): command."


def test_exec_rejects_blank_command(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute(command="   "))

    assert result == "Error: Invalid arguments for exec: command must be a non-empty string."


def test_exec_blocks_powershell_recursive_delete(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute(command="powershell -Command \"Remove-Item foo -Recurse -Force\""))

    assert result == "Error: Command blocked by safety guard (dangerous pattern detected)"


def test_exec_rejects_overlong_command(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute(command="a" * 2001))

    assert result == "Error: Invalid arguments for exec: command must be at most 2000 characters."


def test_process_poll_requires_session_id():
    tool = ProcessTool()

    result = asyncio.run(tool.execute(action="poll"))

    assert result == "Error: process action 'poll' requires session_id."


def test_process_log_requires_session_id():
    tool = ProcessTool()

    result = asyncio.run(tool.execute(action="log"))

    assert result == "Error: process action 'log' requires session_id."


def test_process_inspect_requires_session_id():
    tool = ProcessTool()

    result = asyncio.run(tool.execute(action="inspect"))

    assert result == "Error: process action 'inspect' requires session_id."


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
            return {
                "type": "object",
                "properties": {"path": {"type": "string", "pattern": r"\S"}},
                "required": ["path"],
            }

        async def _execute(self, **kwargs):
            raise AssertionError("_execute should not be called when validation fails")

    registry = ToolRegistry()
    registry.register(ReadFileStub())

    result = asyncio.run(registry.execute("read_file", {"path": "   "}))

    assert result == "Error: Invalid arguments for read_file: path must be a non-empty string."


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
            return {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "pattern": r"\S"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            }

        async def _execute(self, **kwargs):
            raise AssertionError("_execute should not be called when validation fails")

    registry = ToolRegistry()
    registry.register(WriteFileStub())

    result = asyncio.run(registry.execute("write_file", {"path": "out.txt", "content": ["hello"]}))

    assert result == "Error: Invalid arguments for write_file: content must be string, got array."


def test_registry_rejects_integer_below_minimum_before_execution():
    registry = ToolRegistry()
    registry.register(BoundedCountStub())

    result = asyncio.run(registry.execute("count_tool", {"count": 0}))

    assert result == "Error: Invalid arguments for count_tool: count must be at least 1."


def test_registry_rejects_integer_above_maximum_before_execution():
    registry = ToolRegistry()
    registry.register(BoundedCountStub())

    result = asyncio.run(registry.execute("count_tool", {"count": 11}))

    assert result == "Error: Invalid arguments for count_tool: count must be at most 10."
