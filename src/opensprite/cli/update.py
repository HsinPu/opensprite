"""Update support for source-checkout OpenSprite installs."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ..utils.processes import windows_hidden_process_kwargs


@dataclass(frozen=True)
class UpdateResult:
    """Summary of an OpenSprite update run."""

    project_root: Path
    branch: str
    before_rev: str
    after_rev: str
    updated: bool
    python_executable: Path
    frontend_build: str | None = None


class UpdateError(RuntimeError):
    """Raised when OpenSprite cannot update safely."""


def find_project_root(start: Path | None = None) -> Path:
    """Find the repository root for the installed package."""
    current = Path(start or __file__).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / ".git").exists():
            return candidate
    raise UpdateError("OpenSprite is not installed from a git checkout; reinstall with scripts/install.sh.")


def find_python_executable(project_root: Path) -> Path:
    """Return the Python executable used for dependency installation."""
    candidates = [
        project_root / ".venv" / "bin" / "python",
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / "venv" / "bin" / "python",
        project_root / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _run(
    args: list[str],
    *,
    cwd: Path,
    runner=subprocess.run,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = runner(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        **windows_hidden_process_kwargs(),
    )
    if check and result.returncode != 0:
        output = (result.stderr or result.stdout or "command failed").strip()
        raise UpdateError(output)
    return result


def _git_output(args: list[str], *, cwd: Path, runner=subprocess.run) -> str:
    return _run(["git", *args], cwd=cwd, runner=runner).stdout.strip()


def _resolve_npm_executable() -> str | None:
    preferred = "npm.cmd" if os.name == "nt" else "npm"
    return shutil.which(preferred) or shutil.which("npm")


def build_frontend(
    project_root: Path,
    *,
    runner=subprocess.run,
    npm_executable: str | None = None,
) -> str | None:
    """Install and build the bundled web frontend when its source is present."""
    web_dir = project_root / "apps" / "web"
    if not (web_dir / "package.json").exists():
        return None

    npm = npm_executable or _resolve_npm_executable()
    if npm is None:
        raise UpdateError("npm was not found. Install Node.js 20.19+ or 22.12+ and npm, then rerun `opensprite update`.")

    install_args = [npm, "ci"] if (web_dir / "package-lock.json").exists() else [npm, "install"]
    _run(install_args, cwd=web_dir, runner=runner)
    _run([npm, "run", "build"], cwd=web_dir, runner=runner)
    return "built"


def check_update_available(
    *,
    project_root: Path | None = None,
    branch: str = "main",
    runner=subprocess.run,
) -> int:
    """Fetch origin and return the number of commits behind origin/branch."""
    root = project_root or find_project_root()
    _run(["git", "fetch", "origin"], cwd=root, runner=runner)
    raw_count = _git_output(["rev-list", f"HEAD..origin/{branch}", "--count"], cwd=root, runner=runner)
    try:
        return int(raw_count)
    except ValueError as exc:
        raise UpdateError(f"Could not parse update count: {raw_count}") from exc


def update_checkout(
    *,
    project_root: Path | None = None,
    branch: str = "main",
    install_dev: bool = False,
    runner=subprocess.run,
) -> UpdateResult:
    """Fast-forward the checkout and reinstall the package into the local venv."""
    root = project_root or find_project_root()
    if not (root / ".git").exists():
        raise UpdateError("OpenSprite is not installed from a git checkout; reinstall with scripts/install.sh.")

    dirty = _git_output(["status", "--porcelain"], cwd=root, runner=runner)
    if dirty:
        raise UpdateError(
            "Local changes are present. Commit, stash, or discard them before running `opensprite update`."
        )

    before_rev = _git_output(["rev-parse", "HEAD"], cwd=root, runner=runner)
    _run(["git", "fetch", "origin"], cwd=root, runner=runner)
    _run(["git", "checkout", branch], cwd=root, runner=runner)

    count_raw = _git_output(["rev-list", f"HEAD..origin/{branch}", "--count"], cwd=root, runner=runner)
    try:
        commit_count = int(count_raw)
    except ValueError as exc:
        raise UpdateError(f"Could not parse update count: {count_raw}") from exc

    if commit_count:
        _run(["git", "pull", "--ff-only", "origin", branch], cwd=root, runner=runner)

    python_executable = find_python_executable(root)
    install_target = ".[dev]" if install_dev else "."
    _run([str(python_executable), "-m", "pip", "install", "--upgrade", "pip"], cwd=root, runner=runner)
    _run([str(python_executable), "-m", "pip", "install", "-e", install_target], cwd=root, runner=runner)
    frontend_build = build_frontend(root, runner=runner)

    after_rev = _git_output(["rev-parse", "HEAD"], cwd=root, runner=runner)
    return UpdateResult(
        project_root=root,
        branch=branch,
        before_rev=before_rev,
        after_rev=after_rev,
        updated=before_rev != after_rev,
        python_executable=python_executable,
        frontend_build=frontend_build,
    )
