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
        assert "runtime=" in listed

        await asyncio.sleep(0.2)
        polled = await process_tool.execute(action="poll", session_id=session_id)
        assert "Started: " in polled
        assert "Runtime: " in polled
        assert "background hello" in polled

        killed = await process_tool.execute(action="kill", session_id=session_id)
        assert f"Session ID: {session_id}" in killed
        assert "Finished: " in killed
        assert "Runtime: " in killed
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


def test_process_inspect_returns_metadata_without_output_sections(tmp_path):
    async def run() -> None:
        manager = BackgroundProcessManager()
        exec_tool = ExecTool(workspace=Path(tmp_path), process_manager=manager, timeout=5)
        process_tool = ProcessTool(manager)

        started = await exec_tool.execute(
            command=_python_shell_command("print('inspect me', flush=True)"),
            background=True,
            timeout_seconds=5,
        )
        session_id = _extract_session_id(started)
        await asyncio.sleep(0.2)

        inspected = await process_tool.execute(action="inspect", session_id=session_id)

        assert f"Session ID: {session_id}" in inspected
        assert "Started: " in inspected
        assert "Runtime: " in inspected
        assert "Has output: yes" in inspected
        assert "Output drained: yes" in inspected
        assert "Termination: exit" in inspected
        assert "Full output:" not in inspected
        assert "New output:" not in inspected
        assert "Output tail:" not in inspected

    asyncio.run(run())


def test_process_log_and_clear_handle_exited_session(tmp_path):
    async def run() -> None:
        manager = BackgroundProcessManager()
        exec_tool = ExecTool(workspace=Path(tmp_path), process_manager=manager, timeout=5)
        process_tool = ProcessTool(manager)

        command = _python_shell_command(
            "print('finished output', flush=True)"
        )
        started = await exec_tool.execute(
            command=command,
            background=True,
            timeout_seconds=5,
        )

        session_id = _extract_session_id(started)
        await asyncio.sleep(0.2)

        logged = await process_tool.execute(action="log", session_id=session_id)
        assert "Started: " in logged
        assert "Finished: " in logged
        assert "Runtime: " in logged
        assert "Termination: exit" in logged
        assert "finished output" in logged

        cleared = await process_tool.execute(action="clear", session_id=session_id)
        assert f"Cleared background session '{session_id}'" in cleared

        listed = await process_tool.execute(action="list")
        assert listed == "No background sessions."

    asyncio.run(run())


def test_background_session_exit_notifier_runs_on_natural_completion(tmp_path):
    async def run() -> None:
        notifications = []
        manager = BackgroundProcessManager()

        async def notify(session):
            notifications.append(
                (
                    session.session_id,
                    session.termination_reason,
                    manager.render_output(session, max_chars=None),
                )
            )

        exec_tool = ExecTool(
            workspace=Path(tmp_path),
            process_manager=manager,
            timeout=5,
            background_notification_factory=lambda: notify,
        )

        started = await exec_tool.execute(
            command=_python_shell_command("print('notify done', flush=True)"),
            background=True,
            timeout_seconds=5,
        )
        session_id = _extract_session_id(started)
        await asyncio.sleep(0.2)

        assert notifications == [(session_id, "exit", "notify done")]

    asyncio.run(run())


def test_background_session_quiet_success_does_not_notify_by_default(tmp_path):
    async def run() -> None:
        notifications = []
        manager = BackgroundProcessManager()

        async def notify(session):
            notifications.append(session.session_id)

        exec_tool = ExecTool(
            workspace=Path(tmp_path),
            process_manager=manager,
            timeout=5,
            background_notification_factory=lambda: notify,
        )

        await exec_tool.execute(
            command=_python_shell_command("pass"),
            background=True,
            timeout_seconds=5,
        )
        await asyncio.sleep(0.2)

        assert notifications == []

    asyncio.run(run())


