import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path

from opensprite.tools.process import ProcessTool
from opensprite.tools.process_runtime import BackgroundProcessManager
from opensprite.tools.shell import ExecTool


def _python_shell_command(code: str) -> str:
    argv = [sys.executable, "-u", "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _extract_session_id(result: str) -> str:
    for line in result.splitlines():
        if line.startswith("Session ID: "):
            return line.removeprefix("Session ID: ").strip()
    raise AssertionError(f"Session ID missing from result: {result}")


def test_exec_background_starts_managed_session_and_process_tool_can_kill(tmp_path):
    async def run() -> None:
        manager = BackgroundProcessManager()
        exec_tool = ExecTool(workspace=Path(tmp_path), process_manager=manager, timeout=5)
        process_tool = ProcessTool(manager)

        command = _python_shell_command(
            "import time; print('background hello', flush=True); time.sleep(5)"
        )
        started = await exec_tool.execute(
            command=command,
            background=True,
            timeout_seconds=5,
        )

        session_id = _extract_session_id(started)
        assert "Background session started." in started

        listed = await process_tool.execute(action="list")
        assert session_id in listed
        assert "running" in listed

        await asyncio.sleep(0.2)
        polled = await process_tool.execute(action="poll", session_id=session_id)
        assert "background hello" in polled

        killed = await process_tool.execute(action="kill", session_id=session_id)
        assert f"Session ID: {session_id}" in killed
        assert "Termination: killed" in killed

    asyncio.run(run())


def test_exec_yield_ms_moves_running_command_to_background(tmp_path):
    async def run() -> None:
        manager = BackgroundProcessManager()
        exec_tool = ExecTool(workspace=Path(tmp_path), process_manager=manager, timeout=5)
        process_tool = ProcessTool(manager)

        command = _python_shell_command(
            "import time; print('yielded output', flush=True); time.sleep(1)"
        )
        started = await exec_tool.execute(
            command=command,
            yield_ms=50,
            timeout_seconds=5,
        )

        session_id = _extract_session_id(started)
        assert "moved to background" in started

        await asyncio.sleep(0.2)
        polled = await process_tool.execute(action="poll", session_id=session_id)
        assert "yielded output" in polled

        await process_tool.execute(action="kill", session_id=session_id)

    asyncio.run(run())


def test_long_lived_commands_are_allowed_when_exec_background_is_requested(tmp_path):
    manager = BackgroundProcessManager()
    tool = ExecTool(workspace=Path(tmp_path), process_manager=manager)

    assert tool._validate_command("uvicorn app:app", allow_managed_background=True) is None
    assert tool._validate_command("sleep 1 &", allow_managed_background=True) is not None
