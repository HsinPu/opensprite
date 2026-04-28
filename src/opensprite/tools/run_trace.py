"""Read-only tools for inspecting run file-change snapshots."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from ..storage import StorageProvider, StoredRunFileChange
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


SessionIdGetter = Callable[[], str | None]
RevertPreviewer = Callable[[str, str, int], Awaitable[dict[str, Any]]]

_DEFAULT_RUN_LIMIT = 20
_MAX_RUN_LIMIT = 100
_DEFAULT_CHANGE_LIMIT = 50
_MAX_CHANGE_LIMIT = 200
_DIFF_PREVIEW_CHARS = 1200


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _snapshot_available(change: StoredRunFileChange) -> dict[str, bool]:
    return {
        "before": change.before_content is not None,
        "after": change.after_content is not None,
    }


def _diff_preview(diff: str) -> str:
    text = str(diff or "")
    if len(text) <= _DIFF_PREVIEW_CHARS:
        return text
    return text[:_DIFF_PREVIEW_CHARS] + f"\n... (diff truncated, total {len(text)} chars)"


def _change_payload(change: StoredRunFileChange, *, include_diff: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": change.run_id,
        "change_id": change.change_id,
        "path": change.path,
        "action": change.action,
        "tool_name": change.tool_name,
        "before_sha256": change.before_sha256,
        "after_sha256": change.after_sha256,
        "snapshots_available": _snapshot_available(change),
        "created_at": change.created_at,
    }
    if include_diff:
        payload["diff_preview"] = _diff_preview(change.diff)
    return payload


class ListRunFileChangesTool(Tool):
    """List file changes captured for the current session's recent runs."""

    name = "list_run_file_changes"
    description = (
        "List file changes captured in durable run traces for the current session. "
        "Use this to find run_id and change_id before previewing a safe revert. "
        "Read-only; it never modifies files."
    )

    def __init__(self, *, storage: StorageProvider, get_chat_id: SessionIdGetter):
        self.storage = storage
        self.get_chat_id = get_chat_id

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Optional. Specific run id to inspect. If omitted, recent runs for the current session are scanned.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "run_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_RUN_LIMIT,
                    "description": f"Optional. Number of recent runs to scan when run_id is omitted. Defaults to {_DEFAULT_RUN_LIMIT}.",
                },
                "change_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_CHANGE_LIMIT,
                    "description": f"Optional. Maximum number of file-change records to return. Defaults to {_DEFAULT_CHANGE_LIMIT}.",
                },
                "include_diffs": {
                    "type": "boolean",
                    "description": "Optional. Include truncated diff previews for each file change. Defaults to false.",
                },
            },
        }

    async def _execute(self, **kwargs: Any) -> str:
        session_id = self.get_chat_id()
        if not session_id:
            return "Error: current session_id is unavailable. list_run_file_changes requires an active session context."

        include_diffs = bool(kwargs.get("include_diffs", False))
        change_limit = min(max(int(kwargs.get("change_limit") or _DEFAULT_CHANGE_LIMIT), 1), _MAX_CHANGE_LIMIT)
        run_id = str(kwargs.get("run_id") or "").strip()
        if run_id:
            changes = await self.storage.get_run_file_changes(session_id, run_id)
            return _json_result(
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "count": min(len(changes), change_limit),
                    "total_count": len(changes),
                    "file_changes": [
                        _change_payload(change, include_diff=include_diffs)
                        for change in changes[:change_limit]
                    ],
                }
            )

        run_limit = min(max(int(kwargs.get("run_limit") or _DEFAULT_RUN_LIMIT), 1), _MAX_RUN_LIMIT)
        runs = await self.storage.get_runs(session_id, limit=run_limit)
        collected: list[dict[str, Any]] = []
        scanned_runs = 0
        for run in runs:
            scanned_runs += 1
            for change in await self.storage.get_run_file_changes(session_id, run.run_id):
                collected.append(_change_payload(change, include_diff=include_diffs))
                if len(collected) >= change_limit:
                    break
            if len(collected) >= change_limit:
                break

        return _json_result(
            {
                "session_id": session_id,
                "run_id": None,
                "scanned_runs": scanned_runs,
                "count": len(collected),
                "file_changes": collected,
            }
        )


class PreviewRunFileChangeRevertTool(Tool):
    """Preview whether a captured file change can be safely reverted."""

    name = "preview_run_file_change_revert"
    description = (
        "Preview whether one captured file change can be safely reverted in the current session workspace. "
        "Requires run_id and change_id from list_run_file_changes. Read-only dry-run; it never modifies files."
    )

    def __init__(self, *, get_chat_id: SessionIdGetter, preview_revert: RevertPreviewer):
        self.get_chat_id = get_chat_id
        self.preview_revert = preview_revert

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Required. Run id that owns the file-change record.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "change_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Required. Numeric file-change id from list_run_file_changes.",
                },
            },
            "required": ["run_id", "change_id"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        session_id = self.get_chat_id()
        if not session_id:
            return "Error: current session_id is unavailable. preview_run_file_change_revert requires an active session context."
        preview = await self.preview_revert(session_id, str(kwargs["run_id"]), int(kwargs["change_id"]))
        return _json_result(preview)
