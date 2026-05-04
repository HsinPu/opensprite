import subprocess
import sys
from pathlib import Path

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
    command, kwargs = calls[0]
    assert command[:3] == [str(python_path.resolve()), "-m", "opensprite"]
    assert command[3:] == ["gateway", "--config", str((tmp_path / "opensprite.json").resolve())]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert "creationflags" in kwargs


def test_resolve_gateway_python_prefers_installer_venv(tmp_path, monkeypatch):
    install_dir = tmp_path / "opensprite"
    python_path = install_dir / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENSPRITE_INSTALL_DIR", str(install_dir))

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
    else:
        raise AssertionError("Expected early gateway exit to fail")

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


def test_service_status_command_renders_detached_status(monkeypatch, tmp_path):
    monkeypatch.setattr("opensprite.cli.commands.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "opensprite.cli.commands.service_background.get_service_status",
        lambda: service_background.BackgroundServiceStatus(
            running=True,
            pid=4321,
            pid_file=tmp_path / "gateway.pid",
            log_file=tmp_path / "logs" / "gateway.log",
        ),
    )

    result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 0
    assert "Mode: detached process" in result.stdout
    assert "Active: yes" in result.stdout
    assert "PID: 4321" in result.stdout


def test_linux_service_status_uses_detached_when_unit_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr("opensprite.cli.commands.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "opensprite.cli.commands.service_linux.get_service_file_path",
        lambda: tmp_path / "missing.service",
    )
    monkeypatch.setattr(
        "opensprite.cli.commands.service_background.get_service_status",
        lambda: service_background.BackgroundServiceStatus(
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
