import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from opensprite.cli.commands import app
from opensprite.cli import service_background


runner = CliRunner()


class FakeProcess:
    pid = 4321

    def poll(self):
        return None


def test_start_service_launches_detached_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    python_path = tmp_path / "python.exe"

    status = service_background.start_service(
        config_path=tmp_path / "opensprite.json",
        home=tmp_path,
        python_executable=python_path,
        popen=fake_popen,
    )

    assert status.running is True
    assert status.pid == 4321
    assert status.pid_file.read_text(encoding="utf-8") == "4321\n"
    log_text = status.log_file.read_text(encoding="utf-8")
    assert "starting OpenSprite background gateway" in log_text
    assert f"command: {python_path.resolve()} -m opensprite gateway --config {(tmp_path / 'opensprite.json').resolve()}" in log_text
    command, kwargs = calls[0]
    assert command[:3] == [str(python_path.resolve()), "-m", "opensprite"]
    assert command[3:] == ["gateway", "--config", str((tmp_path / "opensprite.json").resolve())]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert "creationflags" in kwargs
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        assert kwargs["creationflags"] & create_no_window
    if getattr(subprocess, "STARTUPINFO", None) is not None:
        assert "startupinfo" in kwargs


def test_is_process_running_uses_windows_process_exit_code(monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")

    class FakeKernel32:
        def OpenProcess(self, access, inherit, pid):
            assert pid == 1234
            return 99

        def GetExitCodeProcess(self, handle, exit_code):
            assert handle == 99
            exit_code._obj.value = 259
            return 1

        def CloseHandle(self, handle):
            assert handle == 99
            return 1

    monkeypatch.setattr(service_background.ctypes, "windll", SimpleNamespace(kernel32=FakeKernel32()), raising=False)

    assert service_background.is_process_running(1234) is True


def test_is_process_running_windows_missing_process(monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")

    class FakeKernel32:
        def OpenProcess(self, access, inherit, pid):
            return 0

    monkeypatch.setattr(service_background.ctypes, "windll", SimpleNamespace(kernel32=FakeKernel32()), raising=False)

    assert service_background.is_process_running(1234) is False


def test_resolve_gateway_python_prefers_installer_venv(tmp_path, monkeypatch):
    install_dir = tmp_path / "opensprite"
    python_path = install_dir / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENSPRITE_INSTALL_DIR", str(install_dir))

    assert service_background.resolve_gateway_python() == python_path.absolute()


def test_resolve_gateway_python_prefers_windows_default_install_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    monkeypatch.delenv("OPENSPRITE_INSTALL_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    python_path = tmp_path / "OpenSprite" / "opensprite" / ".venv" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")

    assert service_background.resolve_gateway_python() == python_path.absolute()


def test_resolve_gateway_python_uses_sibling_python_for_console_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    monkeypatch.delenv("OPENSPRITE_INSTALL_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "missing"))
    scripts_dir = tmp_path / "install" / ".venv" / "Scripts"
    launcher = scripts_dir / "opensprite.exe"
    python_path = scripts_dir / "python.exe"
    scripts_dir.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(service_background.sys, "executable", str(launcher))

    assert service_background.resolve_gateway_python() == python_path.absolute()


def test_resolve_gateway_python_falls_back_to_current_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSPRITE_INSTALL_DIR", str(tmp_path / "missing"))

    assert service_background.resolve_gateway_python() == Path(sys.executable).absolute()


def test_start_service_reports_early_gateway_exit(tmp_path):
    class ExitedProcess:
        pid = 1234

        def poll(self):
            return 1

    def fake_popen(command, **kwargs):
        return ExitedProcess()

    try:
        service_background.start_service(
            home=tmp_path,
            python_executable=tmp_path / "python",
            popen=fake_popen,
            startup_timeout=0.01,
        )
    except RuntimeError as exc:
        assert "exited during startup" in str(exc)
        assert str(tmp_path / "logs" / "gateway.log") in str(exc)
        assert "starting OpenSprite background gateway" in str(exc)
    else:
        raise AssertionError("Expected early gateway exit to fail")

    assert "starting OpenSprite background gateway" in service_background.get_log_file(tmp_path).read_text(encoding="utf-8")
    assert not service_background.get_pid_file(tmp_path).exists()


def test_get_service_status_cleans_stale_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background, "is_process_running", lambda pid: False)
    pid_file = service_background.get_pid_file(tmp_path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("1234\n", encoding="utf-8")

    status = service_background.get_service_status(home=tmp_path)

    assert status.running is False
    assert status.pid is None
    assert not pid_file.exists()


def test_get_service_status_ignores_unremovable_stale_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background, "is_process_running", lambda pid: False)
    pid_file = service_background.get_pid_file(tmp_path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("1234\n", encoding="utf-8")
    monkeypatch.setattr(type(pid_file), "unlink", lambda self: (_ for _ in ()).throw(PermissionError("denied")))

    status = service_background.get_service_status(home=tmp_path)

    assert status.running is False
    assert status.pid is None


def test_stop_service_uses_taskkill_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    states = [True, False]
    monkeypatch.setattr(service_background, "is_process_running", lambda pid: states.pop(0) if states else False)
    pid_file = service_background.get_pid_file(tmp_path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("1234\n", encoding="utf-8")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    service_background.stop_service(home=tmp_path, run=fake_run)

    assert calls[0][0] == ["taskkill", "/PID", "1234", "/T"]
    assert not pid_file.exists()


def test_stop_service_forces_taskkill_on_windows_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    states = [True, True, False]
    monkeypatch.setattr(service_background, "is_process_running", lambda pid: states.pop(0) if states else False)
    monkeypatch.setattr(service_background.time, "sleep", lambda seconds: None)
    pid_file = service_background.get_pid_file(tmp_path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("1234\n", encoding="utf-8")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    service_background.stop_service(home=tmp_path, timeout=0, force_timeout=1, run=fake_run)

    assert [call[0] for call in calls] == [
        ["taskkill", "/PID", "1234", "/T"],
        ["taskkill", "/PID", "1234", "/T", "/F"],
    ]
    assert not pid_file.exists()


def test_install_startup_task_registers_windows_logon_task(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="SUCCESS", stderr="")

    python_path = tmp_path / "python.exe"
    pythonw_path = tmp_path / "pythonw.exe"
    python_path.write_text("", encoding="utf-8")
    pythonw_path.write_text("", encoding="utf-8")

    task_name = service_background.install_startup_task(
        config_path=tmp_path / "opensprite.json",
        python_executable=python_path,
        run=fake_run,
    )

    assert task_name == "OpenSprite Gateway"
    args, kwargs = calls[0]
    assert args[:6] == ["schtasks", "/Create", "/TN", "OpenSprite Gateway", "/SC", "ONLOGON"]
    assert "/TR" in args
    task_run = args[args.index("/TR") + 1]
    assert "pythonw.exe" in task_run
    assert "-m" in task_run
    assert "opensprite" in task_run
    assert "service" in task_run
    assert "start" in task_run
    assert str((tmp_path / "opensprite.json").resolve()) in task_run
    assert "/F" in args
    assert kwargs["capture_output"] is True


def test_install_startup_task_falls_back_to_startup_folder_on_access_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    python_path = tmp_path / "OpenSprite" / "opensprite" / ".venv" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")
    python_path.with_name("pythonw.exe").write_text("", encoding="utf-8")
    legacy_startup_file = service_background.get_windows_startup_folder() / "OpenSprite Gateway.cmd"
    legacy_startup_file.parent.mkdir(parents=True)
    legacy_startup_file.write_text("@echo off\r\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="ERROR: Access is denied.")

    task_name = service_background.install_startup_task(
        config_path=tmp_path / ".opensprite" / "opensprite.json",
        python_executable=python_path,
        run=fake_run,
    )

    startup_file = service_background.get_windows_startup_file_path()
    content = startup_file.read_text(encoding="utf-8")
    assert task_name == "OpenSprite Gateway (Startup folder)"
    assert startup_file.exists()
    assert not legacy_startup_file.exists()
    assert 'WScript.Shell' in content
    assert 'OPENSPRITE_INSTALL_DIR") = ' in content
    assert str(tmp_path / "OpenSprite" / "opensprite") in content
    assert str(python_path.with_name("pythonw.exe").resolve()) in content
    assert "-m" in content
    assert "opensprite" in content
    assert "service" in content
    assert "start" in content
    assert str((tmp_path / ".opensprite" / "opensprite.json").resolve()) in content
    assert ", 0, False" in content


def test_startup_status_detects_startup_folder_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service_background, "is_process_running", lambda pid: False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    startup_file = service_background.get_windows_startup_file_path()
    startup_file.parent.mkdir(parents=True)
    startup_file.write_text("@echo off\r\n", encoding="utf-8")

    status = service_background.get_service_status(home=tmp_path, include_startup=True)

    assert status.startup_enabled is True
    assert status.startup_task_name == "OpenSprite Gateway"


def test_get_service_status_can_include_windows_startup_task(tmp_path, monkeypatch):
    monkeypatch.setattr(service_background.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service_background, "is_process_running", lambda pid: False)
    monkeypatch.setattr(service_background, "is_startup_task_installed", lambda: False)

    status = service_background.get_service_status(home=tmp_path, include_startup=True)
    assert status.startup_enabled is False

    monkeypatch.setattr(service_background, "is_startup_task_installed", lambda: True)
    status = service_background.get_service_status(home=tmp_path, include_startup=True)
    assert status.startup_enabled is True
    assert status.startup_task_name == "OpenSprite Gateway"


def test_service_install_command_registers_windows_startup_task(monkeypatch, tmp_path):
    monkeypatch.setattr("opensprite.cli.commands_service.platform.system", lambda: "Windows")
    monkeypatch.setattr("opensprite.cli.commands.service_background.install_startup_task", lambda config_path: "OpenSprite Gateway")

    result = runner.invoke(app, ["service", "install", "--config", str(tmp_path / "opensprite.json"), "--no-start"])

    assert result.exit_code == 0
    assert "Installed startup task: OpenSprite Gateway" in result.stdout
    assert "Started: no" in result.stdout


def test_service_status_command_renders_detached_status(monkeypatch, tmp_path):
    monkeypatch.setattr("opensprite.cli.commands.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "opensprite.cli.commands.service_background.get_service_status",
        lambda **kwargs: service_background.BackgroundServiceStatus(
            running=True,
            pid=4321,
            pid_file=tmp_path / "gateway.pid",
            log_file=tmp_path / "logs" / "gateway.log",
            startup_enabled=True,
            startup_task_name="OpenSprite Gateway",
        ),
    )

    result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 0
    assert "Mode: detached process" in result.stdout
    assert "Active: yes" in result.stdout
    assert "PID: 4321" in result.stdout
    assert "Startup: yes" in result.stdout
    assert "Startup Task: OpenSprite Gateway" in result.stdout


def test_linux_service_status_uses_detached_when_unit_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr("opensprite.cli.commands.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "opensprite.cli.commands.service_linux.get_service_file_path",
        lambda: tmp_path / "missing.service",
    )
    monkeypatch.setattr(
        "opensprite.cli.commands.service_background.get_service_status",
        lambda **kwargs: service_background.BackgroundServiceStatus(
            running=False,
            pid=None,
            pid_file=tmp_path / "gateway.pid",
            log_file=tmp_path / "logs" / "gateway.log",
        ),
    )

    result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 0
    assert "Mode: detached process" in result.stdout
    assert "Active: no" in result.stdout
