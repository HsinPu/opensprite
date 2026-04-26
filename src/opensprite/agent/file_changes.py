"""Run file-change recording and safe revert helpers."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..storage import StorageProvider, StoredRunFileChange
from ..utils.log import logger
from .run_trace import json_safe_event_payload


RUN_FILE_REVERT_DIFF_MAX_CHARS = 12_000

WorkspaceForChat = Callable[[str], Path]
EventEmitter = Callable[..., Awaitable[None]]
PreviewFormatter = Callable[[str | list[dict[str, Any]] | None, int], str]


@dataclass(frozen=True)
class PreparedRunFileChangeRevert:
    """Prepared state for safely previewing or applying one file-change revert."""

    preview: dict[str, Any]
    change: StoredRunFileChange | None = None
    file_path: Path | None = None
    target_content: str | None = None


def text_sha256(content: str) -> str:
    """Return a stable UTF-8 SHA256 hash for text content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def format_revert_diff(path: str, before: str | None, after: str | None) -> str:
    """Return a bounded unified diff for the proposed revert."""
    if before == after:
        return "(no changes)"

    before_text = before or ""
    after_text = after or ""
    fromfile = "/dev/null" if before is None else f"a/{path}"
    tofile = "/dev/null" if after is None else f"b/{path}"
    diff = "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    if not diff:
        if before is None:
            diff = f"--- /dev/null\n+++ b/{path}\n@@\n(empty file created)"
        elif after is None:
            diff = f"--- a/{path}\n+++ /dev/null\n@@\n(empty file deleted)"
        else:
            diff = "(no changes)"
    if len(diff) > RUN_FILE_REVERT_DIFF_MAX_CHARS:
        return diff[:RUN_FILE_REVERT_DIFF_MAX_CHARS] + f"\n... (diff truncated, total {len(diff)} chars)"
    return diff