def test_background_session_quiet_success_can_notify_when_enabled(tmp_path):
    async def run() -> None:
        notifications = []
        manager = BackgroundProcessManager()

        async def notify(session):
            notifications.append((session.session_id, session.termination_reason, session.exit_code))

        exec_tool = ExecTool(
            workspace=Path(tmp_path),
            process_manager=manager,
            timeout=5,
            background_notification_factory=lambda: notify,
        )

        started = await exec_tool.execute(
            command=_python_shell_command("pass"),
            background=True,
            timeout_seconds=5,
            notify_on_exit_empty_success=True,
        )
        session_id = _extract_session_id(started)
        await asyncio.sleep(0.2)

        assert notifications == [(session_id, "exit", 0)]

    asyncio.run(run())


def test_background_session_non_success_notifies_even_without_output(tmp_path):
    async def run() -> None:
        notifications = []
        manager = BackgroundProcessManager()

        async def notify(session):
            notifications.append((session.termination_reason, session.exit_code))

        exec_tool = ExecTool(
            workspace=Path(tmp_path),
            process_manager=manager,
            timeout=5,
            background_notification_factory=lambda: notify,
        )

        await exec_tool.execute(
            command=_python_shell_command("import sys; sys.exit(2)"),
            background=True,
            timeout_seconds=5,
        )
        await asyncio.sleep(0.2)

        assert notifications == [("exit", 2)]

    asyncio.run(run())


def test_background_session_exit_notifier_is_suppressed_for_manual_kill(tmp_path):
    async def run() -> None:
        notifications = []
        manager = BackgroundProcessManager()

        async def notify(session):
            notifications.append(session.session_id)

        exec_tool = ExecTool(
            workspace=Path(tmp_path),
            process_manager=manager,
            timeout=5,
            background_notification_factory=lambda: notify,
        )
        process_tool = ProcessTool(manager)

        started = await exec_tool.execute(
            command=_python_shell_command("import time; print('kill me', flush=True); time.sleep(5)"),
            background=True,
            timeout_seconds=5,
        )
        session_id = _extract_session_id(started)

        await process_tool.execute(action="kill", session_id=session_id)
        await asyncio.sleep(0.2)

        assert notifications == []

    asyncio.run(run())


def test_process_clear_without_session_id_removes_only_exited_sessions(tmp_path):
    async def run() -> None:
        manager = BackgroundProcessManager()
        exec_tool = ExecTool(workspace=Path(tmp_path), process_manager=manager, timeout=5)
        process_tool = ProcessTool(manager)

        finished = await exec_tool.execute(
            command=_python_shell_command("print('done', flush=True)"),
            background=True,
            timeout_seconds=5,
        )
        running = await exec_tool.execute(
            command=_python_shell_command("import time; print('still running', flush=True); time.sleep(5)"),
            background=True,
            timeout_seconds=5,
        )

        finished_id = _extract_session_id(finished)
        running_id = _extract_session_id(running)
        await asyncio.sleep(0.2)

        cleared = await process_tool.execute(action="clear")
        assert cleared == "Cleared 1 exited background session(s)."

        listed = await process_tool.execute(action="list")
        assert finished_id not in listed
        assert running_id in listed

        await process_tool.execute(action="kill", session_id=running_id)

    asyncio.run(run())


def test_background_manager_prunes_old_exited_sessions(tmp_path):
    async def run() -> None:
        manager = BackgroundProcessManager(max_exited_sessions=1)
        exec_tool = ExecTool(workspace=Path(tmp_path), process_manager=manager, timeout=5)

        first = await exec_tool.execute(
            command=_python_shell_command("print('first', flush=True)"),
            background=True,
            timeout_seconds=5,
        )
        first_id = _extract_session_id(first)
        await asyncio.sleep(0.2)

        second = await exec_tool.execute(
            command=_python_shell_command("print('second', flush=True)"),
            background=True,
            timeout_seconds=5,
        )
        second_id = _extract_session_id(second)
        await asyncio.sleep(0.2)

        sessions = await manager.list_sessions()
        session_ids = [session.session_id for session in sessions]

        assert first_id not in session_ids
        assert second_id in session_ids
        assert len(session_ids) == 1

    asyncio.run(run())


def test_long_lived_commands_are_allowed_when_exec_background_is_requested(tmp_path):
    manager = BackgroundProcessManager()
    tool = ExecTool(workspace=Path(tmp_path), process_manager=manager)

    assert tool._validate_command("uvicorn app:app", allow_managed_background=True) is None
    assert tool._validate_command("sleep 1 &", allow_managed_background=True) is not None
