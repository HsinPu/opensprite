"""Detached background gateway process support for non-systemd platforms."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time


@dataclass
class BackgroundServiceStatus:
    running: bool
    pid: int | None
    pid_file: Path
    log_file: Path


def get_app_home(home: Path | None = None) -> Path:
    """Return the OpenSprite app home used for background process metadata."""
    return Path(home).expanduser() if home is not None else Path.home() / ".opensprite"


def get_pid_file(home: Path | None = None) -> Path:
    """Return the detached gateway PID file path."""
    return get_app_home(home) / "gateway.pid"


def get_log_file(home: Path | None = None) -> Path:
    """Return the detached gateway log file path."""
    return get_app_home(home) / "logs" / "gateway.log"


def _read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def is_process_running(pid: int | None) -> bool:
    """Return whether a process id appears to still be alive."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def resolve_gateway_python(python_executable: Path | None = None) -> Path:
    """Return the Python executable that should run the detached gateway."""
    if python_executable is not None:
        return Path(python_executable).expanduser().resolve()

    install_dir = Path(os.getenv("OPENSPRITE_INSTALL_DIR", "~/.local/share/opensprite/opensprite")).expanduser()
    for candidate in (
        install_dir / ".venv" / "bin" / "python",
        install_dir / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate.resolve()

    return Path(sys.executable).expanduser().resolve()


def _cleanup_stale_pid(pid_file: Path) -> None:
    pid = _read_pid(pid_file)
    if pid is not None and is_process_running(pid):
        return
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def start_service(
    *,
    config_path: Path | None = None,
    home: Path | None = None,
    python_executable: Path | None = None,
    popen=subprocess.Popen,
    startup_timeout: float = 1.0,
) -> BackgroundServiceStatus:
    """Start the gateway as a detached background process."""
    app_home = get_app_home(home)
    pid_file = get_pid_file(app_home)
    log_file = get_log_file(app_home)
    app_home.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_pid(pid_file)

    existing_pid = _read_pid(pid_file)
    if is_process_running(existing_pid):
        raise RuntimeError(f"OpenSprite gateway is already running (PID {existing_pid}).")

    python_path = resolve_gateway_python(python_executable)
    command = [str(python_path), "-m", "opensprite", "gateway"]
    if config_path is not None:
        command.extend(["--config", str(Path(config_path).expanduser().resolve())])

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    creationflags = 0
    log_handle = log_file.open("ab")
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "cwd": str(app_home),
        "env": env,
    }
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = popen(command, **popen_kwargs)
    finally:
        log_handle.close()

    if startup_timeout > 0:
        time.sleep(startup_timeout)
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                "OpenSprite gateway exited during startup "
                f"(exit code {return_code}). Check the log: {log_file}"
            )

    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return BackgroundServiceStatus(running=True, pid=process.pid, pid_file=pid_file, log_file=log_file)


def stop_service(
    *,
    home: Path | None = None,
    timeout: float = 10.0,
    run=subprocess.run,
) -> None:
    """Stop the detached gateway process if it is running."""
    pid_file = get_pid_file(home)
    pid = _read_pid(pid_file)
    if not is_process_running(pid):
        _cleanup_stale_pid(pid_file)
        raise FileNotFoundError("OpenSprite background gateway is not running.")

    assert pid is not None
    if platform.system() == "Windows":
        run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, text=True, check=False)
    else:
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_running(pid):
            break
        time.sleep(0.2)
    if is_process_running(pid):
        raise RuntimeError(f"OpenSprite gateway did not stop within {timeout:g} seconds (PID {pid}).")

    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def get_service_status(*, home: Path | None = None) -> BackgroundServiceStatus:
    """Return detached gateway runtime status."""
    pid_file = get_pid_file(home)
    log_file = get_log_file(home)
    pid = _read_pid(pid_file)
    running = is_process_running(pid)
    if pid is not None and not running:
        _cleanup_stale_pid(pid_file)
        pid = None
    return BackgroundServiceStatus(running=running, pid=pid, pid_file=pid_file, log_file=log_file)


__all__ = [
    "BackgroundServiceStatus",
    "get_log_file",
    "get_pid_file",
    "get_service_status",
    "is_process_running",
    "start_service",
    "stop_service",
]
