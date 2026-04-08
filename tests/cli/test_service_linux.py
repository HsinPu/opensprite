from pathlib import Path
import subprocess

from typer.testing import CliRunner

from opensprite.cli.commands import app
from opensprite.cli import service_linux


runner = CliRunner()


def test_build_service_unit_uses_python_and_config_paths(tmp_path):
    config_path = tmp_path / "opensprite.json"
    python_path = Path("/opt/venv/bin/python").expanduser().resolve()
    unit = service_linux.build_service_unit(
        config_path,
        python_executable=python_path,
    )

    assert "Description=OpenSprite Gateway" in unit
    assert "ExecStart=" in unit
    assert f"{python_path}" in unit
    assert " -m opensprite gateway --config " in unit
    assert str(config_path.resolve()) in unit
    assert "WorkingDirectory=" in unit
    assert str(config_path.parent.resolve()) in unit
    assert "WantedBy=default.target" in unit


def test_install_service_writes_unit_and_runs_systemctl_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(service_linux.platform, "system", lambda: "Linux")
    config_path = tmp_path / "opensprite.json"
    config_path.write_text("{}", encoding="utf-8")
    python_path = Path("/usr/bin/python3").expanduser().resolve()
    calls = []

    def fake_runner(args, check=True):
        calls.append((tuple(args), check))
        return subprocess.CompletedProcess(["systemctl", "--user", *args], 0, stdout="", stderr="")

    service_file = service_linux.install_service(
        config_path,
        home=tmp_path,
        python_executable=python_path,
        systemctl_runner=fake_runner,
    )

    assert service_file.exists()
    content = service_file.read_text(encoding="utf-8")
    assert "ExecStart=" in content
    assert f"{python_path}" in content
    assert " -m opensprite gateway --config " in content
    assert calls == [
        (("daemon-reload",), True),
        (("enable", service_linux.SERVICE_NAME), True),
        (("start", service_linux.SERVICE_NAME), True),
    ]


def test_get_service_status_reports_installed_enabled_and_active(tmp_path, monkeypatch):
    monkeypatch.setattr(service_linux.platform, "system", lambda: "Linux")
    service_file = service_linux.get_service_file_path(tmp_path)
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text("[Unit]\n", encoding="utf-8")
    calls = []

    def fake_runner(args, check=True):
        calls.append((tuple(args), check))
        returncode = 0 if args[0] in {"is-enabled", "is-active"} else 1
        return subprocess.CompletedProcess(["systemctl", "--user", *args], returncode, stdout="", stderr="")

    status = service_linux.get_service_status(home=tmp_path, systemctl_runner=fake_runner)

    assert status.installed is True
    assert status.enabled is True
    assert status.active is True
    assert status.service_file == service_file
    assert calls == [
        (("is-enabled", service_linux.SERVICE_NAME), False),
        (("is-active", service_linux.SERVICE_NAME), False),
    ]


def test_service_status_command_renders_status(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "opensprite.cli.commands.service_linux.get_service_status",
        lambda: service_linux.LinuxServiceStatus(
            installed=True,
            enabled=True,
            active=False,
            service_file=tmp_path / service_linux.SERVICE_NAME,
        ),
    )

    result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 0
    assert "OpenSprite Service" in result.stdout
    assert "Installed: yes" in result.stdout
    assert "Enabled: yes" in result.stdout
    assert "Active: no" in result.stdout
