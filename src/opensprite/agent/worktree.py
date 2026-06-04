"""Worktree sandbox inspection and lifecycle helpers."""

from __future__ import annotations

import subprocess
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SANDBOX_MARKER = ".opensprite-worktree.json"
WORKTREE_SANDBOX_DISABLED_REASON = "worktree sandbox is disabled"
WORKSPACE_NOT_GIT_REPOSITORY_REASON = "workspace is not inside a git repository"
WORKTREE_SANDBOX_EXISTS_REASON = "worktree sandbox already exists"
GIT_WORKTREE_ADD_FAILED_REASON = "git worktree add failed"
MISSING_WORKTREE_MARKER_REASON = "missing OpenSprite worktree marker"
WORKTREE_MARKER_NOT_MANAGED_REASON = "worktree marker is not managed by OpenSprite"
REPOSITORY_ROOT_MISSING_REASON = "repository root no longer exists"
GIT_WORKTREE_REMOVE_FAILED_REASON = "git worktree remove failed"
GIT_COMMAND_FAILED_REASON = "git command failed"


@dataclass
class WorktreeSandboxMetadata:
    enabled: bool
    status: str
    workspace_root: str
    repository_root: str | None = None
    base_branch: str | None = None
    base_commit: str | None = None
    sandbox_path: str | None = None
    created: bool = False
    cleanup_supported: bool = False
    reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "workspace_root": self.workspace_root,
            "repository_root": self.repository_root,
            "base_branch": self.base_branch,
            "base_commit": self.base_commit,
            "sandbox_path": self.sandbox_path,
            "created": self.created,
            "cleanup_supported": self.cleanup_supported,
            "reason": self.reason,
        }


class WorktreeSandboxInspector:
    """Collect non-destructive git metadata for future isolated worktree runs."""

    def __init__(self, *, enabled: bool, workspace_root: Path):
        self.enabled = enabled
        self.workspace_root = Path(workspace_root).expanduser().resolve(strict=False)

    def inspect(self) -> WorktreeSandboxMetadata:
        if not self.enabled:
            return WorktreeSandboxMetadata(
                enabled=False,
                status="disabled",
                workspace_root=str(self.workspace_root),
                reason=WORKTREE_SANDBOX_DISABLED_REASON,
            )

        repository_root = self._git("rev-parse", "--show-toplevel")
        if repository_root is None:
            return WorktreeSandboxMetadata(
                enabled=True,
                status="unavailable",
                workspace_root=str(self.workspace_root),
                reason=WORKSPACE_NOT_GIT_REPOSITORY_REASON,
            )

        return WorktreeSandboxMetadata(
            enabled=True,
            status="ready",
            workspace_root=str(self.workspace_root),
            repository_root=repository_root,
            base_branch=self._git("rev-parse", "--abbrev-ref", "HEAD"),
            base_commit=self._git("rev-parse", "HEAD"),
        )

    def create(self, *, session_id: str, run_id: str) -> WorktreeSandboxMetadata:
        metadata = self.inspect()
        if metadata.status != "ready" or metadata.repository_root is None or metadata.base_commit is None:
            return metadata

        repository_root = Path(metadata.repository_root).resolve(strict=False)
        sandbox_path = self._sandbox_path(repository_root, session_id=session_id, run_id=run_id)
        if sandbox_path.exists():
            return WorktreeSandboxMetadata(
                enabled=True,
                status="exists",
                workspace_root=str(self.workspace_root),
                repository_root=str(repository_root),
                base_branch=metadata.base_branch,
                base_commit=metadata.base_commit,
                sandbox_path=str(sandbox_path),
                cleanup_supported=self._marker_path(sandbox_path).exists(),
                reason=WORKTREE_SANDBOX_EXISTS_REASON,
            )

        sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run_git(
            "worktree",
            "add",
            "--detach",
            str(sandbox_path),
            metadata.base_commit,
            cwd=repository_root,
            timeout=20,
        )
        if result.returncode != 0:
            return WorktreeSandboxMetadata(
                enabled=True,
                status="create_failed",
                workspace_root=str(self.workspace_root),
                repository_root=str(repository_root),
                base_branch=metadata.base_branch,
                base_commit=metadata.base_commit,
                sandbox_path=str(sandbox_path),
                reason=(result.stderr or result.stdout).strip() or GIT_WORKTREE_ADD_FAILED_REASON,
            )

        marker = {
            "managed_by": "opensprite",
            "repository_root": str(repository_root),
            "base_branch": metadata.base_branch,
            "base_commit": metadata.base_commit,
            "session_id": session_id,
            "run_id": run_id,
        }
        self._marker_path(sandbox_path).write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
        return WorktreeSandboxMetadata(
            enabled=True,
            status="created",
            workspace_root=str(self.workspace_root),
            repository_root=str(repository_root),
            base_branch=metadata.base_branch,
            base_commit=metadata.base_commit,
            sandbox_path=str(sandbox_path),
            created=True,
            cleanup_supported=True,
        )

    @classmethod
    def cleanup(cls, sandbox_path: str | Path) -> dict[str, Any]:
        path = Path(sandbox_path).expanduser().resolve(strict=False)
        marker_path = cls._marker_path(path)
        if not marker_path.exists():
            return {"ok": False, "status": "refused", "reason": MISSING_WORKTREE_MARKER_REASON, "sandbox_path": str(path)}
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "status": "refused", "reason": f"invalid worktree marker: {exc}", "sandbox_path": str(path)}
        if marker.get("managed_by") != "opensprite":
            return {"ok": False, "status": "refused", "reason": WORKTREE_MARKER_NOT_MANAGED_REASON, "sandbox_path": str(path)}
        repository_root = Path(str(marker.get("repository_root") or "")).expanduser().resolve(strict=False)
        if not repository_root.exists():
            return {"ok": False, "status": "refused", "reason": REPOSITORY_ROOT_MISSING_REASON, "sandbox_path": str(path)}

        result = cls._run_git("worktree", "remove", str(path), cwd=repository_root, timeout=20)
        if result.returncode != 0:
            return {
                "ok": False,
                "status": "remove_failed",
                "reason": (result.stderr or result.stdout).strip() or GIT_WORKTREE_REMOVE_FAILED_REASON,
                "sandbox_path": str(path),
                "repository_root": str(repository_root),
            }
        try:
            marker_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": True, "status": "removed", "sandbox_path": str(path), "repository_root": str(repository_root)}

    @staticmethod
    def _slug(value: str) -> str:
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)[:80] or "sandbox"

    @classmethod
    def _sandbox_path(cls, repository_root: Path, *, session_id: str, run_id: str) -> Path:
        root = repository_root.parent / f"{repository_root.name}.opensprite-worktrees"
        return root / cls._slug(session_id) / cls._slug(run_id)

    @staticmethod
    def _marker_path(sandbox_path: Path) -> Path:
        return sandbox_path.with_name(f"{sandbox_path.name}{SANDBOX_MARKER}")

    def _git(self, *args: str) -> str | None:
        result = self._run_git(*args, cwd=self.workspace_root, timeout=5)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    @staticmethod
    def _run_git(*args: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr=GIT_COMMAND_FAILED_REASON)
