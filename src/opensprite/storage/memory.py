"""
opensprite/storage/memory.py - 記憶體 Storage 實作

把對話歷史存放在記憶體中（current implementation）

"""

import time
from collections import defaultdict
from typing import Any

from .base import (
    StorageProvider,
    StoredBackgroundProcess,
    StoredMessage,
    StoredRun,
    StoredRunEvent,
    StoredRunFileChange,
    StoredRunPart,
    StoredWorkState,
    coerce_stored_delegated_tasks,
    legacy_delegated_tasks,
    selected_delegated_task,
)


class MemoryStorage(StorageProvider):
    """
    記憶體 Storage 實作
    
    把對話歷史存在 dict 裡，重啟後會消失。
    適合開發測試用。
    """
    
    def __init__(self):
        """初始化"""
        self._messages: dict[str, list[StoredMessage]] = defaultdict(list)
        self._consolidated_index: dict[str, int] = {}  # Per-chat consolidation tracking
        self._runs: dict[str, StoredRun] = {}
        self._run_events: dict[tuple[str, str], list[StoredRunEvent]] = defaultdict(list)
        self._run_file_changes: dict[tuple[str, str], list[StoredRunFileChange]] = defaultdict(list)
        self._run_parts: dict[tuple[str, str], list[StoredRunPart]] = defaultdict(list)
        self._work_states: dict[str, StoredWorkState] = {}
        self._background_processes: dict[str, StoredBackgroundProcess] = {}
    
    async def get_messages(self, session_id: str, limit: int | None = None) -> list[StoredMessage]:
        """
        取得對話歷史
        """
        messages = self._messages.get(session_id, [])
        if limit:
            return messages[-limit:]
        return messages
    
    async def add_message(self, session_id: str, message: StoredMessage) -> None:
        """
        加入訊息
        """
        # 設定時間戳記（如果沒有的話）
        if message.timestamp == 0:
            message.timestamp = time.time()
        
        self._messages[session_id].append(message)

    async def get_message_count(self, session_id: str) -> int:
        """Return the total message count for one in-memory chat."""
        return len(self._messages.get(session_id, []))

    async def get_messages_slice(
        self,
        session_id: str,
        *,
        start_index: int = 0,
        end_index: int | None = None,
    ) -> list[StoredMessage]:
        """Return one message slice without copying the full chat history first."""
        messages = self._messages.get(session_id, [])
        return list(messages[max(0, start_index):end_index])
    
    async def clear_messages(self, session_id: str) -> None:
        """
        清除歷史
        """
        if session_id in self._messages:
            self._messages[session_id].clear()
        self._consolidated_index.pop(session_id, None)
        for run_id, run in list(self._runs.items()):
            if run.session_id == session_id:
                self._runs.pop(run_id, None)
                self._run_events.pop((session_id, run_id), None)
                self._run_file_changes.pop((session_id, run_id), None)
                self._run_parts.pop((session_id, run_id), None)
        self._work_states.pop(session_id, None)
        for process_id, process in list(self._background_processes.items()):
            if process.owner_session_id == session_id:
                self._background_processes.pop(process_id, None)
    
    async def get_consolidated_index(self, session_id: str) -> int:
        """取得 consolidation 標記"""
        return self._consolidated_index.get(session_id, 0)
    
    async def set_consolidated_index(self, session_id: str, index: int) -> None:
        """設定 consolidation 標記"""
        self._consolidated_index[session_id] = index
    
    async def get_all_sessions(self) -> list[str]:
        """
        取得所有聊天室
        """
        session_ids = set(self._messages.keys())
        session_ids.update(run.session_id for run in self._runs.values())
        session_ids.update(self._work_states.keys())
        session_ids.update(process.owner_session_id for process in self._background_processes.values())
        return sorted(session_ids)

    async def upsert_background_process(
        self,
        process: StoredBackgroundProcess,
    ) -> StoredBackgroundProcess:
        existing = self._background_processes.get(process.process_session_id)
        started_at = existing.started_at if existing is not None and existing.started_at else float(process.started_at or time.time())
        updated = StoredBackgroundProcess(
            process_session_id=process.process_session_id,
            owner_session_id=process.owner_session_id,
            owner_run_id=process.owner_run_id,
            owner_channel=process.owner_channel,
            owner_external_chat_id=process.owner_external_chat_id,
            pid=process.pid,
            command=process.command,
            cwd=process.cwd,
            state=process.state,
            termination_reason=process.termination_reason,
            exit_code=process.exit_code,
            notify_mode=process.notify_mode,
            output_tail=process.output_tail,
            output_path=process.output_path,
            metadata=dict(process.metadata or {}),
            started_at=started_at,
            updated_at=float(process.updated_at or time.time()),
            finished_at=process.finished_at,
        )
        self._background_processes[updated.process_session_id] = updated
        return updated

    async def get_background_process(self, process_session_id: str) -> StoredBackgroundProcess | None:
        return self._background_processes.get(process_session_id)

    async def list_background_processes(
        self,
        *,
        owner_session_id: str | None = None,
        states: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[StoredBackgroundProcess]:
        processes = list(self._background_processes.values())
        if owner_session_id is not None:
            processes = [process for process in processes if process.owner_session_id == owner_session_id]
        if states:
            allowed_states = set(states)
            processes = [process for process in processes if process.state in allowed_states]
        processes.sort(key=lambda process: (process.updated_at, process.process_session_id), reverse=True)
        if limit is not None:
            return processes[:limit]
        return processes

    async def create_run(
        self,
        session_id: str,
        run_id: str,
        *,
        status: str = "running",
        metadata: dict | None = None,
        created_at: float | None = None,
    ) -> StoredRun:
        now = float(created_at or time.time())
        run = StoredRun(
            run_id=run_id,
            session_id=session_id,
            status=status,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        self._runs[run_id] = run
        return run

    async def update_run_status(
        self,
        session_id: str,
        run_id: str,
        status: str,
        *,
        metadata: dict | None = None,
        finished_at: float | None = None,
    ) -> StoredRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        run.status = status
        run.updated_at = time.time()
        if finished_at is not None:
            run.finished_at = float(finished_at)
        if metadata:
            run.metadata.update(metadata)
        return run

    async def get_runs(self, session_id: str, limit: int | None = None) -> list[StoredRun]:
        runs = [run for run in self._runs.values() if run.session_id == session_id]
        runs.sort(key=lambda run: (run.created_at, run.run_id), reverse=True)
        if limit is not None:
            return runs[:limit]
        return runs

    async def get_run(self, session_id: str, run_id: str) -> StoredRun | None:
        run = self._runs.get(run_id)
        if run is None or run.session_id != session_id:
            return None
        return run

    async def get_work_state(self, session_id: str) -> StoredWorkState | None:
        return self._work_states.get(session_id)

    async def upsert_work_state(self, state: StoredWorkState) -> StoredWorkState:
        existing = self._work_states.get(state.session_id)
        created_at = existing.created_at if existing is not None and existing.created_at else float(state.created_at or time.time())
        delegated_tasks = coerce_stored_delegated_tasks(state.delegated_tasks) or legacy_delegated_tasks(
            state.active_delegate_task_id,
            state.active_delegate_prompt_type,
        )
        selected_task = selected_delegated_task(delegated_tasks)
        updated = StoredWorkState(
            session_id=state.session_id,
            objective=state.objective,
            kind=state.kind,
            status=state.status,
            steps=tuple(state.steps),
            constraints=tuple(state.constraints),
            done_criteria=tuple(state.done_criteria),
            long_running=bool(state.long_running),
            coding_task=bool(state.coding_task),
            expects_code_change=bool(state.expects_code_change),
            expects_verification=bool(state.expects_verification),
            current_step=state.current_step,
            next_step=state.next_step,
            completed_steps=tuple(state.completed_steps),
            pending_steps=tuple(state.pending_steps),
            blockers=tuple(state.blockers),
            verification_targets=tuple(state.verification_targets),
            resume_hint=state.resume_hint,
            last_progress_signals=tuple(state.last_progress_signals),
            file_change_count=int(state.file_change_count),
            touched_paths=tuple(state.touched_paths),
            verification_attempted=bool(state.verification_attempted),
            verification_passed=bool(state.verification_passed),
            last_next_action=state.last_next_action,
            delegated_tasks=delegated_tasks,
            active_delegate_task_id=selected_task.task_id if selected_task is not None else None,
            active_delegate_prompt_type=selected_task.prompt_type if selected_task is not None else None,
            metadata=dict(state.metadata or {}),
            created_at=created_at,
            updated_at=float(state.updated_at or time.time()),
        )
        self._work_states[state.session_id] = updated
        return updated

    async def clear_work_state(self, session_id: str) -> None:
        self._work_states.pop(session_id, None)

    async def add_run_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        *,
        payload: dict | None = None,
        created_at: float | None = None,
    ) -> StoredRunEvent:
        key = (session_id, run_id)
        event = StoredRunEvent(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=dict(payload or {}),
            created_at=float(created_at or time.time()),
            event_id=len(self._run_events[key]) + 1,
        )
        self._run_events[key].append(event)
        return event

    async def get_run_events(self, session_id: str, run_id: str) -> list[StoredRunEvent]:
        return list(self._run_events.get((session_id, run_id), []))

    async def add_run_part(
        self,
        session_id: str,
        run_id: str,
        part_type: str,
        *,
        content: str = "",
        tool_name: str | None = None,
        metadata: dict | None = None,
        created_at: float | None = None,
    ) -> StoredRunPart:
        key = (session_id, run_id)
        part = StoredRunPart(
            run_id=run_id,
            session_id=session_id,
            part_type=part_type,
            content=str(content or ""),
            tool_name=tool_name,
            metadata=dict(metadata or {}),
            created_at=float(created_at or time.time()),
            part_id=len(self._run_parts[key]) + 1,
        )
        self._run_parts[key].append(part)
        return part

    async def get_run_parts(self, session_id: str, run_id: str) -> list[StoredRunPart]:
        return list(self._run_parts.get((session_id, run_id), []))

    async def add_run_file_change(
        self,
        session_id: str,
        run_id: str,
        tool_name: str,
        path: str,
        action: str,
        *,
        before_sha256: str | None = None,
        after_sha256: str | None = None,
        before_content: str | None = None,
        after_content: str | None = None,
        diff: str = "",
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRunFileChange:
        key = (session_id, run_id)
        change = StoredRunFileChange(
            run_id=run_id,
            session_id=session_id,
            tool_name=tool_name,
            path=path,
            action=action,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            before_content=before_content,
            after_content=after_content,
            diff=str(diff or ""),
            metadata=dict(metadata or {}),
            created_at=float(created_at or time.time()),
            change_id=len(self._run_file_changes[key]) + 1,
        )
        self._run_file_changes[key].append(change)
        return change

    async def get_run_file_changes(self, session_id: str, run_id: str) -> list[StoredRunFileChange]:
        return list(self._run_file_changes.get((session_id, run_id), []))

    async def get_run_file_change(
        self,
        session_id: str,
        run_id: str,
        change_id: int,
    ) -> StoredRunFileChange | None:
        for change in self._run_file_changes.get((session_id, run_id), []):
            if change.change_id == change_id:
                return change
        return None
