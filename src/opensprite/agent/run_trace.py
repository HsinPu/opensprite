"""Run trace persistence and event publishing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Awaitable, Callable

from ..bus.events import RunEvent
from ..runs.events import (
    FILE_CHANGED_EVENT,
    FILE_REVERT_APPLIED_EVENT,
    FILE_REVERT_FAILED_EVENT,
    FILE_REVERT_PREVIEWED_EVENT,
    FILE_REVERT_SKIPPED_EVENT,
)
from ..runs.lifecycle import (
    RUN_CANCELLED_EVENT,
    RUN_CANCELLED_STATUS,
    RUN_COMPLETED_STATUS,
    RUN_FAILED_EVENT,
    RUN_FINISHED_EVENT,
    RUN_RUNNING_STATUS,
    RUN_STARTED_EVENT,
)
from ..runs.schema import serialize_work_state_todos
from ..storage import StorageProvider, StoredRunFileChange
from ..utils.json_safe import json_safe_payload
from ..utils.log import logger
from ..utils.text_changes import format_unified_diff, text_sha256


RUN_PART_CONTENT_MAX_CHARS = 20_000
RUN_FILE_REVERT_DIFF_MAX_CHARS = 12_000
TRACE_PROFILE_FIELD = "profile"
TRACE_HARNESS_PROFILE_FIELD = "harness_profile"
TRACE_HARNESS_POLICY_FIELD = "harness_policy"
TRACE_POLICY_FIELD = "policy"
TRACE_CONTRACT_FIELD = "contract"
TRACE_COMPLETION_FIELD = "completion"
TRACE_TRACE_HEALTH_FIELD = "trace_health"
TRACE_SENSOR_COUNTS_FIELD = "sensor_counts"
TRACE_STATUS_FIELD = "status"
TRACE_NAME_FIELD = "name"
TRACE_TASK_TYPE_FIELD = "task_type"
TRACE_NEXT_ACTION_FIELD = "next_action"
TRACE_SUMMARY_FIELD = "summary"
TRACE_KIND_FIELD = "kind"
TRACE_OK_FIELD = "ok"
TRACE_PASSED_CASES_FIELD = "passed_cases"
TRACE_TOTAL_CASES_FIELD = "total_cases"
TRACE_PASSED_CHECKS_FIELD = "passed_checks"
TRACE_TOTAL_CHECKS_FIELD = "total_checks"
TRACE_OPERATION_TYPE_FIELD = "operation_type"
TRACE_TARGET_FIELD = "target"
TRACE_ROLLBACK_AVAILABLE_FIELD = "rollback_available"
TRACE_SENSOR_FAIL_FIELD = "fail"
TRACE_SENSOR_WARN_FIELD = "warn"
TRACE_SENSOR_PASS_FIELD = "pass"
WorkspaceForSession = Callable[[str], Path]
EventEmitter = Callable[..., Awaitable[None]]
PreviewFormatter = Callable[[str | list[dict[str, Any]] | None, int], str]
FileChangeRecorder = Callable[[str], None]


def truncate_run_part_content(
    content: str,
    max_chars: int = RUN_PART_CONTENT_MAX_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Bound durable run-part content while preserving useful head/tail context."""
    text = str(content or "")
    original_len = len(text)
    if original_len <= max_chars:
        return text, {"content_truncated": False, "content_original_len": original_len}

    marker = f"\n... (run part content truncated, original {original_len} chars) ...\n"
    tail_chars = max(1000, max_chars // 4)
    head_chars = max(0, max_chars - tail_chars - len(marker))
    truncated = text[:head_chars].rstrip() + marker + text[-tail_chars:].lstrip()
    return truncated, {"content_truncated": True, "content_original_len": original_len}


class RunEventSink:
    """Persists run events and publishes their live bus representation."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        message_bus_getter: Callable[[], Any | None],
    ):
        self.storage = storage
        self._message_bus_getter = message_bus_getter

    async def emit(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Persist and publish one structured run event."""
        created_at = time.time()
        safe_payload = json_safe_payload(payload)
        add_event = getattr(self.storage, "add_run_event", None)
        if callable(add_event):
            try:
                await add_event(session_id, run_id, event_type, payload=safe_payload, created_at=created_at)
            except Exception as e:
                logger.warning("[{}] run.event.persist.failed | run_id={} type={} error={}", session_id, run_id, event_type, e)

        message_bus = self._message_bus_getter()
        if message_bus is None or not channel or external_chat_id is None:
            return
        try:
            await message_bus.publish_run_event(
                RunEvent(
                    channel=channel,
                    external_chat_id=str(external_chat_id),
                    session_id=session_id,
                    run_id=run_id,
                    event_type=event_type,
                    payload=safe_payload,
                    created_at=created_at,
                )
            )
        except Exception as e:
            logger.warning("[{}] run.event.publish.failed | run_id={} type={} error={}", session_id, run_id, event_type, e)


@dataclass(frozen=True)
class PreparedRunFileChangeRevert:
    """Prepared state for safely previewing or applying one file-change revert."""

    preview: dict[str, Any]
    change: StoredRunFileChange | None = None
    file_path: Path | None = None
    target_content: str | None = None


class RunFileChangeService:
    """Records file mutations and evaluates guarded single-file revert operations."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        workspace_for_session: WorkspaceForSession,
        emit_run_event: EventEmitter,
        format_log_preview: PreviewFormatter,
        note_file_change: FileChangeRecorder | None = None,
    ):
        self.storage = storage
        self._workspace_for_session = workspace_for_session
        self._emit_run_event = emit_run_event
        self._format_log_preview = format_log_preview
        self._note_file_change = note_file_change

    async def record_changes(
        self,
        tool_name: str,
        changes: list[dict[str, Any]],
        *,
        session_id: str | None,
        run_id: str | None,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Persist file mutations for the active run when available."""
        if not session_id or not run_id or not changes:
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
            metadata = json_safe_payload(raw_metadata if isinstance(raw_metadata, dict) else {})
            metadata.setdefault("diff_len", len(diff))
            try:
                await add_change(
                    session_id,
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
                    session_id,
                    run_id,
                    tool_name,
                    path,
                    e,
                )
                continue

            await self._emit_run_event(
                session_id,
                run_id,
                FILE_CHANGED_EVENT,
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
                external_chat_id=external_chat_id,
            )
            if self._note_file_change is not None:
                try:
                    self._note_file_change(path)
                except Exception:
                    logger.exception("[{}] run.file-change.progress-hook.failed | run_id={} path={}", session_id, run_id, path)

    def _resolve_change_path(self, session_id: str, path: str) -> tuple[Path | None, str | None]:
        """Resolve a stored run file-change path and keep it inside the session workspace."""
        raw_path = str(path or "").strip()
        if not raw_path:
            return None, "stored file-change path is empty"

        workspace = self._workspace_for_session(session_id).resolve(strict=False)
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
        session_id: str,
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
                    "session_id": session_id,
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
                    "session_id": session_id,
                    "run_id": run_id,
                    "change_id": change_id,
                    "reason": "change_id must be an integer",
                }
            )

        change = await getter(session_id, run_id, normalized_change_id)
        if change is None:
            return PreparedRunFileChangeRevert(
                preview={
                    "status": "not_found",
                    "ok": False,
                    "session_id": session_id,
                    "run_id": run_id,
                    "change_id": normalized_change_id,
                    "reason": "file change was not found for this run",
                }
            )

        file_path, path_error = self._resolve_change_path(session_id, change.path)
        base_preview = self._base_revert_preview(session_id, run_id, normalized_change_id, change)
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
                failure["diff"] = format_unified_diff(
                    change.path,
                    current_content,
                    target_content,
                    max_chars=RUN_FILE_REVERT_DIFF_MAX_CHARS,
                )
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
                "diff": format_unified_diff(
                    change.path,
                    current_content,
                    target_content,
                    max_chars=RUN_FILE_REVERT_DIFF_MAX_CHARS,
                ),
            },
            change=change,
            file_path=file_path,
            target_content=target_content,
        )

    @staticmethod
    def _base_revert_preview(
        session_id: str,
        run_id: str,
        change_id: int,
        change: StoredRunFileChange,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
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

    async def preview_revert(self, session_id: str, run_id: str, change_id: int) -> dict[str, Any]:
        """Inspect whether one captured file change can be safely reverted."""
        prepared = await self.prepare_revert(session_id, run_id, change_id)
        await self._emit_revert_event(session_id, run_id, FILE_REVERT_PREVIEWED_EVENT, prepared.preview)
        return prepared.preview

    async def _emit_revert_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        result: dict[str, Any],
    ) -> None:
        payload = {
            "change_id": result.get("change_id"),
            "path": result.get("path"),
            "status": result.get("status"),
            "ok": bool(result.get("ok")),
            "applied": bool(result.get("applied", False)),
            "dry_run": bool(result.get("dry_run", False)),
            "reason": result.get("reason"),
            "revert_action": result.get("revert_action"),
            "post_sha256": result.get("post_sha256"),
        }
        await self._emit_run_event(session_id, run_id, event_type, payload)

    async def revert(
        self,
        session_id: str,
        run_id: str,
        change_id: int,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Safely revert one captured file change; defaults to dry-run inspection."""
        prepared = await self.prepare_revert(session_id, run_id, change_id)
        result = {**prepared.preview, "dry_run": bool(dry_run), "applied": False}
        if dry_run or prepared.preview.get("status") != "ready" or prepared.file_path is None:
            await self._emit_revert_event(session_id, run_id, FILE_REVERT_SKIPPED_EVENT, result)
            return result

        current_content, current_sha, current_error = self._read_current_content(prepared.file_path)
        if current_error:
            result.update({"status": "conflict", "ok": False, "reason": current_error})
            await self._emit_revert_event(session_id, run_id, FILE_REVERT_SKIPPED_EVENT, result)
            return result

        expected_current_sha = prepared.preview.get("expected_current_sha256")
        if expected_current_sha is None:
            if current_content is not None:
                result.update({"status": "conflict", "ok": False, "reason": "current file changed before revert apply"})
                await self._emit_revert_event(session_id, run_id, FILE_REVERT_SKIPPED_EVENT, result)
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
            await self._emit_revert_event(session_id, run_id, FILE_REVERT_SKIPPED_EVENT, result)
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
            await self._emit_revert_event(session_id, run_id, FILE_REVERT_FAILED_EVENT, result)
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
        await self._emit_revert_event(session_id, run_id, FILE_REVERT_APPLIED_EVENT, result)
        return result


class RunTraceRecorder:
    """Small service for durable run lifecycle, events, and ordered parts."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        message_bus_getter: Callable[[], Any | None],
    ):
        self.storage = storage
        self._message_bus_getter = message_bus_getter
        self.events = RunEventSink(storage=storage, message_bus_getter=message_bus_getter)

    async def create_run(
        self,
        session_id: str,
        run_id: str,
        *,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create a durable run record when the configured storage supports it."""
        creator = getattr(self.storage, "create_run", None)
        if not callable(creator):
            return
        try:
            await creator(session_id, run_id, status=status, metadata=metadata)
        except Exception as e:
            logger.warning("[{}] run.create.failed | run_id={} error={}", session_id, run_id, e)

    async def update_run_status(
        self,
        session_id: str,
        run_id: str,
        status: str,
        *,
        metadata: dict[str, Any] | None = None,
        finished_at: float | None = None,
    ) -> None:
        """Update a durable run record when the configured storage supports it."""
        updater = getattr(self.storage, "update_run_status", None)
        if not callable(updater):
            return
        try:
            await updater(session_id, run_id, status, metadata=metadata, finished_at=finished_at)
        except Exception as e:
            logger.warning("[{}] run.update.failed | run_id={} status={} error={}", session_id, run_id, status, e)

    async def add_part(
        self,
        session_id: str,
        run_id: str,
        part_type: str,
        *,
        content: str = "",
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist one ordered run artifact when the storage supports it."""
        add_part = getattr(self.storage, "add_run_part", None)
        if not callable(add_part):
            return
        try:
            stored_content, content_metadata = truncate_run_part_content(str(content or ""))
            safe_metadata = json_safe_payload(metadata)
            safe_metadata.update(content_metadata)
            await add_part(
                session_id,
                run_id,
                part_type,
                content=stored_content,
                tool_name=tool_name,
                metadata=safe_metadata,
            )
        except Exception as e:
            logger.warning("[{}] run.part.persist.failed | run_id={} type={} error={}", session_id, run_id, part_type, e)

    async def emit_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Persist and publish one structured run event."""
        await self.events.emit(
            session_id,
            run_id,
            event_type,
            payload,
            channel=channel,
            external_chat_id=external_chat_id,
        )

    async def start_turn_run(
        self,
        session_id: str,
        run_id: str,
        *,
        channel: str | None,
        external_chat_id: str | None,
        sender_id: str | None,
        sender_name: str | None,
        text: str | None,
        images: list[str] | None,
        audios: list[str] | None,
        videos: list[str] | None,
    ) -> None:
        """Create a run and emit the initial user-turn run_started event."""
        run_metadata = {
            "channel": channel,
            "external_chat_id": external_chat_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
        }
        run_metadata = {key: value for key, value in run_metadata.items() if value is not None}
        await self.create_run(session_id, run_id, status=RUN_RUNNING_STATUS, metadata=run_metadata)
        await self.emit_event(
            session_id,
            run_id,
            RUN_STARTED_EVENT,
            {
                "status": RUN_RUNNING_STATUS,
                "text_len": len(text or ""),
                "images_count": len(images or []),
                "audios_count": len(audios or []),
                "videos_count": len(videos or []),
            },
            channel=channel,
            external_chat_id=external_chat_id,
        )

    async def record_assistant_message_part(
        self,
        session_id: str,
        run_id: str,
        response: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist the assistant-visible response as an ordered run part."""
        await self.add_part(
            session_id,
            run_id,
            "assistant_message",
            content=response,
            metadata=metadata,
        )

    async def record_context_compaction_parts(
        self,
        session_id: str,
        run_id: str,
        compaction_events: list[Any],
    ) -> None:
        """Persist context compaction telemetry events as ordered run parts."""
        for compaction_event in compaction_events:
            compaction_metadata = vars(compaction_event)
            await self.add_part(
                session_id,
                run_id,
                "context_compaction",
                content=(
                    f"{compaction_event.trigger}:"
                    f"{compaction_event.strategy}:"
                    f"{compaction_event.outcome}"
                ),
                metadata=compaction_metadata,
            )

    async def record_llm_step_parts(
        self,
        session_id: str,
        run_id: str,
        step_events: list[Any],
    ) -> None:
        """Persist LLM request attempts as ordered run artifacts."""
        for step_event in step_events:
            metadata = vars(step_event)
            content = (
                f"iteration={step_event.iteration} attempt={step_event.attempt} "
                f"status={step_event.status} provider={step_event.provider or 'unknown'} "
                f"model={step_event.model or 'unknown'}"
            )
            await self.add_part(
                session_id,
                run_id,
                "llm_step",
                content=content,
                metadata=metadata,
            )

    async def record_harness_checkpoint_part(
        self,
        session_id: str,
        run_id: str,
        checkpoint: dict[str, Any],
    ) -> None:
        """Persist the latest harness state as a durable run part."""
        profile = checkpoint.get(TRACE_HARNESS_PROFILE_FIELD) if isinstance(checkpoint, dict) else {}
        policy = checkpoint.get(TRACE_HARNESS_POLICY_FIELD) if isinstance(checkpoint, dict) else {}
        completion = checkpoint.get(TRACE_COMPLETION_FIELD) if isinstance(checkpoint, dict) else {}
        content = " · ".join(
            item
            for item in (
                f"profile={profile.get(TRACE_NAME_FIELD)}" if isinstance(profile, dict) and profile.get(TRACE_NAME_FIELD) else "",
                f"policy={policy.get(TRACE_NAME_FIELD)}" if isinstance(policy, dict) and policy.get(TRACE_NAME_FIELD) else "",
                f"completion={completion.get(TRACE_STATUS_FIELD)}" if isinstance(completion, dict) and completion.get(TRACE_STATUS_FIELD) else "",
                f"next={checkpoint.get(TRACE_NEXT_ACTION_FIELD)}" if isinstance(checkpoint, dict) and checkpoint.get(TRACE_NEXT_ACTION_FIELD) else "",
            )
            if item
        )
        await self.add_part(
            session_id,
            run_id,
            "harness_checkpoint",
            content=content,
            metadata=checkpoint,
        )

    async def record_harness_scorecard_part(
        self,
        session_id: str,
        run_id: str,
        scorecard: dict[str, Any],
    ) -> None:
        """Persist the latest harness scorecard as a durable run part."""
        profile = scorecard.get(TRACE_PROFILE_FIELD) if isinstance(scorecard, dict) else {}
        contract = scorecard.get(TRACE_CONTRACT_FIELD) if isinstance(scorecard, dict) else {}
        completion = scorecard.get(TRACE_COMPLETION_FIELD) if isinstance(scorecard, dict) else {}
        trace_health = scorecard.get(TRACE_TRACE_HEALTH_FIELD) if isinstance(scorecard, dict) else {}
        sensor_counts = trace_health.get(TRACE_SENSOR_COUNTS_FIELD) if isinstance(trace_health, dict) else {}
        content = " · ".join(
            item
            for item in (
                f"profile={profile.get(TRACE_NAME_FIELD)}" if isinstance(profile, dict) and profile.get(TRACE_NAME_FIELD) else "",
                f"contract={contract.get(TRACE_TASK_TYPE_FIELD)}" if isinstance(contract, dict) and contract.get(TRACE_TASK_TYPE_FIELD) else "",
                f"completion={completion.get(TRACE_STATUS_FIELD)}" if isinstance(completion, dict) and completion.get(TRACE_STATUS_FIELD) else "",
                f"trace={trace_health.get(TRACE_STATUS_FIELD)}" if isinstance(trace_health, dict) and trace_health.get(TRACE_STATUS_FIELD) else "",
                _scorecard_sensor_summary(sensor_counts),
            )
            if item
        )
        await self.add_part(
            session_id,
            run_id,
            "harness_scorecard",
            content=content,
            metadata=scorecard,
        )

    async def record_operation_audit_part(
        self,
        session_id: str,
        run_id: str,
        audit: dict[str, Any],
    ) -> None:
        """Persist an operation audit snapshot for rollback and review."""
        content = " · ".join(
            item
            for item in (
                f"operation={audit.get(TRACE_OPERATION_TYPE_FIELD)}",
                f"target={audit.get(TRACE_TARGET_FIELD)}",
                f"rollback={bool(audit.get(TRACE_ROLLBACK_AVAILABLE_FIELD))}",
            )
            if item
        )
        await self.add_part(
            session_id,
            run_id,
            "operation_audit",
            content=content,
            metadata=audit,
        )

    async def record_harness_eval_result_part(
        self,
        session_id: str,
        run_id: str,
        result: dict[str, Any],
    ) -> None:
        """Persist a controlled harness eval result as a durable run part."""
        summary = result.get(TRACE_SUMMARY_FIELD) if isinstance(result, dict) else {}
        content = " · ".join(
            item
            for item in (
                f"kind={result.get(TRACE_KIND_FIELD)}",
                f"ok={bool(result.get(TRACE_OK_FIELD))}",
                f"cases={summary.get(TRACE_PASSED_CASES_FIELD)}/{summary.get(TRACE_TOTAL_CASES_FIELD)}" if isinstance(summary, dict) else "",
                f"checks={summary.get(TRACE_PASSED_CHECKS_FIELD)}/{summary.get(TRACE_TOTAL_CHECKS_FIELD)}" if isinstance(summary, dict) else "",
            )
            if item
        )
        await self.add_part(
            session_id,
            run_id,
            "harness_eval_result",
            content=content,
            metadata=result,
        )

    async def record_task_checklist_part(
        self,
        session_id: str,
        run_id: str,
        work_state: Any,
    ) -> list[dict[str, Any]]:
        """Persist the current session task checklist as a run artifact."""
        todos = serialize_work_state_todos(work_state)
        await self.add_part(
            session_id,
            run_id,
            "task_checklist",
            content="\n".join(f"[{item['status']}] {item['content']}" for item in todos),
            metadata={
                "status": getattr(work_state, "status", "active"),
                "objective": getattr(work_state, "objective", ""),
                "todos": todos,
            },
        )
        return todos

    async def record_worktree_sandbox_part(
        self,
        session_id: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Persist worktree sandbox readiness metadata for one run."""
        await self.add_part(
            session_id,
            run_id,
            "worktree_sandbox",
            content=str(metadata.get(TRACE_STATUS_FIELD) or "unknown"),
            metadata=metadata,
        )

    async def complete_run(
        self,
        session_id: str,
        run_id: str,
        *,
        event_payload: dict[str, Any],
        status_metadata: dict[str, Any] | None = None,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Emit run_finished and mark the durable run completed."""
        finished_at = time.time()
        await self.emit_event(
            session_id,
            run_id,
            RUN_FINISHED_EVENT,
            event_payload,
            channel=channel,
            external_chat_id=external_chat_id,
        )
        await self.update_run_status(
            session_id,
            run_id,
            RUN_COMPLETED_STATUS,
            metadata=status_metadata,
            finished_at=finished_at,
        )

    async def fail_run(
        self,
        session_id: str,
        run_id: str,
        *,
        status: str,
        event_payload: dict[str, Any],
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> None:
        """Emit a terminal run event and mark the durable run with the supplied status."""
        finished_at = time.time()
        event_type = RUN_CANCELLED_EVENT if status == RUN_CANCELLED_STATUS else RUN_FAILED_EVENT
        await self.emit_event(
            session_id,
            run_id,
            event_type,
            event_payload,
            channel=channel,
            external_chat_id=external_chat_id,
        )
        await self.update_run_status(session_id, run_id, status, finished_at=finished_at)


def _scorecard_sensor_summary(sensor_counts: Any) -> str:
    if not isinstance(sensor_counts, dict):
        return ""
    fail_count = int(sensor_counts.get(TRACE_SENSOR_FAIL_FIELD) or 0)
    warn_count = int(sensor_counts.get(TRACE_SENSOR_WARN_FIELD) or 0)
    pass_count = int(sensor_counts.get(TRACE_SENSOR_PASS_FIELD) or 0)
    if fail_count or warn_count:
        return f"sensors={pass_count} pass/{warn_count} warn/{fail_count} fail"
    return f"sensors={pass_count} pass" if pass_count else ""