class RunFileChangeService:
    """Records file mutations and evaluates guarded single-file revert operations."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        workspace_for_chat: WorkspaceForChat,
        emit_run_event: EventEmitter,
        format_log_preview: PreviewFormatter,
    ):
        self.storage = storage
        self._workspace_for_chat = workspace_for_chat
        self._emit_run_event = emit_run_event
        self._format_log_preview = format_log_preview

    async def record_changes(
        self,
        tool_name: str,
        changes: list[dict[str, Any]],
        *,
        chat_id: str | None,
        run_id: str | None,
        channel: str | None = None,
        transport_chat_id: str | None = None,
    ) -> None:
        """Persist file mutations for the active run when available."""
        if not chat_id or not run_id or not changes:
            return

        add_change = getattr(self.storage, "add_run_file_change", None)
        if not callable(add_change):
            return

        for raw_change in changes:
            path = str(raw_change.get("path") or "").strip()
            action = str(raw_change.get("action") or "").strip()
            if not path or not action:
                continue

            diff = str(raw_change.get("diff") or "")
            raw_metadata = raw_change.get("metadata")
            metadata = json_safe_event_payload(raw_metadata if isinstance(raw_metadata, dict) else {})
            metadata.setdefault("diff_len", len(diff))
            try:
                await add_change(
                    chat_id,
                    run_id,
                    tool_name,
                    path,
                    action,
                    before_sha256=raw_change.get("before_sha256"),
                    after_sha256=raw_change.get("after_sha256"),
                    before_content=raw_change.get("before_content"),
                    after_content=raw_change.get("after_content"),
                    diff=diff,
                    metadata=metadata,
                )
            except Exception as e:
                logger.warning(
                    "[{}] run.file-change.persist.failed | run_id={} tool={} path={} error={}",
                    chat_id,
                    run_id,
                    tool_name,
                    path,
                    e,
                )
                continue

            await self._emit_run_event(
                chat_id,
                run_id,
                "file_changed",
                {
                    "tool_name": tool_name,
                    "path": path,
                    "action": action,
                    "before_sha256": raw_change.get("before_sha256"),
                    "after_sha256": raw_change.get("after_sha256"),
                    "diff_len": len(diff),
                    "diff_preview": self._format_log_preview(diff, 240),
                },
                channel=channel,
                transport_chat_id=transport_chat_id,
            )

    def _resolve_change_path(self, chat_id: str, path: str) -> tuple[Path | None, str | None]:
        """Resolve a stored run file-change path and keep it inside the chat workspace."""
        raw_path = str(path or "").strip()
        if not raw_path:
            return None, "stored file-change path is empty"

        workspace = self._workspace_for_chat(chat_id).resolve(strict=False)
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(workspace)
        except ValueError:
            return None, f"stored file-change path escapes workspace: {raw_path}"
        return candidate, None

    @staticmethod
    def _read_current_content(file_path: Path) -> tuple[str | None, str | None, str | None]:
        """Read current text content for revert checks; return content, sha, error."""
        if not file_path.exists():
            return None, None, None
        if not file_path.is_file():
            return None, None, "path exists but is not a file"
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None, None, "current file is not valid UTF-8 text"
        except OSError as e:
            return None, None, f"failed to read current file: {e}"
        return content, text_sha256(content), None

    async def prepare_revert(
        self,
        chat_id: str,
        run_id: str,
        change_id: int,
    ) -> PreparedRunFileChangeRevert:
        """Build a dry-run preview and required state for one guarded revert."""
        getter = getattr(self.storage, "get_run_file_change", None)
        if not callable(getter):
            return PreparedRunFileChangeRevert(
                preview={
                    "status": "unavailable",
                    "ok": False,
                    "chat_id": chat_id,
                    "run_id": run_id,
                    "change_id": change_id,
                    "reason": "storage does not support run file-change lookup",
                }
            )

        try:
            normalized_change_id = int(change_id)
        except (TypeError, ValueError):
            return PreparedRunFileChangeRevert(
                preview={
                    "status": "not_found",
                    "ok": False,
                    "chat_id": chat_id,
                    "run_id": run_id,
                    "change_id": change_id,
                    "reason": "change_id must be an integer",
                }
            )

        change = await getter(chat_id, run_id, normalized_change_id)
        if change is None:
            return PreparedRunFileChangeRevert(
                preview={
                    "status": "not_found",
                    "ok": False,
                    "chat_id": chat_id,
                    "run_id": run_id,
                    "change_id": normalized_change_id,
                    "reason": "file change was not found for this run",
                }
            )

        file_path, path_error = self._resolve_change_path(chat_id, change.path)
        base_preview = self._base_revert_preview(chat_id, run_id, normalized_change_id, change)
        if path_error or file_path is None:
            return PreparedRunFileChangeRevert(
                preview={
                    **base_preview,
                    "status": "invalid_path",
                    "ok": False,
                    "reason": path_error or "invalid stored file-change path",
                },
                change=change,
                file_path=file_path,
            )

        current_content, current_sha, current_error = self._read_current_content(file_path)
        target_content = change.before_content
        preview = {
            **base_preview,
            "absolute_path": str(file_path),
            "current_exists": current_content is not None,
            "current_sha256": current_sha,
        }

        failure = self._validate_revert_preconditions(change, current_content, current_sha, current_error, target_content)
        if failure is not None:
            if failure.get("include_diff"):
                failure["diff"] = format_revert_diff(change.path, current_content, target_content)
                failure.pop("include_diff", None)
            return PreparedRunFileChangeRevert(
                preview={**preview, **failure},
                change=change,
                file_path=file_path,
                target_content=target_content,
            )

        return PreparedRunFileChangeRevert(
            preview={
                **preview,
                "status": "ready",
                "ok": True,
                "reason": "ready to revert",
                "diff": format_revert_diff(change.path, current_content, target_content),
            },
            change=change,
            file_path=file_path,
            target_content=target_content,
        )

    @staticmethod
    def _base_revert_preview(
        chat_id: str,
        run_id: str,
        change_id: int,
        change: StoredRunFileChange,
    ) -> dict[str, Any]:
        return {
            "chat_id": chat_id,
            "run_id": run_id,
            "change_id": change_id,
            "path": change.path,
            "tool_name": change.tool_name,
            "original_action": change.action,
            "before_sha256": change.before_sha256,
            "after_sha256": change.after_sha256,
            "expected_current_sha256": change.after_sha256,
            "target_sha256": change.before_sha256,
            "current_exists": False,
            "target_exists": change.before_sha256 is not None,
            "current_sha256": None,
            "revert_action": "delete" if change.before_sha256 is None else "write",
            "diff": "",
        }

    @staticmethod
    def _validate_revert_preconditions(
        change: StoredRunFileChange,
        current_content: str | None,
        current_sha: str | None,
        current_error: str | None,
        target_content: str | None,
    ) -> dict[str, Any] | None:
        if current_error:
            return {"status": "conflict", "ok": False, "reason": current_error}

        if change.before_sha256 is not None:
            if target_content is None:
                return {
                    "status": "unavailable",
                    "ok": False,
                    "reason": "stored before_content snapshot is unavailable; cannot safely reconstruct the file",
                }
            target_sha = text_sha256(target_content)
            if target_sha != change.before_sha256:
                return {
                    "status": "unavailable",
                    "ok": False,
                    "reason": "stored before_content snapshot hash does not match before_sha256",
                }

        if change.after_sha256 is None:
            if current_content is not None:
                return {
                    "status": "conflict",
                    "ok": False,
                    "reason": "current file exists but the recorded post-change state expected it to be missing",
                    "include_diff": True,
                }
        elif current_content is None:
            return {
                "status": "conflict",
                "ok": False,
                "reason": "current file is missing but the recorded post-change state expected file content",
            }
        elif current_sha != change.after_sha256:
            return {
                "status": "conflict",
                "ok": False,
                "reason": "current file hash does not match the recorded post-change hash",
                "include_diff": True,
            }

        return None

    async def preview_revert(self, chat_id: str, run_id: str, change_id: int) -> dict[str, Any]:
        """Inspect whether one captured file change can be safely reverted."""
        prepared = await self.prepare_revert(chat_id, run_id, change_id)
        return prepared.preview

    async def revert(
        self,
        chat_id: str,
        run_id: str,
        change_id: int,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Safely revert one captured file change; defaults to dry-run inspection."""
        prepared = await self.prepare_revert(chat_id, run_id, change_id)
        result = {**prepared.preview, "dry_run": bool(dry_run), "applied": False}
        if dry_run or prepared.preview.get("status") != "ready" or prepared.file_path is None:
            return result

        current_content, current_sha, current_error = self._read_current_content(prepared.file_path)
        if current_error:
            result.update({"status": "conflict", "ok": False, "reason": current_error})
            return result

        expected_current_sha = prepared.preview.get("expected_current_sha256")
        if expected_current_sha is None:
            if current_content is not None:
                result.update({"status": "conflict", "ok": False, "reason": "current file changed before revert apply"})
                return result
        elif current_sha != expected_current_sha:
            result.update(
                {
                    "status": "conflict",
                    "ok": False,
                    "reason": "current file changed before revert apply",
                    "current_sha256": current_sha,
                }
            )
            return result

        try:
            if prepared.target_content is None:
                if prepared.file_path.exists():
                    prepared.file_path.unlink()
                post_sha = None
            else:
                prepared.file_path.parent.mkdir(parents=True, exist_ok=True)
                prepared.file_path.write_text(prepared.target_content, encoding="utf-8")
                post_sha = text_sha256(prepared.target_content)
        except OSError as e:
            result.update({"status": "failed", "ok": False, "reason": f"failed to apply revert: {e}"})
            return result

        result.update(
            {
                "status": "applied",
                "ok": True,
                "applied": True,
                "post_sha256": post_sha,
                "reason": "file change reverted",
            }
        )
        return result
