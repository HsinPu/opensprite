"""Minimal Linux systemd user service support for OpenSprite."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys


SERVICE_NAME = "opensprite-gateway.service"


@dataclass
class LinuxServiceStatus:
    installed: bool
    enabled: bool
    active: bool
    service_file: Path


def ensure_linux() -> None:
    """Raise when Linux-only service management is unavailable."""
    if platform.system() != "Linux":
        raise RuntimeError("Linux service commands are only supported on Linux.")


def get_user_service_dir(home: Path | None = None) -> Path:
    """Return the systemd user service directory."""
    resolved_home = Path(home).expanduser() if home is not None else Path.home()
    return resolved_home / ".config" / "systemd" / "user"


def get_service_file_path(home: Path | None = None) -> Path:
    """Return the OpenSprite systemd user service file path."""
    return get_user_service_dir(home) / SERVICE_NAME


def get_default_config_path(home: Path | None = None) -> Path:
    """Return the default OpenSprite config path."""
    resolved_home = Path(home).expanduser() if home is not None else Path.home()
    return resolved_home / ".opensprite" / "opensprite.json"


def resolve_opensprite_executable() -> Path:
    """Return the CLI executable that can import the installed OpenSprite package."""
    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.name.startswith("opensprite") and argv0.exists():
        return argv0.resolve()

    discovered = shutil.which("opensprite")
    if discovered:
        return Path(discovered).expanduser().resolve()

    raise RuntimeError("Could not resolve the opensprite executable for the Linux service.")


def build_service_unit(
    config_path: Path,
    *,
    opensprite_executable: Path | None = None,
    python_executable: Path | None = None,
) -> str:
    """Build the systemd user service unit content."""
    config_path = Path(config_path).expanduser().resolve()
    if python_executable is not None:
        python_path = Path(python_executable).expanduser().resolve()
        exec_args = [str(python_path), "-m", "opensprite", "gateway", "--config", str(config_path)]
    else:
        executable_path = Path(opensprite_executable).expanduser().resolve() if opensprite_executable else resolve_opensprite_executable()
        exec_args = [str(executable_path), "gateway", "--config", str(config_path)]
    exec_start = shlex.join(exec_args)
    working_directory = shlex.quote(str(config_path.parent))
    return (
        "[Unit]\n"
        "Description=OpenSprite Gateway\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={working_directory}\n"
        f"ExecStart={exec_start}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _run_systemctl_user(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a systemctl --user command and optionally raise on failure."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("systemctl is not available on this system.") from exc

    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "systemctl command failed"
        raise RuntimeError(message)
    return result


def install_service(
    config_path: Path,
    *,
    start: bool = True,
    home: Path | None = None,
    opensprite_executable: Path | None = None,
    python_executable: Path | None = None,
    systemctl_runner=_run_systemctl_user,
) -> Path:
    """Install and optionally start the OpenSprite systemd user service."""
    ensure_linux()
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    service_file = get_service_file_path(home)
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text(
        build_service_unit(
            config_path,
            opensprite_executable=opensprite_executable,
            python_executable=python_executable,
        ),
        encoding="utf-8",
    )
    systemctl_runner(["daemon-reload"])
    systemctl_runner(["enable", SERVICE_NAME])
    if start:
        systemctl_runner(["start", SERVICE_NAME])
    return service_file


def uninstall_service(
    *,
    home: Path | None = None,
    systemctl_runner=_run_systemctl_user,
) -> bool:
    """Uninstall the OpenSprite systemd user service."""
    ensure_linux()
    service_file = get_service_file_path(home)
    if service_file.exists():
        systemctl_runner(["stop", SERVICE_NAME], check=False)
        systemctl_runner(["disable", SERVICE_NAME], check=False)
        service_file.unlink()
        systemctl_runner(["daemon-reload"])
        return True
    return False


def start_service(*, systemctl_runner=_run_systemctl_user, home: Path | None = None) -> None:
    """Start the installed OpenSprite service."""
    ensure_linux()
    if not get_service_file_path(home).exists():
        raise FileNotFoundError("OpenSprite service is not installed.")
    systemctl_runner(["start", SERVICE_NAME])


def stop_service(*, systemctl_runner=_run_systemctl_user, home: Path | None = None) -> None:
    """Stop the installed OpenSprite service."""
    ensure_linux()
    if not get_service_file_path(home).exists():
        raise FileNotFoundError("OpenSprite service is not installed.")
    systemctl_runner(["stop", SERVICE_NAME])


def restart_service(*, systemctl_runner=_run_systemctl_user, home: Path | None = None) -> None:
    """Restart the installed OpenSprite service."""
    ensure_linux()
    if not get_service_file_path(home).exists():
        raise FileNotFoundError("OpenSprite service is not installed.")
    systemctl_runner(["restart", SERVICE_NAME])


def get_service_status(
    *,
    home: Path | None = None,
    systemctl_runner=_run_systemctl_user,
) -> LinuxServiceStatus:
    """Return install, enable, and active status for the service."""
    ensure_linux()
    service_file = get_service_file_path(home)
    installed = service_file.exists()
    enabled = False
    active = False
    if installed:
        enabled = systemctl_runner(["is-enabled", SERVICE_NAME], check=False).returncode == 0
        active = systemctl_runner(["is-active", SERVICE_NAME], check=False).returncode == 0
    return LinuxServiceStatus(
        installed=installed,
        enabled=enabled,
        active=active,
        service_file=service_file,
    )


__all__ = [
    "LinuxServiceStatus",
    "SERVICE_NAME",
    "build_service_unit",
    "ensure_linux",
    "get_default_config_path",
    "get_service_file_path",
    "get_service_status",
    "get_user_service_dir",
    "install_service",
    "resolve_opensprite_executable",
    "restart_service",
    "start_service",
    "stop_service",
    "uninstall_service",
]
