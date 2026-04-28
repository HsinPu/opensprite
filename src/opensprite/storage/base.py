"""
opensprite/storage/base.py - Storage 介面定義

設計理念：
- Agent 只認得「統一的 Storage 介面」
- 不同存放方式（記憶體、檔案、資料庫、Redis）都實作這個介面
- 以後要換存放方式 Agent 不用改

"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoredMessage:
    """
    已儲存的訊息格式
    """
    role: str      # "user" / "assistant" / "tool"
    content: str   # 訊息內容
    timestamp: float  # 時間戳記
    tool_name: str | None = None  # 如果是 tool，記錄用了什麼工具
    is_consolidated: bool = False  # 是否已被 consolidate 過
    metadata: dict[str, Any] = field(default_factory=dict)  # 額外欄位（channel、sender、external chat id...）


@dataclass
class StoredRun:
    """Persisted execution run for one user-facing turn."""

    run_id: str
    session_id: str
    status: str
    created_at: float
    updated_at: float
    finished_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoredRunEvent:
    """One structured event emitted while a run is executing."""

    run_id: str
    session_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    event_id: int | None = None


@dataclass
class StoredRunPart:
    """One durable, ordered execution artifact for a run."""

    run_id: str
    session_id: str
    part_type: str
    content: str = ""
    tool_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    part_id: int | None = None


@dataclass
class StoredRunFileChange:
    """One file mutation captured during a run for later inspection."""

    run_id: str
    session_id: str
    tool_name: str
    path: str
    action: str
    before_sha256: str | None = None
    after_sha256: str | None = None
    before_content: str | None = None
    after_content: str | None = None
    diff: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    change_id: int | None = None


@dataclass
class StoredRunTrace:
    """Complete persisted execution trace for one run."""

    run: StoredRun
    events: list[StoredRunEvent] = field(default_factory=list)
    parts: list[StoredRunPart] = field(default_factory=list)
    file_changes: list[StoredRunFileChange] = field(default_factory=list)


@dataclass
class StoredWorkState:
    """Persisted structured task state for one chat session."""

    session_id: str
    objective: str
    kind: str
    status: str = "active"
    steps: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    done_criteria: tuple[str, ...] = ()
    long_running: bool = False
    coding_task: bool = False
    expects_code_change: bool = False
    expects_verification: bool = False
    current_step: str = "not set"
    next_step: str = "not set"
    completed_steps: tuple[str, ...] = ()
    pending_steps: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    verification_targets: tuple[str, ...] = ()
    resume_hint: str = ""
    last_progress_signals: tuple[str, ...] = ()
    file_change_count: int = 0
    touched_paths: tuple[str, ...] = ()
    verification_attempted: bool = False
    verification_passed: bool = False
    last_next_action: str = ""
    active_delegate_task_id: str | None = None
    active_delegate_prompt_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


class StorageProvider(ABC):
    """
    Storage Provider 的抽象基底類別
    
    每種存放方式都應該實作這個類別。
    
    抽象方法：
        - get_messages(): 取得對話歷史
        - add_message(): 加入訊息
        - clear_messages(): 清除歷史
        - get_consolidated_index(): 取得 consolidation 標記
        - set_consolidated_index(): 設定 consolidation 標記
    """
    
    @abstractmethod
    async def get_messages(self, session_id: str, limit: int | None = None) -> list[StoredMessage]:
        """
        取得對話歷史
        
        參數：
            session_id: 聊天室 ID
            limit: 最多取幾筆（可選）
        
        回傳：
            list[StoredMessage]: 訊息清單
        """
        pass

    async def get_message_count(self, session_id: str) -> int:
        """Return the total persisted message count for one chat."""
        return len(await self.get_messages(session_id))

    async def get_messages_slice(
        self,
        session_id: str,
        *,
        start_index: int = 0,
        end_index: int | None = None,
    ) -> list[StoredMessage]:
        """Return one contiguous message slice using Python slice semantics."""
        messages = await self.get_messages(session_id)
        return messages[max(0, start_index):end_index]
    
    @abstractmethod
    async def add_message(self, session_id: str, message: StoredMessage) -> None:
        """
        加入訊息到歷史
        
        參數：
            session_id: 聊天室 ID
            message: StoredMessage 訊息
        """
        pass
    
    @abstractmethod
    async def clear_messages(self, session_id: str) -> None:
        """
        清除指定聊天室的歷史
        
        參數：
            session_id: 聊天室 ID
        """
        pass

    @abstractmethod
    async def get_consolidated_index(self, session_id: str) -> int:
        """Get the last consolidated message index for a chat."""
        pass

    @abstractmethod
    async def set_consolidated_index(self, session_id: str, index: int) -> None:
        """Persist the last consolidated message index for a chat."""
        pass

    async def create_run(
        self,
        session_id: str,
        run_id: str,
        *,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRun | None:
        """Persist the start of a user-facing execution run when supported."""
        return None

    async def update_run_status(
        self,
        session_id: str,
        run_id: str,
        status: str,
        *,
        metadata: dict[str, Any] | None = None,
        finished_at: float | None = None,
    ) -> StoredRun | None:
        """Update a persisted run status when supported."""
        return None

    async def get_runs(self, session_id: str, limit: int | None = None) -> list[StoredRun]:
        """Return persisted runs for one chat from newest to oldest when supported."""
        return []

    async def get_run(self, session_id: str, run_id: str) -> StoredRun | None:
        """Return one persisted run for a chat when supported."""
        for run in await self.get_runs(session_id):
            if run.run_id == run_id:
                return run
        return None

    async def get_latest_run(self, session_id: str) -> StoredRun | None:
        """Return the newest persisted run for one chat when supported."""
        runs = await self.get_runs(session_id, limit=1)
        return runs[0] if runs else None

    async def add_run_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRunEvent | None:
        """Persist one structured run event when supported."""
        return None

    async def get_run_events(self, session_id: str, run_id: str) -> list[StoredRunEvent]:
        """Return persisted events for one run when supported."""
        return []

    async def add_run_part(
        self,
        session_id: str,
        run_id: str,
        part_type: str,
        *,
        content: str = "",
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> StoredRunPart | None:
        """Persist one ordered run artifact when supported."""
        return None

    async def get_run_parts(self, session_id: str, run_id: str) -> list[StoredRunPart]:
        """Return ordered run artifacts for one run when supported."""
        return []

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
    ) -> StoredRunFileChange | None:
        """Persist one file mutation captured during a run when supported."""
        return None

    async def get_run_file_changes(self, session_id: str, run_id: str) -> list[StoredRunFileChange]:
        """Return ordered file mutations captured for one run when supported."""
        return []

    async def get_run_file_change(
        self,
        session_id: str,
        run_id: str,
        change_id: int,
    ) -> StoredRunFileChange | None:
        """Return one captured file mutation for a run when supported."""
        for change in await self.get_run_file_changes(session_id, run_id):
            if change.change_id == change_id:
                return change
        return None

    async def get_run_trace(self, session_id: str, run_id: str) -> StoredRunTrace | None:
        """Return a run with its ordered events and durable parts."""
        run = await self.get_run(session_id, run_id)
        if run is None:
            return None
        return StoredRunTrace(
            run=run,
            events=await self.get_run_events(session_id, run_id),
            parts=await self.get_run_parts(session_id, run_id),
            file_changes=await self.get_run_file_changes(session_id, run_id),
        )

    async def get_work_state(self, session_id: str) -> StoredWorkState | None:
        """Return persisted structured work state for one chat when supported."""
        return None

    async def upsert_work_state(self, state: StoredWorkState) -> StoredWorkState | None:
        """Create or replace the persisted work state for one chat when supported."""
        return None

    async def clear_work_state(self, session_id: str) -> None:
        """Remove persisted structured work state for one chat when supported."""
        return None
    
    @abstractmethod
    async def get_all_sessions(self) -> list[str]:
        """
        取得所有聊天室 ID
        
        回傳：
            list[str]: 聊天室 ID 清單
        """
        pass


async def get_storage_message_count(storage: Any, session_id: str) -> int:
    """Compatibility helper for storages that may not implement get_message_count yet."""
    getter = getattr(storage, "get_message_count", None)
    if callable(getter):
        return int(await getter(session_id))
    return len(await storage.get_messages(session_id))


async def get_storage_messages_slice(
    storage: Any,
    session_id: str,
    *,
    start_index: int = 0,
    end_index: int | None = None,
) -> list[StoredMessage]:
    """Compatibility helper for storages that may not implement get_messages_slice yet."""
    getter = getattr(storage, "get_messages_slice", None)
    if callable(getter):
        return list(await getter(session_id, start_index=max(0, start_index), end_index=end_index))
    messages = await storage.get_messages(session_id)
    return list(messages[max(0, start_index):end_index])


async def get_storage_work_state(storage: Any, session_id: str) -> StoredWorkState | None:
    """Compatibility helper for storages that may not implement work-state APIs yet."""
    getter = getattr(storage, "get_work_state", None)
    if callable(getter):
        return await getter(session_id)
    return None


async def upsert_storage_work_state(storage: Any, state: StoredWorkState) -> StoredWorkState | None:
    """Compatibility helper for storages that may not implement work-state APIs yet."""
    setter = getattr(storage, "upsert_work_state", None)
    if callable(setter):
        return await setter(state)
    return None


async def clear_storage_work_state(storage: Any, session_id: str) -> None:
    """Compatibility helper for storages that may not implement work-state APIs yet."""
    clearer = getattr(storage, "clear_work_state", None)
    if callable(clearer):
        await clearer(session_id)
