"""Frontend build and browser runtime helpers for the web adapter."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping


def is_frontend_source_dir(path: Path) -> bool:
    return (path / "package.json").is_file()


def resolve_frontend_source_dir(config: Mapping[str, Any], *, module_path: Path) -> Path | None:
    configured = str(config.get("frontend_source_dir", "") or "").strip()
    configured_static = str(config.get("static_dir", "") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    if configured_static:
        candidates.append(Path(configured_static).expanduser())

    if not configured_static:
        candidates.extend(
            [
                module_path.parents[3] / "apps" / "web",
                Path.cwd() / "apps" / "web",
            ]
        )

    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        if is_frontend_source_dir(resolved):
            return resolved
    return None


def trim_process_output(value: str | None, limit: int = 2000) -> str:
    if not value:
        return ""
    stripped = value.strip()
    if len(stripped) <= limit:
        return stripped
    return f"...{stripped[-limit:]}"


def resolve_npm_executable() -> str | None:
    preferred = "npm.cmd" if os.name == "nt" else "npm"
    return shutil.which(preferred) or shutil.which("npm")


def is_feature_enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def frontend_dependencies_ready(source_dir: Path) -> bool:
    bin_dir = source_dir / "node_modules" / ".bin"
    return (bin_dir / "vite").is_file() or (bin_dir / "vite.cmd").is_file()


def build_frontend_run_kwargs() -> dict[str, object]:
    run_kwargs: dict[str, object] = {}
    if os.name == "nt":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_type is not None:
            startupinfo = startupinfo_type()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = 0
            run_kwargs["startupinfo"] = startupinfo
    return run_kwargs


def run_frontend_command(source_dir: Path, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=source_dir,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **build_frontend_run_kwargs(),
    )


def maybe_install_frontend_dependencies(
    source_dir: Path,
    npm: str,
    *,
    auto_install_enabled: bool,
    install_timeout: int,
    logger,
) -> bool:
    if frontend_dependencies_ready(source_dir):
        return True
    if not auto_install_enabled:
        logger.warning("Skipping web frontend dependency install because frontend_auto_install is disabled")
        return False

    install_command = [npm, "ci"] if (source_dir / "package-lock.json").is_file() else [npm, "install"]
    logger.info("Installing web frontend dependencies before build: {}", source_dir)
    try:
        result = run_frontend_command(source_dir, install_command, install_timeout)
    except subprocess.TimeoutExpired:
        logger.warning("Web frontend dependency install timed out after {} seconds", install_timeout)
        return False
    except OSError as exc:
        logger.warning("Web frontend dependency install could not start: {}", exc)
        return False

    if result.returncode != 0:
        logger.warning(
            "Web frontend dependency install failed with exit code {} | stdout={} | stderr={}",
            result.returncode,
            trim_process_output(result.stdout),
            trim_process_output(result.stderr),
        )
        return False

    return True


def maybe_build_frontend(
    config: Mapping[str, Any],
    *,
    default_config: Mapping[str, Any],
    module_path: Path,
    build_timeout: int,
    install_timeout: int,
    logger,
) -> None:
    if not is_feature_enabled(config.get("frontend_auto_build", default_config["frontend_auto_build"])):
        return

    source_dir = resolve_frontend_source_dir(config, module_path=module_path)
    if source_dir is None:
        return

    npm = resolve_npm_executable()
    if npm is None:
        logger.warning("Skipping web frontend build because npm was not found")
        return

    auto_install_enabled = is_feature_enabled(config.get("frontend_auto_install", default_config["frontend_auto_install"]))
    if not maybe_install_frontend_dependencies(
        source_dir,
        npm,
        auto_install_enabled=auto_install_enabled,
        install_timeout=install_timeout,
        logger=logger,
    ):
        return

    logger.info("Building web frontend before gateway startup: {}", source_dir)
    try:
        result = run_frontend_command(source_dir, [npm, "run", "build"], build_timeout)
    except subprocess.TimeoutExpired:
        logger.warning("Web frontend build timed out after {} seconds", build_timeout)
        return
    except OSError as exc:
        logger.warning("Web frontend build could not start: {}", exc)
        return

    if result.returncode != 0:
        logger.warning(
            "Web frontend build failed with exit code {} | stdout={} | stderr={}",
            result.returncode,
            trim_process_output(result.stdout),
            trim_process_output(result.stderr),
        )
        return

    logger.info("Web frontend build completed")


def resolve_frontend_dir(config: Mapping[str, Any], *, module_path: Path) -> Path | None:
    configured = str(config.get("static_dir", "") or "").strip()
    candidates: list[Path] = []
    if configured:
        resolved = Path(configured).expanduser().resolve(strict=False)
        candidates.append(resolved / "dist" if is_frontend_source_dir(resolved) else resolved)

    candidates.extend(
        [
            module_path.parents[3] / "apps" / "web" / "dist",
            Path.cwd() / "apps" / "web" / "dist",
        ]
    )

    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        if (resolved / "index.html").is_file():
            return resolved
    return None


def browser_command_prefix() -> list[str]:
    agent_browser = shutil.which("agent-browser")
    if agent_browser:
        return [agent_browser]

    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx:
        return [npx, "agent-browser"]
    return []


def browser_runtime_status(command_prefix: list[str]) -> dict[str, Any]:
    if command_prefix and len(command_prefix) == 1:
        return {"available": True, "command": command_prefix[0], "install_hint": ""}
    if command_prefix:
        return {
            "available": True,
            "command": " ".join(command_prefix),
            "install_hint": "agent-browser is not on PATH; OpenSprite will fall back to npx agent-browser.",
        }
    return {
        "available": False,
        "command": "",
        "install_hint": "Install agent-browser on PATH, or install Node.js/npm so npx agent-browser can run.",
    }


async def run_browser_doctor_command(
    args: list[str],
    *,
    timeout: int = 20,
    launch_args: str = "",
    command_prefix: list[str] | None = None,
) -> dict[str, Any]:
    effective_prefix = list(command_prefix or browser_command_prefix())
    if not effective_prefix:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "agent-browser and npx were not found."}
    effective_launch_args = str(launch_args or "").strip()
    global_args = ["--args", effective_launch_args] if effective_launch_args and args != ["--version"] else []
    argv = [*effective_prefix, *global_args, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=max(1, timeout))
    except asyncio.TimeoutError:
        return with_browser_diagnostic({"ok": False, "exit_code": None, "stdout": "", "stderr": f"command timed out after {timeout}s"})
    except OSError as exc:
        return with_browser_diagnostic({"ok": False, "exit_code": None, "stdout": "", "stderr": str(exc)})
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    return with_browser_diagnostic(
        {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout_text[-4000:],
            "stderr": stderr_text[-4000:],
        }
    )


async def run_browser_install_command(*, timeout: int = 300, command_prefix: list[str] | None = None) -> dict[str, Any]:
    return await run_browser_doctor_command(["install"], timeout=timeout, command_prefix=command_prefix)


def with_browser_diagnostic(result: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(result or {})
    text = "\n".join(str(payload.get(key) or "") for key in ("error", "stderr", "stdout")).lower()
    if payload.get("ok") is True or payload.get("success") is True:
        payload["diagnostic_code"] = "ok"
        payload["suggestion"] = ""
    elif "agent-browser and npx were not found" in text or "agent-browser cli was not found" in text:
        payload["diagnostic_code"] = "cli_missing"
        payload["suggestion"] = "Install agent-browser on PATH, or install Node.js/npm so OpenSprite can run npx agent-browser."
    elif "timed out" in text or "timeout" in text:
        payload["diagnostic_code"] = "timeout"
        payload["suggestion"] = "Increase command timeout or retry after closing stale browser sessions."
    elif "no usable sandbox" in text or "--no-sandbox" in text:
        payload["diagnostic_code"] = "sandbox_unavailable"
        payload["suggestion"] = "Use Browser launch args: --no-sandbox, or fix Linux Chromium sandbox support."
    elif "install --with-deps" in text or "missing dependencies" in text or "system dependencies" in text:
        payload["diagnostic_code"] = "system_deps_missing"
        payload["suggestion"] = "Run agent-browser install --with-deps on Linux, then retry the browser test."
    elif "executable" in text and ("not found" in text or "doesn't exist" in text or "missing" in text):
        payload["diagnostic_code"] = "browser_missing"
        payload["suggestion"] = "Run agent-browser install to download the managed Chromium browser."
    elif "cdp" in text and ("refused" in text or "unreachable" in text or "failed" in text or "could not" in text):
        payload["diagnostic_code"] = "cdp_unreachable"
        payload["suggestion"] = "Check the Chrome CDP URL or start Chrome with remote debugging enabled."
    else:
        payload["diagnostic_code"] = "unknown"
        payload["suggestion"] = "Review stdout/stderr and run agent-browser doctor locally for more detail."
    return payload
