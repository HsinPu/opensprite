"""exec policy and lifecycle behavior."""

import asyncio
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from opensprite.tools.shell import (
    _build_pipe_drain_warning_result,
    _build_timeout_result,
    _foreground_exec_guidance,
    _has_shell_background_operator,
)


def _python_shell_command(code: str) -> str:
    argv = [sys.executable, "-u", "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


class TestBackgroundOperatorDetection:
    def test_detects_shell_background_operator(self):
        assert _has_shell_background_operator("sleep 1 &") is True
        assert _has_shell_background_operator("sleep 1&") is True

    def test_ignores_redirect_and_logical_ampersands(self):
        assert _has_shell_background_operator("cmd 2>&1") is False
        assert _has_shell_background_operator("cmd &>/dev/null") is False
        assert _has_shell_background_operator("A && B") is False

    def test_ignores_ampersand_inside_quotes(self):
        assert _has_shell_background_operator('printf "&"') is False


class TestForegroundGuidance:
    def test_blocks_trailing_ampersand(self):
        assert _foreground_exec_guidance("sleep 1&") is not None

    def test_blocks_inline_ampersand(self):
        assert _foreground_exec_guidance("foo & bar") is not None

    def test_blocks_nohup(self):
        assert _foreground_exec_guidance("nohup python server.py") is not None

    def test_blocks_uvicorn(self):
        assert _foreground_exec_guidance("uvicorn app:app --host 0.0.0.0") is not None

    def test_allows_plain_echo(self):
        assert _foreground_exec_guidance("echo hello") is None

    def test_allows_uvicorn_help(self):
        assert _foreground_exec_guidance("uvicorn --help") is None


def test_exec_tool_returns_guidance_for_uvicorn(tmp_path):
    from opensprite.tools.shell import ExecTool

    tool = ExecTool(workspace=Path(tmp_path))
    result = asyncio.run(tool.execute(command="uvicorn app:app"))
    assert result.startswith("Error:")
    assert "long-lived" in result.lower() or "server" in result.lower()


def test_exec_tool_runs_echo_when_allowed(tmp_path):
    from opensprite.tools.shell import ExecTool

    tool = ExecTool(workspace=Path(tmp_path))
    result = asyncio.run(tool.execute(command="echo opensprite_exec_ok"))
    assert "opensprite_exec_ok" in result
    assert not result.startswith("Error:")


def test_exec_tool_blocks_dangerous_command(tmp_path):
    from opensprite.tools.shell import ExecTool

    tool = ExecTool(workspace=Path(tmp_path))
    result = asyncio.run(tool.execute(command="git reset --hard"))

    assert result == "Error: Command blocked by safety guard (dangerous pattern detected)"


def test_exec_tool_accepts_notify_on_complete_alias(tmp_path):
    from opensprite.tools.shell import ExecTool

    tool = ExecTool(workspace=Path(tmp_path))

    async def run():
        result = await tool.execute(
            command=_python_shell_command("print('done', flush=True)"),
            background=True,
            notify_on_complete=False,
        )
        sessions = await tool.process_manager.list_sessions()
        return result, sessions

    result, sessions = asyncio.run(run())

    assert "Background session started." in result
    assert len(sessions) == 1
    assert sessions[0].notify_on_exit is False


def test_exec_tool_persists_background_session_lifecycle(tmp_path):
    from opensprite.storage.sqlite import SQLiteStorage
    from opensprite.tools.process_runtime import BackgroundProcessManager
    from opensprite.tools.shell import ExecTool

    storage = SQLiteStorage(Path(tmp_path) / "sessions.db")
    manager = BackgroundProcessManager(storage=storage)
    tool = ExecTool(
        workspace=Path(tmp_path),
        process_manager=manager,
        background_session_owner_factory=lambda: {
            "session_id": "chat-1",
            "run_id": "run-1",
            "channel": "web",
            "external_chat_id": "external-1",
        },
    )

    async def run():
        result = await tool.execute(
            command=_python_shell_command("print('persisted background', flush=True)"),
            background=True,
            notify_on_complete=False,
        )
        sessions = await manager.list_sessions()
        assert len(sessions) == 1
        session = sessions[0]
        deadline = time.time() + 5
        while session.state != "exited" and time.time() < deadline:
            await asyncio.sleep(0.05)
            session = (await manager.list_sessions())[0]
        stored = await storage.get_background_process(session.session_id)
        return result, session, stored

    result, session, stored = asyncio.run(run())

    assert "Background session started." in result
    assert session.state == "exited"
    assert stored is not None
    assert stored.owner_session_id == "chat-1"
    assert stored.owner_run_id == "run-1"
    assert stored.state == "exited"
    assert stored.exit_code == 0
    assert stored.notify_mode == "none"
    assert "persisted background" in stored.output_tail


def test_exec_tool_preserves_stdout_stderr_order(tmp_path):
    from opensprite.tools.shell import ExecTool

    tool = ExecTool(workspace=Path(tmp_path))
    command = _python_shell_command(
        "import sys, time; "
        "print('out1', flush=True); "
        "time.sleep(0.1); "
        "print('err1', file=sys.stderr, flush=True); "
        "time.sleep(0.1); "
        "print('out2', flush=True)"
    )

    result = asyncio.run(tool.execute(command=command))

    assert "out1" in result
    assert "[stderr] err1" in result
    assert "out2" in result
    assert result.index("out1") < result.index("[stderr] err1") < result.index("out2")


def test_exec_timeout_terminates_descendant_processes(tmp_path):
    from opensprite.tools.shell import ExecTool

    marker = Path(tmp_path) / "child-survived.txt"
    child_code = (
        "import pathlib, time; "
        "time.sleep(2); "
        f"pathlib.Path({str(marker)!r}).write_text('child survived', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-u', '-c', {child_code!r}]); "
        "print('parent started', flush=True); "
        "time.sleep(10)"
    )

    tool = ExecTool(workspace=Path(tmp_path), timeout=1)
    result = asyncio.run(tool.execute(command=_python_shell_command(parent_code)))

    assert "Error: Command timed out after 1s." in result
    assert "parent started" in result

    deadline = time.time() + 3
    while time.time() < deadline:
        if marker.exists():
            break
        time.sleep(0.1)

    assert not marker.exists()


def test_exec_warns_when_output_readers_linger_after_process_exit(tmp_path, monkeypatch):
    import opensprite.tools.shell as shell_module

    class _FinishedProcess:
        returncode = 0

        async def wait(self):
            return 0

    async def fake_start_shell_process(command, *, cwd, output_chunks):
        output_chunks.extend(
            [
                shell_module.CapturedOutputChunk("stdout", b"parent exiting\n"),
                shell_module.CapturedOutputChunk("stdout", b"child still attached\n"),
            ]
        )

        async def sleeper():
            await asyncio.sleep(1)

        return _FinishedProcess(), [asyncio.create_task(sleeper())]

    monkeypatch.setattr(shell_module, "start_shell_process", fake_start_shell_process)

    tool = shell_module.ExecTool(workspace=Path(tmp_path), timeout=1)
    tool._output_drain_timeout = lambda timeout_seconds: 0.1
    result = asyncio.run(tool.execute(command="echo simulated"))

    assert "parent exiting" in result
    assert "child still attached" in result
    assert "output pipes did not close within 0.1s after the shell exited" in result


def test_build_timeout_result_appends_pipe_warning_when_not_drained():
    result = _build_timeout_result(3, "partial output", drained=False)

    assert "Error: Command timed out after 3s." in result
    assert "Partial output before timeout:\npartial output" in result
    assert "output pipes did not close promptly after timeout" in result


def test_build_pipe_drain_warning_result_mentions_timeout_window():
    result = _build_pipe_drain_warning_result("hello", drain_timeout=7)

    assert result.startswith("hello\n\n")
    assert "output pipes did not close within 7s after the shell exited" in result
