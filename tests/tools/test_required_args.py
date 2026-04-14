import asyncio
from pathlib import Path

from opensprite.tools.filesystem import WriteFileTool
from opensprite.tools.shell import ExecTool


def test_write_file_reports_missing_required_arguments(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute())

    assert result == (
        "Error: Missing required argument(s) for write_file: path, content. "
        "Call write_file with both 'path' and 'content'."
    )


def test_write_file_rejects_blank_required_arguments(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(path="   ", content=""))

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
