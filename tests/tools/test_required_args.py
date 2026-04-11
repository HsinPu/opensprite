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


def test_exec_reports_missing_command_argument(tmp_path):
    tool = ExecTool(workspace=Path(tmp_path))

    result = asyncio.run(tool.execute())

    assert result == "Error: Missing required argument for exec: command. Call exec with a 'command' string."
