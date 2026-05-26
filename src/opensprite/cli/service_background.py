"""Detached background gateway process support for non-systemd platforms."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import ctypes


@dataclass
class BackgroundServiceStatus:
    running: bool
    pid: int | None
    pid_file: Path
    log_file: Path
    startup_enabled: bool = False
    startup_task_name: str | None = None


WINDOWS_STARTUP_TASK_NAME = "OpenSprite Gateway"


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
    if platform.system() == "Windows":
        return _is_windows_process_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_windows_process_running(pid: int) -> bool:
    """Return whether a Windows process exists and has not exited."""
    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _windows_hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is None:
        return None
    startupinfo = startupinfo_type()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = 0
    return startupinfo


def resolve_gateway_python(python_executable: Path | None = None) -> Path:
    """Return the Python executable that should run the detached gateway."""
    if python_executable is not None:
        return Path(python_executable).expanduser().absolute()

    install_dir = Path(os.getenv("OPENSPRITE_INSTALL_DIR", "~/.local/share/opensprite/opensprite")).expanduser()
    for candidate in (
        install_dir / ".venv" / "bin" / "python",
        install_dir / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate.absolute()

    return Path(sys.executable).expanduser().absolute()


def _cleanup_stale_pid(pid_file: Path) -> None:
    pid = _read_pid(pid_file)
    if pid is not None and is_process_running(pid):
        return
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def _append_startup_log_preamble(log_file: Path, command: list[str], app_home: Path) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    message = (
        f"\n[{timestamp}] starting OpenSprite background gateway\n"
        f"command: {' '.join(command)}\n"
        f"cwd: {app_home}\n"
    )
    with log_file.open("ab") as handle:
        handle.write(message.encode("utf-8", errors="replace"))


def _read_log_tail(log_file: Path, limit: int = 2000) -> str:
    try:
        data = log_file.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace").strip()


def _quote_task_command_part(value: str) -> str:
    return '"' + value.replace('"', r'\"') + '"'


def _build_windows_startup_command(config_path: Path | None, *, python_executable: Path | None = None) -> str:
    python_path = resolve_gateway_python(python_executable)
    parts = [str(python_path), "-m", "opensprite", "service", "start"]
    if config_path is not None:
        parts.extend(["--config", str(Path(config_path).expanduser().resolve())])
    return " ".join(_quote_task_command_part(part) for part in parts)


def install_startup_task(
    *,
    config_path: Path | None = None,
    python_executable: Path | None = None,
    run=subprocess.run,
) -> str:
    """Register a Windows logon startup task that starts the detached gateway."""
    if platform.system() != "Windows":
        raise RuntimeError("Startup task installation is only supported on Windows.")
    task_command = _build_windows_startup_command(config_path, python_executable=python_executable)
    result = run(
        [
            "schtasks",
            "/Create",
            "/TN",
            WINDOWS_STARTUP_TASK_NAME,
            "/SC",
            "ONLOGON",
            "/TR",
            task_command,
            "/RL",
            "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Failed to register Windows startup task."
        raise RuntimeError(message)
    return WINDOWS_STARTUP_TASK_NAME


def uninstall_startup_task(*, run=subprocess.run) -> bool:
    """Remove the Windows logon startup task if it exists."""
    if platform.system() != "Windows":
        raise RuntimeError("Startup task uninstall is only supported on Windows.")
    result = run(
        ["schtasks", "/Delete", "/TN", WINDOWS_STARTUP_TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    output = f"{result.stdout}\n{result.stderr}".lower()
    if "cannot find" in output or "does not exist" in output:
        return False
    message = result.stderr.strip() or result.stdout.strip() or "Failed to remove Windows startup task."
    raise RuntimeError(message)


def is_startup_task_installed(*, run=subprocess.run) -> bool:
    """Return whether the Windows logon startup task is registered."""
    if platform.system() != "Windows":
        return False
    result = run(
        ["schtasks", "/Query", "/TN", WINDOWS_STARTUP_TASK_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


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
    _append_startup_log_preamble(log_file, command, app_home)
    log_handle = log_file.open("ab")
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "cwd": str(app_home),
        "env": env,
    }
    if platform.system() == "Windows":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        popen_kwargs["creationflags"] = creationflags
        startupinfo = _windows_hidden_startupinfo()
        if startupinfo is not None:
            popen_kwargs["startupinfo"] = startupinfo
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
            log_tail = _read_log_tail(log_file)
            tail_message = f" Recent log output: {log_tail}" if log_tail else ""
            raise RuntimeError(
                "OpenSprite gateway exited during startup "
                f"(exit code {return_code}). Check the log: {log_file}.{tail_message}"
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


def get_service_status(*, home: Path | None = None, include_startup: bool = False) -> BackgroundServiceStatus:
    """Return detached gateway runtime status."""
    pid_file = get_pid_file(home)
    log_file = get_log_file(home)
    pid = _read_pid(pid_file)
    running = is_process_running(pid)
    if pid is not None and not running:
        _cleanup_stale_pid(pid_file)
        pid = None
    startup_enabled = include_startup and is_startup_task_installed()
    return BackgroundServiceStatus(
        running=running,
        pid=pid,
        pid_file=pid_file,
        log_file=log_file,
        startup_enabled=startup_enabled,
        startup_task_name=WINDOWS_STARTUP_TASK_NAME if startup_enabled else None,
    )


__all__ = [
    "BackgroundServiceStatus",
    "get_log_file",
    "get_pid_file",
    "get_service_status",
    "install_startup_task",
    "is_process_running",
    "is_startup_task_installed",
    "start_service",
    "stop_service",
    "uninstall_startup_task",
]
