import asyncio
import subprocess

from opensprite.utils import processes as processes_module


class _FakeKiller:
    def __init__(self):
        self.kill_called = False

    async def wait(self):
        return 0

    def kill(self):
        self.kill_called = True


class _FakeProcess:
    def __init__(self, *, pid: int = 123, wait_plan: list[str] | None = None):
        self.pid = pid
        self.wait_plan = list(wait_plan or ["exit"])
        self.returncode = None
        self.kill_called = False
        self.terminate_called = False
        self.wait_calls = 0

    async def wait(self):
        self.wait_calls += 1
        if self.returncode is not None:
            return self.returncode

        action = self.wait_plan.pop(0) if self.wait_plan else "exit"
        if action == "timeout":
            await asyncio.sleep(1)
            return self.returncode

        if self.kill_called:
            self.returncode = -9
            return self.returncode

        self.returncode = 0
        return self.returncode

    def kill(self):
        self.kill_called = True
        self.returncode = -9

    def terminate(self):
        self.terminate_called = True
        self.returncode = 0


def test_windows_hidden_process_kwargs_suppresses_console_window(monkeypatch):
    monkeypatch.setattr(processes_module.os, "name", "nt", raising=False)
    monkeypatch.setattr(processes_module.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(processes_module.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)

    kwargs = processes_module.windows_hidden_process_kwargs(new_process_group=True)

    assert kwargs["creationflags"] & 0x08000000
    assert kwargs["creationflags"] & 0x00000200
    if getattr(subprocess, "STARTUPINFO", None) is not None:
        assert "startupinfo" in kwargs


def test_terminate_process_tree_uses_graceful_then_force_taskkill_on_windows(monkeypatch):
    taskkill_calls = []
    process = _FakeProcess(pid=456)

    async def fake_create_subprocess_exec(*args, **kwargs):
        taskkill_calls.append((args, kwargs))
        if "/F" in args:
            process.returncode = -9
        return _FakeKiller()

    monkeypatch.setattr(processes_module.os, "name", "nt", raising=False)
    monkeypatch.setattr(processes_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(processes_module.terminate_process_tree(process, wait_timeout=0.01))

    assert [call[0] for call in taskkill_calls] == [
        ("taskkill", "/PID", "456", "/T"),
        ("taskkill", "/PID", "456", "/T", "/F"),
    ]
    assert process.returncode == -9
    assert process.kill_called is False


def test_terminate_process_tree_falls_back_to_direct_kill_after_windows_taskkill(monkeypatch):
    taskkill_calls = []
    process = _FakeProcess(pid=654, wait_plan=["timeout", "timeout"])

    async def fake_create_subprocess_exec(*args, **kwargs):
        taskkill_calls.append((args, kwargs))
        return _FakeKiller()

    monkeypatch.setattr(processes_module.os, "name", "nt", raising=False)
    monkeypatch.setattr(processes_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(processes_module.terminate_process_tree(process, wait_timeout=0.01))

    assert [call[0] for call in taskkill_calls] == [
        ("taskkill", "/PID", "654", "/T"),
        ("taskkill", "/PID", "654", "/T", "/F"),
    ]
    assert process.kill_called is True
    assert process.returncode == -9


def test_terminate_process_tree_stops_after_sigterm_when_process_exits(monkeypatch):
    killpg_calls = []
    process = _FakeProcess(pid=789)

    def fake_killpg(pid, sig):
        killpg_calls.append((pid, sig))
        if sig == processes_module.signal.SIGTERM:
            process.returncode = 0

    monkeypatch.setattr(processes_module.os, "name", "posix", raising=False)
    monkeypatch.setattr(processes_module.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(processes_module.signal, "SIGTERM", "SIGTERM", raising=False)
    monkeypatch.setattr(processes_module.signal, "SIGKILL", "SIGKILL", raising=False)

    asyncio.run(processes_module.terminate_process_tree(process, wait_timeout=0.01))

    assert killpg_calls == [(789, processes_module.signal.SIGTERM)]
    assert process.kill_called is False


def test_terminate_process_tree_escalates_from_sigterm_to_sigkill(monkeypatch):
    killpg_calls = []
    process = _FakeProcess(pid=987, wait_plan=["timeout"])

    def fake_killpg(pid, sig):
        killpg_calls.append((pid, sig))
        if sig == processes_module.signal.SIGKILL:
            process.returncode = -9

    monkeypatch.setattr(processes_module.os, "name", "posix", raising=False)
    monkeypatch.setattr(processes_module.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(processes_module.signal, "SIGTERM", "SIGTERM", raising=False)
    monkeypatch.setattr(processes_module.signal, "SIGKILL", "SIGKILL", raising=False)

    asyncio.run(processes_module.terminate_process_tree(process, wait_timeout=0.01))

    assert killpg_calls == [
        (987, processes_module.signal.SIGTERM),
        (987, processes_module.signal.SIGKILL),
    ]
    assert process.kill_called is False
    assert process.returncode == -9
