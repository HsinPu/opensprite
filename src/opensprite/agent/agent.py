"""
opensprite/agent.py - Agent Loop

核心流程：
1. 接收使用者訊息
2. 用 ContextBuilder 組 prompt
3. 叫 LLM
4. 執行 tool calls（如果 LLM 請求）
5. 回覆給使用者

設計重點：
- 只認得「統一的訊息格式」：UserMessage、AssistantMessage
- 只認得「統一的 LLM Provider 介面」
- 只認得「統一的 Storage 介面」
- 只認得「統一的 ContextBuilder 介面」
- 具体的訊息來源（telegram、discord）由外部 Adapter 轉換
- 具体的 LLM 廠商由 Provider 實作
- 具体的存放方式由 Storage 實作
- 具体的 prompt 組裝由 ContextBuilder 實作
- Tool 由 ToolRegistry 管理
"""

from contextvars import ContextVar
import time
from pathlib import Path
import shutil
from typing import Any, Awaitable, Callable

from ..bus.message import UserMessage, AssistantMessage
from ..llms import LLMProvider, ChatMessage
from ..llms.runtime_provider import create_llm_from_runtime, resolve_provider_runtime
from ..storage import StorageProvider, StoredDelegatedTask
from ..storage.base import get_storage_message_count
from ..documents.active_task import ActiveTaskConsolidator, create_active_task_store
from ..context.builder import ContextBuilder
from ..documents.memory import MemoryStore
from ..context.paths import (
    get_session_curator_state_file,
    get_session_learning_state_file,
    get_session_skills_dir,
    get_session_workspace,
)
from ..documents.recent_summary import RecentSummaryConsolidator, RecentSummaryStore
from ..media import (
    MediaRouter,
    OpenAICompatibleSpeechProvider,
    OpenAICompatibleVideoProvider,
    create_image_analysis_provider,
)
from ..documents.user_profile import UserProfileConsolidator, create_user_profile_store
from ..documents.user_overlay import UserOverlayIndexStore, UserOverlayPromotionService, UserOverlayStore
from ..search.base import SearchStore
from ..tools import ToolRegistry
from ..tools.approval import PermissionRequest, PermissionRequestManager
from ..tools.permissions import PermissionApprovalResult, PermissionDecision
from ..tools.process_runtime import BackgroundProcessManager, BackgroundSession
from ..tools.verify import classify_verification_result
from ..documents.user_overlay_identity import resolve_user_overlay_id
from ..utils.log import logger
from ..config import AgentConfig, MemoryConfig, ToolsConfig, LogConfig, SearchConfig, UserProfileConfig, ActiveTaskConfig, RecentSummaryConfig, MessagesConfig, Config
from ..storage.base import clear_storage_work_state, get_storage_work_state, upsert_storage_work_state
from .active_task_commands import ActiveTaskCommandService
from .auto_continue import AutoContinueService
from .background_notifications import BackgroundSessionNotificationService
from .completion_gate import CompletionGateResult, CompletionGateService
from .consolidation import MemoryConsolidationService, RecentSummaryUpdateService, UserProfileUpdateService, ActiveTaskUpdateService
from .curator import CuratorService, fingerprint_text_directory
from .execution import ExecutionEngine, ExecutionResult
from .file_changes import RunFileChangeService
from .history_reset import HistoryResetService
from .learning_ledger import LearningLedger
from .llm_call import LlmCallService
from .media import AgentMediaService
from .message_history import MessageHistoryService
from .mcp_lifecycle import McpLifecycleService
from .permission_events import PermissionEventRecorder
from .permission_flow import AgentPermissionService
from .prompt_budget import PromptBudgetService
from .prompt_logging import PromptLoggingService
from .retrieval import ProactiveRetrievalService
from .response_finalizer import AgentResponseFinalizer
from .run_state import AgentRunStateService
from .run_trace import RunTraceRecorder
from .run_hooks import RunHookService
from .skill_review import SkillReviewService
from .subagents import SubagentRunService
from .task_intent import TaskIntent, TaskIntentService
from .tool_registration import register_default_tools, register_memory_tool
from .turn_context import TurnContextService
from .turn_input import TurnInputPreparer
from .turn_runner import AgentTurnRunner
from .worktree import WorktreeSandboxInspector
from .workflows import SubagentWorkflowService
from .work_progress import WorkProgressService, WorkProgressUpdate

class AgentLoop:
    """
    Agent Loop

    這是整個 Agent 的核心類別，負責：
    - 維護對話歷史（透過 Storage）
    - 組 prompt（透過 ContextBuilder）
    - 呼叫 LLM（透過 Provider 介面）
    - 執行 Tool Calls
    - 處理使用者輸入並回傳（process）

    設計重點：
    - 只認得「統一的訊息格式」：UserMessage、AssistantMessage
    - 只認得「統一的 LLM Provider 介面」
    - 只認得「統一的 Storage 介面」
    - 只認得「統一的 ContextBuilder 介面」
    - 具体的 LLM 廠商由外部注入
    - 具体的存放方式由外部注入
    - 具体的 prompt 組裝由外部注入
    - Tools 由 ToolRegistry 管理
    """

    MAX_TOOL_ITERATIONS = 10

    @staticmethod
    def _sanitize_log_filename(value: str) -> str:
        """Sanitize a string for use in per-prompt log filenames."""
        return PromptLoggingService.sanitize_log_filename(value)

    def _get_system_prompt_log_path(self, log_id: str) -> Path:
        """Return a unique file path for one full system prompt log entry."""
        return self.prompt_logging.get_system_prompt_log_path(log_id)

    def _write_full_system_prompt_log(self, log_id: str, content: str) -> None:
        """Write the full system prompt to a dedicated per-prompt log file."""
        self.prompt_logging.write_full_system_prompt_log(log_id, content)

    @staticmethod
    def _sanitize_response_content(content: str) -> str:
        """Remove provider-internal control blocks from visible replies."""
        return PromptLoggingService.sanitize_response_content(content)

    @staticmethod
    def _format_log_preview(content: str | list[dict[str, Any]] | None, max_chars: int = 160) -> str:
        """Build a compact, single-line preview for logs."""
        return PromptLoggingService.format_log_preview(content, max_chars=max_chars)

    @staticmethod
    def _summarize_messages(messages: list[ChatMessage], tail: int = 4) -> str:
        """Build a compact summary of the trailing chat messages for diagnostics."""
        return PromptLoggingService.summarize_messages(messages, tail=tail)

    @staticmethod
    def _extract_available_subagents(system_prompt: str) -> list[str]:
        """Parse the Available Subagents section from a rendered system prompt."""
        return PromptLoggingService.extract_available_subagents(system_prompt)

    @staticmethod
    def _tool_warrants_progress_notice(tool_name: str) -> bool:
        """Whether to send a short interim message before this tool runs (main agent only)."""
        return RunHookService.tool_warrants_progress_notice(tool_name)

    @staticmethod
    def _format_tool_progress_message(tool_name: str, tool_args: dict[str, Any]) -> str:
        """User-facing one-line status for skill / subagent / MCP tool execution."""
        return RunHookService.format_tool_progress_message(tool_name, tool_args)

    def _make_tool_progress_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any]], Awaitable[None]] | None:
        """Publish run telemetry and a brief outbound status before selected tools run."""
        return self.run_hooks.make_tool_progress_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def _make_tool_result_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any], str], Awaitable[None]] | None:
        """Publish structured run telemetry after a tool finishes."""
        base_hook = self.run_hooks.make_tool_result_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )
        if run_id is None:
            return base_hook
        if base_hook is None and self.learning_ledger is None:
            return None

        async def _hook(
            tool_name: str,
            tool_args: dict[str, Any],
            result: str,
            tool_call_id: str | None = None,
            iteration: int | None = None,
            delegate_task_id: str | None = None,
            delegate_prompt_type: str | None = None,
            state: str | None = None,
            interrupted: bool = False,
        ) -> None:
            if base_hook is not None:
                await base_hook(
                    tool_name,
                    tool_args,
                    result,
                    tool_call_id,
                    iteration,
                    delegate_task_id,
                    delegate_prompt_type,
                    state,
                    interrupted,
                )
            if tool_name != "read_skill":
                return
            skill_name = str((tool_args or {}).get("skill_name") or "").strip()
            if not skill_name or str(result or "").lstrip().startswith("Error:"):
                return
            self._run_skill_reads.setdefault(run_id, set()).add(skill_name)

        return _hook

    def _make_llm_status_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str], Awaitable[None]] | None:
        """在 LLM 長時間等待或重試前，對使用者發送短暫狀態（與工具進度相同走 MessageBus）。"""
        return self.run_hooks.make_llm_status_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def _make_llm_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, str, str, int], Awaitable[None]] | None:
        """Publish visible assistant response chunks into the run event stream."""
        return self.run_hooks.make_llm_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def _make_tool_input_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, str, str, int], Awaitable[None]] | None:
        """Publish streamed tool input chunks into the run event stream."""
        return self.run_hooks.make_tool_input_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def _make_reasoning_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, int], Awaitable[None]] | None:
        """Publish provider reasoning chunks into the run event stream only."""
        return self.run_hooks.make_reasoning_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    async def _create_run(
        self,
        session_id: str,
        run_id: str,
        *,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create a durable run record when the configured storage supports it."""
        await self.run_trace.create_run(session_id, run_id, status=status, metadata=metadata)

    async def _update_run_status(
        self,
        session_id: str,
        run_id: str,
        status: str,
        *,
        metadata: dict[str, Any] | None = None,
        finished_at: float | None = None,
    ) -> None:
        """Update a durable run record when the configured storage supports it."""
        await self.run_trace.update_run_status(
            session_id,
            run_id,
            status,
            metadata=metadata,
            finished_at=finished_at,
        )

    async def _add_run_part(
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
        await self.run_trace.add_part(
            session_id,
            run_id,
            part_type,
            content=content,
            tool_name=tool_name,
            metadata=metadata,
        )

    async def _record_file_changes(self, tool_name: str, changes: list[dict[str, Any]]) -> None:
        """Persist file mutations for the active run when available."""
        await self.file_changes.record_changes(
            tool_name,
            changes,
            session_id=self.turn_context.current_session_id(),
            run_id=self.turn_context.current_run_id(),
            channel=self.turn_context.current_channel(),
            external_chat_id=self.turn_context.current_external_chat_id(),
        )

    async def _emit_run_event(
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
        await self.run_trace.emit_event(
            session_id,
            run_id,
            event_type,
            payload,
            channel=channel,
            external_chat_id=external_chat_id,
        )

    def _record_delegated_task_update(self, run_id: str | None, task: StoredDelegatedTask) -> None:
        """Track delegated child-task updates for the active parent run."""
        if run_id is None:
            return
        task_id = str(task.task_id or "").strip()
        if not task_id:
            return
        bucket = self._delegated_task_updates.setdefault(run_id, {})
        previous = bucket.pop(task_id, None)
        now = time.time()
        bucket[task_id] = StoredDelegatedTask(
            task_id=task_id,
            prompt_type=task.prompt_type or (previous.prompt_type if previous is not None else None),
            status=str(task.status or (previous.status if previous is not None else "unknown")).strip() or "unknown",
            selected=bool(task.selected),
            summary=str(task.summary or (previous.summary if previous is not None else "")).strip(),
            error=(
                str(task.error or "").strip()
                if str(task.error or "").strip()
                else ""
                if str(task.status or "").strip() and str(task.status or "").strip() != "failed"
                else previous.error if previous is not None else ""
            ),
            child_session_id=task.child_session_id or (previous.child_session_id if previous is not None else None),
            last_child_run_id=task.last_child_run_id or (previous.last_child_run_id if previous is not None else None),
            metadata={**(previous.metadata if previous is not None else {}), **dict(task.metadata or {})},
            created_at=(
                previous.created_at
                if previous is not None and previous.created_at
                else float(task.created_at or now)
            ),
            updated_at=float(task.updated_at or now),
        )

    def _consume_delegated_task_updates(self, run_id: str) -> tuple[StoredDelegatedTask, ...]:
        """Return and clear delegated child-task updates captured for one run."""
        bucket = self._delegated_task_updates.pop(run_id, None)
        if not bucket:
            return ()
        return tuple(bucket.values())

    def _clear_delegated_task_updates(self, run_id: str) -> None:
        """Drop delegated child-task updates for one run without returning them."""
        self._delegated_task_updates.pop(run_id, None)

    def _record_workflow_outcome(self, run_id: str | None, outcome: dict[str, Any]) -> None:
        """Track one completed or failed workflow outcome for the active run."""
        if run_id is None or not isinstance(outcome, dict):
            return
        workflow_run_id = str(outcome.get("workflow_run_id") or "").strip()
        if not workflow_run_id:
            return
        bucket = self._workflow_outcomes.setdefault(run_id, {})
        bucket.pop(workflow_run_id, None)
        bucket[workflow_run_id] = dict(outcome)

    def _consume_workflow_outcomes(self, run_id: str) -> tuple[dict[str, Any], ...]:
        """Return and clear workflow outcomes captured for one run."""
        bucket = self._workflow_outcomes.pop(run_id, None)
        if not bucket:
            return ()
        return tuple(bucket.values())

    def _clear_workflow_outcomes(self, run_id: str) -> None:
        """Drop workflow outcomes for one run without returning them."""
        self._workflow_outcomes.pop(run_id, None)

    def pending_permission_requests(self) -> list[PermissionRequest]:
        """Return permission requests waiting for an external decision."""
        return self.permissions.pending_requests()

    def cleanup_worktree_sandbox(self, sandbox_path: str) -> dict[str, Any]:
        """Remove an OpenSprite-managed worktree sandbox by marker-guarded path."""
        return WorktreeSandboxInspector.cleanup(sandbox_path)

    async def approve_permission_request(self, request_id: str) -> PermissionRequest | None:
        """Approve one pending tool permission request."""
        return await self.permissions.approve_request(request_id)

    async def deny_permission_request(
        self,
        request_id: str,
        reason: str = "user denied approval",
    ) -> PermissionRequest | None:
        """Deny one pending tool permission request."""
        return await self.permissions.deny_request(request_id, reason=reason)

    async def _handle_tool_permission_request(
        self,
        tool_name: str,
        params: Any,
        decision: PermissionDecision,
    ) -> PermissionApprovalResult:
        """Create an ask-mode approval request for the current run context."""
        return await self.permissions.handle_tool_permission_request(tool_name, params, decision)

    async def _emit_permission_request_event(
        self,
        event_type: str,
        request: PermissionRequest,
    ) -> None:
        """Persist and publish permission approval lifecycle events for a run."""
        await self.permissions.emit_request_event(event_type, request)

    @staticmethod
    def _format_background_session_exit_message(session: BackgroundSession) -> str:
        """Render a concise outbound notice when a managed background session exits."""
        return BackgroundSessionNotificationService.format_exit_message(session)

    def _make_background_session_exit_notifier(self) -> Callable[[BackgroundSession], Awaitable[None]] | None:
        """Build an outbound notifier for managed background session completion."""
        return self.background_notifications.make_exit_notifier(
            channel=self.turn_context.current_channel(),
            external_chat_id=self.turn_context.current_external_chat_id(),
            session_id=self._get_current_session_id(),
        )

    def __init__(
        self,
        config: AgentConfig,
        provider: LLMProvider,
        storage: StorageProvider | None = None,
        context_builder: ContextBuilder | None = None,
        tools: ToolRegistry | None = None,
        memory_config: MemoryConfig | None = None,
        tools_config: ToolsConfig | None = None,
        log_config: LogConfig | None = None,
        search_store: SearchStore | None = None,
        search_config: SearchConfig | None = None,
        user_profile_config: UserProfileConfig | None = None,
        active_task_config: ActiveTaskConfig | None = None,
        recent_summary_config: RecentSummaryConfig | None = None,
        cron_manager: Any | None = None,
        media_router: MediaRouter | None = None,
        config_path: str | Path | None = None,
        llm_config: Any | None = None,
        *,
        llm_chat_temperature: float,
        llm_chat_max_tokens: int,
        llm_chat_top_p: float | None,
        llm_chat_frequency_penalty: float | None,
        llm_chat_presence_penalty: float | None,
        llm_pass_decoding_params: bool,
        llm_context_window_tokens: int | None = None,
        llm_configured: bool = True,
        messages_config: MessagesConfig | None = None,
    ):
        ...
        self.config = config
        self.llm_chat_temperature = llm_chat_temperature
        self.llm_chat_max_tokens = llm_chat_max_tokens
        self.llm_chat_top_p = llm_chat_top_p
        self.llm_chat_frequency_penalty = llm_chat_frequency_penalty
        self.llm_chat_presence_penalty = llm_chat_presence_penalty
        self.llm_pass_decoding_params = llm_pass_decoding_params
        self.llm_context_window_tokens = llm_context_window_tokens
        self.llm_configured = llm_configured
        self.messages = messages_config or MessagesConfig()
        self.memory_config = memory_config or MemoryConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("memory", {}))
        )
        self.tools_config = tools_config or ToolsConfig()
        self.log_config = log_config or LogConfig()
        self.search_config = search_config or SearchConfig()
        self.user_profile_config = user_profile_config or UserProfileConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("user_profile", {}))
        )
        self.active_task_config = active_task_config or ActiveTaskConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("active_task", {}))
        )
        self.recent_summary_config = recent_summary_config or RecentSummaryConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("recent_summary", {}))
        )
        self.search_store = search_store
        self.cron_manager = cron_manager
        self.media_router = media_router
        self.provider = provider
        self._current_session_id: ContextVar[str | None] = ContextVar("current_session_id", default=None)
        self._current_channel: ContextVar[str | None] = ContextVar("current_channel", default=None)
        self._current_external_chat_id: ContextVar[str | None] = ContextVar(
            "current_external_chat_id", default=None
        )
        self._current_images: ContextVar[list[str] | None] = ContextVar("current_images", default=None)
        self._current_audios: ContextVar[list[str] | None] = ContextVar("current_audios", default=None)
        self._current_videos: ContextVar[list[str] | None] = ContextVar("current_videos", default=None)
        self._current_outbound_media: ContextVar[dict[str, list[str]] | None] = ContextVar(
            "current_outbound_media",
            default=None,
        )
        self._current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
        self._current_work_progress: ContextVar[dict[str, Any] | None] = ContextVar(
            "current_work_progress",
            default=None,
        )
        self.background_process_manager: BackgroundProcessManager | None = None
        self.turn_context = TurnContextService(
            current_session_id=self._current_session_id,
            current_channel=self._current_channel,
            current_external_chat_id=self._current_external_chat_id,
            current_images=self._current_images,
            current_audios=self._current_audios,
            current_videos=self._current_videos,
            current_outbound_media=self._current_outbound_media,
            current_run_id=self._current_run_id,
            current_work_progress=self._current_work_progress,
        )
        self.app_home: Path | None = None
        self.tool_workspace: Path | None = None
        self.config_path: Path | None = Path(config_path).expanduser().resolve() if config_path is not None else None
        self.llm_config = llm_config
        self.prompt_logging = PromptLoggingService(
            log_config=self.log_config,
            app_home_getter=lambda: self.app_home,
        )
        self.curator: CuratorService | None = None
        self.learning_ledger: LearningLedger | None = None
        self._skill_review_tasks: dict[str, Any] = {}
        self._skill_review_rerun: set[str] = set()
        self._maintenance_tasks: dict[str, Any] = {}
        self._maintenance_rerun: set[str] = set()
        self._run_skill_reads: dict[str, set[str]] = {}
        self._delegated_task_updates: dict[str, dict[str, StoredDelegatedTask]] = {}
        self._workflow_outcomes: dict[str, dict[str, dict[str, Any]]] = {}
        # Set by runtime after MessageQueue is created; used for interim tool progress outbound messages.
        self._message_bus: Any = None
        self.permission_requests = PermissionRequestManager(
            timeout_seconds=self.tools_config.permissions.approval_timeout_seconds,
            on_event=self._emit_permission_request_event,
        )

        self.storage = self._setup_storage(storage)
        self.message_history = MessageHistoryService(
            storage=self.storage,
            search_store=self.search_store,
            max_history_getter=lambda: self.config.max_history,
        )
        self.retrieval = ProactiveRetrievalService(search_store=self.search_store)
        self._context_builder = self._setup_context_builder(context_builder)
        self.learning_ledger = self._setup_learning_ledger()
        self.history_reset = HistoryResetService(
            storage=self.storage,
            search_store=self.search_store,
            clear_session_artifacts=self._clear_session_artifacts,
        )
        self.run_trace = RunTraceRecorder(
            storage=self.storage,
            message_bus_getter=lambda: self._message_bus,
        )
        self.response_finalizer = AgentResponseFinalizer(
            run_trace=self.run_trace,
            save_message=self._save_message,
            format_log_preview=self._format_log_preview,
            log_config=self.log_config,
        )
        self.task_intents = TaskIntentService()
        self.completion_gate = CompletionGateService()
        self.auto_continue = AutoContinueService(max_auto_continues=1)
        self.work_progress = WorkProgressService()
        self.run_state = AgentRunStateService()
        self.user_overlay_store = UserOverlayStore(app_home=self.app_home)
        self.user_overlay_index = UserOverlayIndexStore(app_home=self.app_home)
        self.user_overlay_promotion = UserOverlayPromotionService(
            overlay_store=self.user_overlay_store,
            index_store=self.user_overlay_index,
        )
        self.turn_runner = AgentTurnRunner(
            run_trace=self.run_trace,
            response_finalizer=self.response_finalizer,
            turn_context=self.turn_context,
            run_state=self.run_state,
            task_intents=self.task_intents,
            completion_gate=self.completion_gate,
            auto_continue=self.auto_continue,
            work_progress=self.work_progress,
            connect_mcp=lambda: self.connect_mcp(),
            save_message=lambda *args, **kwargs: self._save_message(*args, **kwargs),
            emit_run_event=lambda *args, **kwargs: self._emit_run_event(*args, **kwargs),
            call_llm=lambda *args, **kwargs: self.call_llm(*args, **kwargs),
            run_workflow=lambda workflow, task, start_step=None: self.run_workflow(workflow, task, start_step),
            run_verify=lambda action, path, pytest_args=(): self.run_verify(action=action, path=path, pytest_args=pytest_args),
            verification_available=lambda: self.tools.get("verify") is not None,
            get_queued_outbound_media=self._get_queued_outbound_media,
            media_saved_ack=lambda: self.messages.agent.media_saved_ack,
            llm_not_configured_message=lambda: self.messages.agent.llm_not_configured,
            format_log_preview=self._format_log_preview,
            set_session_overlay_id=lambda session_id, metadata, channel, sender_id: self._set_session_overlay_id(
                session_id,
                metadata,
                channel,
                sender_id,
            ),
            get_work_state=lambda session_id: self._get_work_state(session_id),
            save_work_state=lambda state: self._save_work_state(state),
            apply_completion_gate_result=lambda session_id, result: self._maybe_apply_completion_gate_result(
                session_id,
                result,
            ),
            apply_work_progress=lambda session_id, progress, state: self._maybe_apply_work_progress(session_id, progress, state),
            schedule_curator=lambda session_id, run_id, channel, external_chat_id, result: self._schedule_curator(
                session_id,
                run_id,
                channel,
                external_chat_id,
                result,
            ),
            finalize_learning_reuse=lambda session_id, run_id, success: self._finalize_learning_reuse(session_id, run_id, success),
            consume_delegated_task_updates=self._consume_delegated_task_updates,
            clear_delegated_task_updates=self._clear_delegated_task_updates,
            consume_workflow_outcomes=self._consume_workflow_outcomes,
            clear_workflow_outcomes=self._clear_workflow_outcomes,
            worktree_sandbox_enabled=lambda: self.config.worktree_sandbox_enabled,
            workspace_root=lambda: Path(self.tool_workspace or getattr(self._context_builder, "workspace", Path.cwd())),
        )
        self.permission_events = PermissionEventRecorder(
            emit_run_event=self._emit_run_event,
            format_log_preview=self._format_log_preview,
        )
        self.permissions = AgentPermissionService(
            requests=self.permission_requests,
            events=self.permission_events,
            current_session_id=self.turn_context.current_session_id,
            current_run_id=self.turn_context.current_run_id,
            current_channel=self.turn_context.current_channel,
            current_external_chat_id=self.turn_context.current_external_chat_id,
        )
        self.run_hooks = RunHookService(
            message_bus_getter=lambda: self._message_bus,
            add_run_part=self._add_run_part,
            emit_run_event=self._emit_run_event,
            format_log_preview=self._format_log_preview,
        )
        self.background_notifications = BackgroundSessionNotificationService(
            message_bus_getter=lambda: self._message_bus,
            save_message=self._save_message,
        )
        self.file_changes = RunFileChangeService(
            storage=self.storage,
            workspace_for_session=self._get_workspace_for_session,
            emit_run_event=self._emit_run_event,
            format_log_preview=self._format_log_preview,
            note_file_change=self.turn_context.note_file_change,
        )
        self.media_service = AgentMediaService(
            workspace_root_getter=lambda: self.tool_workspace
            or getattr(self._context_builder, "workspace", Path.cwd()),
            app_home_getter=lambda: self.app_home,
        )
        self.turn_inputs = TurnInputPreparer(
            media_service=self.media_service,
            format_log_preview=self._format_log_preview,
        )
        self.tools = self._setup_tools(tools)
        self.tools.set_permission_request_handler(self._handle_tool_permission_request)
        self.subagents = SubagentRunService(
            storage=self.storage,
            tools=self.tools,
            max_history_getter=lambda: self.config.max_history,
            app_home_getter=lambda: self.app_home,
            workspace_getter=self._get_current_workspace,
            current_session_id_getter=self._get_current_session_id,
            current_run_id_getter=self.turn_context.current_run_id,
            current_channel_getter=self.turn_context.current_channel,
            current_external_chat_id_getter=self.turn_context.current_external_chat_id,
            provider_getter=lambda: self.provider,
            llm_config_getter=lambda: self.llm_config,
            should_cancel_parent_run=lambda session_id, run_id: self._is_run_cancel_requested(session_id, run_id),
            skills_loader_getter=lambda: getattr(self._context_builder, "skills_loader", None),
            save_message=self._save_message,
            execute_messages=self._execute_messages,
            log_prepared_messages=self._log_prepared_messages,
            format_log_preview=self._format_log_preview,
            run_trace=self.run_trace,
            run_hooks=self.run_hooks,
            record_delegated_task_update=self._record_delegated_task_update,
        )
        self.workflows = SubagentWorkflowService(
            current_session_id_getter=self._get_current_session_id,
            current_run_id_getter=self.turn_context.current_run_id,
            current_channel_getter=self.turn_context.current_channel,
            current_external_chat_id_getter=self.turn_context.current_external_chat_id,
            run_subagent_task=lambda task, prompt_type: self.subagents.run_task(task, prompt_type),
            emit_run_event=self._emit_run_event,
            format_log_preview=self._format_log_preview,
            record_workflow_outcome=self._record_workflow_outcome,
        )
        self.prompt_budget = PromptBudgetService(
            context_builder=self._context_builder,
            provider=self.provider,
            tools=self.tools,
            history_token_budget_getter=lambda: self.config.history_token_budget,
            context_window_tokens_getter=lambda: self.llm_context_window_tokens,
            output_token_reserve_getter=lambda: self.llm_chat_max_tokens,
        )
        self.mcp_lifecycle = McpLifecycleService(
            tools=self.tools,
            tools_config=self.tools_config,
            context_builder=self._context_builder,
            config_path_getter=self._get_config_path,
        )
        self.memory = self._setup_memory_store()
        self.memory_consolidation = self._setup_memory_consolidation()
        self._register_memory_tool()
        self.execution_engine = self._setup_execution_engine()
        self.skill_review = SkillReviewService(
            storage=self.storage,
            tools=self.tools,
            transcript_message_limit_getter=lambda: self.config.skill_review_transcript_messages,
            max_tool_iterations_getter=lambda: self.config.skill_review_max_tool_iterations,
            build_system_prompt=lambda session_id: self._context_builder.build_system_prompt(session_id),
            execute_messages=self._execute_messages,
        )
        self.user_profile_update = self._setup_user_profile_update()
        self.active_task_update = self._setup_active_task_update()
        self.active_task_commands = ActiveTaskCommandService(
            storage=self.storage,
            app_home_getter=lambda: self.app_home,
            workspace_root_getter=lambda: self.tool_workspace,
        )
        self.recent_summary_update = self._setup_recent_summary_update()
        self.curator = CuratorService(
            maybe_consolidate_memory=lambda session_id: self._maybe_consolidate_memory(session_id),
            maybe_update_recent_summary=lambda session_id: self._maybe_update_recent_summary(session_id),
            maybe_update_user_profile=lambda session_id: self._maybe_update_user_profile(session_id),
            maybe_update_active_task=lambda session_id: self._maybe_update_active_task(session_id),
            run_skill_review=lambda session_id: self._run_skill_review(session_id),
            should_run_skill_review=lambda result: self._should_schedule_skill_review(result),
            read_memory_snapshot=self._read_memory_snapshot,
            read_recent_summary_snapshot=self._read_recent_summary_snapshot,
            read_user_profile_snapshot=self._read_user_profile_snapshot,
            read_active_task_snapshot=self._read_active_task_snapshot,
            read_skill_snapshot=self._read_skill_snapshot,
            record_learning=lambda *args, **kwargs: self._record_learning(*args, **kwargs),
            emit_run_event=lambda session_id, run_id, event_type, payload, channel, external_chat_id: self._emit_run_event(
                session_id,
                run_id,
                event_type,
                payload,
                channel=channel,
                external_chat_id=external_chat_id,
            ),
            state_path_for_session=lambda session_id: get_session_curator_state_file(
                session_id,
                app_home=self.app_home,
                workspace_root=self.tool_workspace,
            ),
        )
        self._skill_review_tasks = self.curator.tasks
        self._skill_review_rerun = self.curator.rerun_keys
        self._maintenance_tasks = self.curator.tasks
        self._maintenance_rerun = self.curator.rerun_keys
        self.llm_calls = LlmCallService(
            config=self.config,
            maybe_seed_active_task=lambda session_id, message, task_intent=None: self._maybe_seed_active_task(
                session_id,
                message,
                task_intent=task_intent,
            ),
            load_history=lambda session_id: self._load_history(session_id),
            get_current_audios=self._get_current_audios,
            get_current_videos=self._get_current_videos,
            augment_message_for_media=lambda *args, **kwargs: self._augment_message_for_media(*args, **kwargs),
            estimate_tool_schema_tokens=lambda *args, **kwargs: self._estimate_tool_schema_tokens(*args, **kwargs),
            trim_history_to_token_budget=lambda *args, **kwargs: self._trim_history_to_token_budget(*args, **kwargs),
            effective_context_token_budget=self._effective_context_token_budget,
            llm_context_window_tokens=lambda: self.llm_context_window_tokens,
            llm_chat_max_tokens=lambda: self.llm_chat_max_tokens,
            sync_runtime_mcp_tools_context=self._sync_runtime_mcp_tools_context,
            build_messages=lambda **kwargs: self._context_builder.build_messages(**kwargs),
            build_system_prompt=lambda session_id: self._context_builder.build_system_prompt(session_id),
            log_prepared_messages=self._log_prepared_messages,
            get_work_state_summary=lambda session_id: self._get_work_state_summary(session_id),
            build_proactive_retrieval_context=lambda session_id, current_message: self.retrieval.build_context(
                session_id=session_id,
                current_message=current_message,
            ),
            get_tool_registry=lambda: self.tools,
            get_current_run_id=self.turn_context.current_run_id,
            should_cancel_run=lambda session_id, run_id: self._is_run_cancel_requested(session_id, run_id),
            make_tool_progress_hook=lambda *args, **kwargs: self._make_tool_progress_hook(*args, **kwargs),
            make_tool_result_hook=lambda *args, **kwargs: self._make_tool_result_hook(*args, **kwargs),
            make_llm_status_hook=lambda *args, **kwargs: self._make_llm_status_hook(*args, **kwargs),
            make_llm_delta_hook=lambda *args, **kwargs: self._make_llm_delta_hook(*args, **kwargs),
            make_tool_input_delta_hook=lambda *args, **kwargs: self._make_tool_input_delta_hook(*args, **kwargs),
            make_reasoning_delta_hook=lambda *args, **kwargs: self._make_reasoning_delta_hook(*args, **kwargs),
            execute_messages=lambda *args, **kwargs: self._execute_messages(*args, **kwargs),
        )

    def _trim_history_to_token_budget(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        channel: str | None,
        session_id: str,
        tool_schema_tokens: int = 0,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Trim oldest history messages when the prompt would exceed the history token budget."""
        return self.prompt_budget.trim_history_to_token_budget(
            history=history,
            current_message=current_message,
            channel=channel,
            session_id=session_id,
            tool_schema_tokens=tool_schema_tokens,
        )

    def _effective_context_token_budget(self) -> int:
        """Return the prompt token budget after applying model window and output reserve."""
        return self.prompt_budget.effective_context_token_budget()

    def _estimate_tool_schema_tokens(self, *, allow_tools: bool, tool_registry: ToolRegistry | None = None) -> int:
        """Estimate token cost of tool schemas sent with the request."""
        return self.prompt_budget.estimate_tool_schema_tokens(
            allow_tools=allow_tools,
            tool_registry=tool_registry,
        )

    def _set_session_overlay_id(
        self,
        session_id: str,
        metadata: dict[str, Any] | None,
        channel: str | None,
        sender_id: str | None,
    ) -> None:
        """Propagate the resolved stable overlay identity into the context builder when supported."""
        setter = getattr(self._context_builder, "set_session_overlay_id", None)
        if not callable(setter):
            return
        overlay_id = resolve_user_overlay_id(channel=channel, sender_id=sender_id, metadata=metadata)
        setter(session_id, overlay_id)

    def _get_session_overlay_id(self, session_id: str) -> str | None:
        getter = getattr(self._context_builder, "get_session_overlay_id", None)
        if not callable(getter):
            return None
        value = getter(session_id)
        text = str(value or "").strip()
        return text or None

    def _maybe_update_user_overlay(self, session_id: str) -> dict[str, Any] | None:
        overlay_id = self._get_session_overlay_id(session_id)
        if not overlay_id or self.app_home is None:
            return None
        bootstrap_dir = getattr(self._context_builder, "bootstrap_dir", None)
        profile_store = create_user_profile_store(
            self.app_home,
            session_id,
            bootstrap_dir=bootstrap_dir,
            workspace_root=self.tool_workspace,
        )
        return self.user_overlay_promotion.update_from_session_documents(
            overlay_id,
            profile_block=profile_store.read_managed_block(),
            response_language_block=profile_store.read_response_language_block(),
            memory_text=self.memory.read(session_id),
            source_session_id=session_id,
        )

    def _setup_storage(self, storage: StorageProvider | None) -> StorageProvider:
        """Resolve the storage provider used by the agent."""
        if storage is None:
            from ..storage import MemoryStorage

            return MemoryStorage()
        return storage

    def _setup_context_builder(self, context_builder: ContextBuilder | None) -> ContextBuilder:
        """Resolve or bootstrap the context builder and workspace paths."""
        if context_builder is None:
            try:
                from ..context.paths import get_app_home, get_tool_workspace, sync_templates
                from ..context import FileContextBuilder

                self.app_home = get_app_home()
                self.tool_workspace = get_tool_workspace(self.app_home)
                sync_templates(self.app_home)
                return FileContextBuilder(
                    app_home=self.app_home,
                    tool_workspace=self.tool_workspace,
                )
            except Exception as e:
                raise RuntimeError(f"無法建立 ContextBuilder: {e}")

        self.app_home = getattr(context_builder, "app_home", None)
        self.tool_workspace = getattr(context_builder, "tool_workspace", None)
        if self.tool_workspace is None:
            self.tool_workspace = getattr(context_builder, "workspace", Path.cwd())
        return context_builder

    def _setup_learning_ledger(self) -> LearningLedger:
        """Create the session learning ledger and attach it to compatible context builders."""
        ledger = LearningLedger(
            state_path_for_session=lambda session_id: get_session_learning_state_file(
                session_id,
                app_home=self.app_home,
                workspace_root=self.tool_workspace,
            )
        )
        setter = getattr(self._context_builder, "set_learning_ledger", None)
        if callable(setter):
            setter(ledger)
        return ledger

    def _setup_tools(self, tools: ToolRegistry | None) -> ToolRegistry:
        """Resolve the tool registry and populate defaults when needed."""
        registry = tools or ToolRegistry()
        if not registry.tool_names:
            self.tools = registry
            self._register_default_tools()
            return self.tools
        return registry

    def _setup_memory_store(self) -> MemoryStore:
        """Create the long-term memory store."""
        memory_dir = getattr(self._context_builder, "memory_dir", Path.cwd() / "memory")
        return MemoryStore(memory_dir, app_home=self.app_home, workspace_root=self.tool_workspace)

    def _setup_memory_consolidation(self) -> MemoryConsolidationService:
        """Create the memory consolidation maintenance service."""
        return MemoryConsolidationService(
            storage=self.storage,
            memory_store=self.memory,
            provider=self.provider,
            threshold=self.memory_config.threshold,
            token_threshold=self.memory_config.token_threshold,
            memory_llm=self.memory_config.llm,
        )

    def _setup_execution_engine(self) -> ExecutionEngine:
        """Create the execution engine used for the LLM/tool loop."""
        return ExecutionEngine(
            provider=self.provider,
            tools=self.tools,
            tools_config=self.tools_config,
            search_store=self.search_store,
            empty_response_fallback=self.messages.agent.empty_response_fallback,
            save_message=self._save_message,
            format_log_preview=self._format_log_preview,
            summarize_messages=self._summarize_messages,
            sanitize_response_content=self._sanitize_response_content,
            chat_temperature=self.llm_chat_temperature,
            chat_max_tokens=self.llm_chat_max_tokens,
            chat_top_p=self.llm_chat_top_p,
            chat_frequency_penalty=self.llm_chat_frequency_penalty,
            chat_presence_penalty=self.llm_chat_presence_penalty,
            pass_decoding_params=self.llm_pass_decoding_params,
            context_compaction_enabled=self.config.context_compaction_enabled,
            context_compaction_token_budget=self._effective_context_token_budget(),
            context_window_tokens=self.llm_context_window_tokens,
            context_output_reserve_tokens=self.llm_chat_max_tokens,
            context_compaction_threshold_ratio=self.config.context_compaction_threshold_ratio,
            context_compaction_min_messages=self.config.context_compaction_min_messages,
            context_compaction_strategy=self.config.context_compaction_strategy,
            context_compaction_llm=self.config.context_compaction_llm,
        )

    def _setup_user_profile_update(self) -> UserProfileUpdateService:
        """Create the optional USER.md update service."""
        consolidator: UserProfileConsolidator | None = None
        if self.app_home is not None:
            bootstrap_dir = getattr(self._context_builder, "bootstrap_dir", None)
            consolidator = UserProfileConsolidator(
                storage=self.storage,
                provider=self.provider,
                model=self.provider.get_default_model(),
                profile_store_factory=lambda session_id: create_user_profile_store(
                    self.app_home,
                    session_id,
                    bootstrap_dir=bootstrap_dir,
                    workspace_root=self.tool_workspace,
                ),
                threshold=self.user_profile_config.threshold,
                lookback_messages=self.user_profile_config.lookback_messages,
                enabled=self.user_profile_config.enabled,
                llm=self.user_profile_config.llm,
            )

        return UserProfileUpdateService(consolidator)

    def _setup_recent_summary_update(self) -> RecentSummaryUpdateService:
        """Create the optional RECENT_SUMMARY.md update service."""
        memory_dir = getattr(self._context_builder, "memory_dir", None)
        if memory_dir is None:
            return RecentSummaryUpdateService(None)

        summary_store = RecentSummaryStore(memory_dir, app_home=self.app_home, workspace_root=self.tool_workspace)
        consolidator = RecentSummaryConsolidator(
            storage=self.storage,
            provider=self.provider,
            model=self.provider.get_default_model(),
            summary_store=summary_store,
            threshold=self.recent_summary_config.threshold,
            token_threshold=self.recent_summary_config.token_threshold,
            lookback_messages=self.recent_summary_config.lookback_messages,
            keep_last_messages=self.recent_summary_config.keep_last_messages,
            enabled=self.recent_summary_config.enabled,
            llm=self.recent_summary_config.llm,
        )
        return RecentSummaryUpdateService(consolidator)

    def _setup_active_task_update(self) -> ActiveTaskUpdateService:
        """Create the optional ACTIVE_TASK.md update service."""
        if self.app_home is None:
            return ActiveTaskUpdateService(None)

        consolidator = ActiveTaskConsolidator(
            storage=self.storage,
            provider=self.provider,
            model=self.provider.get_default_model(),
            active_task_store_factory=lambda session_id: create_active_task_store(
                self.app_home,
                session_id,
                workspace_root=self.tool_workspace,
            ),
            threshold=self.active_task_config.threshold,
            lookback_messages=self.active_task_config.lookback_messages,
            enabled=self.active_task_config.enabled,
            llm=self.active_task_config.llm,
        )
        return ActiveTaskUpdateService(consolidator)

    def _clear_recent_summary(self, session_id: str) -> None:
        memory_dir = getattr(self._context_builder, "memory_dir", None)
        if memory_dir is None:
            return
        RecentSummaryStore(memory_dir, app_home=self.app_home, workspace_root=self.tool_workspace).clear(session_id)

    async def _clear_session_artifacts(self, session_id: str) -> None:
        """Delete all persisted session-scoped files and runtime metadata for one session."""
        await self._clear_work_state(session_id)
        if self.background_process_manager is not None:
            await self.background_process_manager.kill_owned_sessions(session_id)
        if self.curator is not None:
            self.curator.clear_session(session_id)
        if self.learning_ledger is not None:
            self.learning_ledger.clear_session(session_id)
        workspace = self._get_workspace_for_session(session_id)
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

    def _register_memory_tool(self) -> None:
        """Register the save_memory tool."""
        register_memory_tool(self.tools, self.memory, self._get_current_session_id)

    def _sync_runtime_mcp_tools_context(self) -> None:
        """Expose connected MCP tools to context builders that support prompt summaries."""
        self.mcp_lifecycle.sync_runtime_tools_context()

    def _get_config_path(self) -> Path | None:
        if self.config_path is not None:
            return self.config_path
        if self.app_home is not None:
            return (self.app_home / "opensprite.json").resolve()
        return None

    async def connect_mcp(self) -> None:
        """Connect configured MCP servers once and register their tools."""
        await self.mcp_lifecycle.connect()

    async def close_mcp(self) -> None:
        """Close any active MCP sessions and reset lifecycle flags."""
        await self.mcp_lifecycle.close()

    def _schedule_background_maintenance(
        self,
        *,
        kind: str,
        session_id: str,
        runner: Callable[[str], Awaitable[None]],
    ) -> None:
        """Back-compat wrapper for callers that still schedule maintenance directly."""
        del kind, runner
        self._schedule_post_response_maintenance(session_id)

    def _schedule_post_response_maintenance(self, session_id: str) -> None:
        """Queue maintenance-only curator work without blocking the reply."""
        if self.curator is None:
            return
        self.curator.schedule_maintenance(
            session_id,
            run_id=self.turn_context.current_run_id(),
            channel=self.turn_context.current_channel(),
            external_chat_id=self.turn_context.current_external_chat_id(),
        )

    def _schedule_curator(
        self,
        session_id: str,
        run_id: str,
        channel: str | None,
        external_chat_id: str | None,
        result: ExecutionResult,
    ) -> None:
        """Queue the full curator pass for one completed visible run."""
        if self.curator is None:
            return
        self.curator.schedule_after_turn(
            session_id=session_id,
            run_id=run_id,
            channel=channel,
            external_chat_id=external_chat_id,
            result=result,
        )

    async def wait_for_background_maintenance(self) -> None:
        """Wait until all currently scheduled maintenance tasks finish."""
        if self.curator is None:
            return
        await self.curator.wait()

    async def close_background_maintenance(self) -> None:
        """Cancel and drain any in-flight maintenance tasks."""
        if self.curator is None:
            return
        await self.curator.close()

    async def wait_for_background_skill_reviews(self) -> None:
        """Wait until all currently scheduled skill review tasks finish."""
        if self.curator is None:
            return
        await self.curator.wait()

    async def close_background_skill_reviews(self) -> None:
        """Cancel and drain any in-flight skill review tasks."""
        if self.curator is None:
            return
        await self.curator.close()

    async def close_background_processes(self) -> None:
        """Terminate managed background exec sessions before the event loop closes."""
        process_tool = self.tools.get("process")
        manager = getattr(process_tool, "manager", None)
        close = getattr(manager, "close", None)
        if close is not None:
            await close()

    async def _maybe_seed_active_task(
        self,
        session_id: str,
        current_message: str,
        *,
        task_intent: TaskIntent | None = None,
    ) -> None:
        """Create a minimal ACTIVE_TASK.md before the first heavy turn when no task is active yet."""
        if task_intent is None:
            task_intent = self.task_intents.classify(current_message)
        await self.active_task_commands.maybe_seed(
            session_id,
            current_message,
            enabled=self.active_task_config.enabled,
            task_intent=task_intent,
        )

    async def reload_mcp_from_config(self) -> str:
        """Reload MCP settings from disk and reconnect MCP tools for this agent."""
        return await self.mcp_lifecycle.reload_from_config()

    @staticmethod
    def _refresh_consolidator_llm(consolidator: Any | None, provider: LLMProvider) -> None:
        """Point optional background document consolidators at the active LLM."""
        if consolidator is None:
            return
        if hasattr(consolidator, "provider"):
            consolidator.provider = provider
        if hasattr(consolidator, "model"):
            consolidator.model = provider.get_default_model()

    def reload_llm_from_config(self, config: Config) -> dict[str, Any]:
        """Reload the active chat LLM from an already persisted Config."""
        cfg = config.llm.get_active()
        llm_runtime = resolve_provider_runtime(
            cfg,
            provider_name=cfg.provider or config.llm.default or "",
            app_home=config.source_path.parent if config.source_path is not None else self.app_home,
        )
        provider = create_llm_from_runtime(llm_runtime)

        self.provider = provider
        self.llm_chat_temperature = config.llm.temperature
        self.llm_chat_max_tokens = config.llm.max_tokens
        self.llm_chat_top_p = config.llm.top_p
        self.llm_chat_frequency_penalty = config.llm.frequency_penalty
        self.llm_chat_presence_penalty = config.llm.presence_penalty
        self.llm_pass_decoding_params = config.llm.pass_decoding_params
        self.llm_context_window_tokens = cfg.context_window_tokens
        self.llm_configured = config.is_llm_configured

        self.prompt_budget.provider = provider
        self.execution_engine.provider = provider
        self.execution_engine.chat_temperature = self.llm_chat_temperature
        self.execution_engine.chat_max_tokens = self.llm_chat_max_tokens
        self.execution_engine.chat_top_p = self.llm_chat_top_p
        self.execution_engine.chat_frequency_penalty = self.llm_chat_frequency_penalty
        self.execution_engine.chat_presence_penalty = self.llm_chat_presence_penalty
        self.execution_engine.pass_decoding_params = self.llm_pass_decoding_params
        self.execution_engine.context_compaction_token_budget = self._effective_context_token_budget()
        self.execution_engine.context_window_tokens = self.llm_context_window_tokens
        self.execution_engine.context_output_reserve_tokens = max(0, self.llm_chat_max_tokens)

        self.memory_consolidation.provider = provider
        self._refresh_consolidator_llm(self.user_profile_update.consolidator, provider)
        self._refresh_consolidator_llm(self.recent_summary_update.consolidator, provider)
        self._refresh_consolidator_llm(self.active_task_update.consolidator, provider)

        logger.info(
            "LLM runtime reloaded | provider={} model={} configured={}",
            config.llm.default or "default",
            provider.get_default_model(),
            self.llm_configured,
        )
        return {
            "provider_id": config.llm.default,
            "model": provider.get_default_model(),
            "configured": self.llm_configured,
            "context_window_tokens": self.llm_context_window_tokens,
        }

    def reload_media_from_config(self, config: Config) -> dict[str, Any]:
        """Reload media analysis providers from an already persisted Config."""
        vision = getattr(config, "vision", None)
        ocr = getattr(config, "ocr", None)
        speech = getattr(config, "speech", None)
        video = getattr(config, "video", None)

        if self.media_router is None:
            self.media_router = MediaRouter()

        self.media_router.image_provider = create_image_analysis_provider(
            provider=vision.provider,
            api_key=vision.api_key,
            default_model=vision.model,
            base_url=vision.base_url,
        ) if vision and vision.enabled else None
        self.media_router.ocr_provider = create_image_analysis_provider(
            provider=ocr.provider,
            api_key=ocr.api_key,
            default_model=ocr.model,
            base_url=ocr.base_url,
        ) if ocr and ocr.enabled else None
        self.media_router.speech_provider = OpenAICompatibleSpeechProvider(
            api_key=speech.api_key,
            default_model=speech.model,
            base_url=speech.base_url,
        ) if speech and speech.enabled else None
        self.media_router.video_provider = OpenAICompatibleVideoProvider(
            api_key=video.api_key,
            default_model=video.model,
            base_url=video.base_url,
        ) if video and video.enabled else None

        logger.info(
            "Media runtime reloaded | vision={} ocr={} speech={} video={}",
            bool(self.media_router.image_provider),
            bool(self.media_router.ocr_provider),
            bool(self.media_router.speech_provider),
            bool(self.media_router.video_provider),
        )
        return {
            "vision_enabled": bool(self.media_router.image_provider),
            "ocr_enabled": bool(self.media_router.ocr_provider),
            "speech_enabled": bool(self.media_router.speech_provider),
            "video_enabled": bool(self.media_router.video_provider),
        }

    def _get_current_session_id(self) -> str | None:
        """Return the current task-local session id."""
        return self.turn_context.current_session_id()

    def _current_background_session_owner(self) -> dict[str, str | None] | None:
        """Return active turn ownership metadata for managed background sessions."""
        session_id = self.turn_context.current_session_id()
        run_id = self.turn_context.current_run_id()
        channel = self.turn_context.current_channel()
        external_chat_id = self.turn_context.current_external_chat_id()
        if session_id is None and run_id is None:
            return None
        return {
            "session_id": session_id,
            "run_id": run_id,
            "channel": channel,
            "external_chat_id": external_chat_id,
        }

    def _set_background_process_manager(self, manager: BackgroundProcessManager) -> None:
        """Store the shared manager used by exec/process background sessions."""
        self.background_process_manager = manager

    async def _cancel_owned_background_sessions(
        self,
        session_id: str,
        run_id: str,
    ) -> list[BackgroundSession]:
        """Kill managed background sessions started by the specified run."""
        if self.background_process_manager is None:
            return []
        return await self.background_process_manager.kill_owned_sessions(session_id, run_id=run_id)

    def _get_current_workspace(self) -> Path:
        """Resolve the current task-local workspace."""
        workspace_root = self.tool_workspace or getattr(self._context_builder, "workspace", Path.cwd())
        session_id = self._get_current_session_id() or "default"
        return get_session_workspace(session_id, workspace_root=workspace_root)

    def _get_workspace_for_session(self, session_id: str) -> Path:
        """Resolve the isolated workspace for a specific session id."""
        workspace_root = self.tool_workspace or getattr(self._context_builder, "workspace", Path.cwd())
        return get_session_workspace(session_id, workspace_root=workspace_root)

    async def preview_run_file_change_revert(
        self,
        session_id: str,
        run_id: str,
        change_id: int,
    ) -> dict[str, Any]:
        """Inspect whether one captured file change can be safely reverted."""
        return await self.file_changes.preview_revert(session_id, run_id, change_id)

    async def revert_run_file_change(
        self,
        session_id: str,
        run_id: str,
        change_id: int,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Safely revert one captured file change; defaults to dry-run inspection."""
        return await self.file_changes.revert(session_id, run_id, change_id, dry_run=dry_run)

    @staticmethod
    def _decode_media_data_url(payload: str, media_prefix: str) -> tuple[str, bytes] | None:
        """Decode a media data URL into a MIME type and bytes."""
        return AgentMediaService.decode_data_url(payload, media_prefix)

    def _persist_inbound_media(
        self,
        session_id: str,
        media_items: list[str] | None,
        *,
        media_prefix: str,
        directory_name: str,
        extensions: dict[str, str],
    ) -> list[str]:
        """Persist inbound media data URLs under a session workspace directory."""
        return self.media_service.persist_inbound_media(
            session_id,
            media_items,
            media_prefix=media_prefix,
            directory_name=directory_name,
            extensions=extensions,
        )

    def _persist_inbound_images(self, session_id: str, images: list[str] | None) -> list[str]:
        """Persist inbound image data URLs under the session workspace images directory."""
        return self.media_service.persist_inbound_images(session_id, images)

    def _persist_inbound_audios(self, session_id: str, audios: list[str] | None) -> list[str]:
        """Persist inbound audio data URLs under the session workspace audios directory."""
        return self.media_service.persist_inbound_audios(session_id, audios)

    def _persist_inbound_videos(self, session_id: str, videos: list[str] | None) -> list[str]:
        """Persist inbound video data URLs under the session workspace videos directory."""
        return self.media_service.persist_inbound_videos(session_id, videos)

    def _register_default_tools(self) -> None:
        """
        註冊代理人的預設工具。
        
        Register default tools for the agent.
        
        註冊檔案系統工具、Shell 執行、網頁搜尋和網頁抓取。
        Registers filesystem tools, shell execution, web search, and web fetch.
        """
        register_default_tools(
            self.tools,
            workspace_resolver=self._get_current_workspace,
            get_session_id=self._get_current_session_id,
            run_subagent=self.run_subagent,
            run_subagents_many=self.run_subagents_many,
            run_workflow=self.run_workflow,
            workflow_catalog_getter=lambda: self.workflows.catalog(),
            config_path_resolver=self._get_config_path,
            reload_mcp=self.reload_mcp_from_config,
            app_home=self.app_home,
            skills_loader=getattr(self._context_builder, "skills_loader", None),
            tools_config=self.tools_config,
            search_store=self.search_store,
            search_config=self.search_config,
            cron_manager=self.cron_manager,
            cron_messages_config=self.messages.cron,
            media_router=self.media_router,
            get_current_images=self._get_current_images,
            get_current_audios=self._get_current_audios,
            get_current_videos=self._get_current_videos,
            queue_outbound_media=self._queue_outbound_media,
            background_notification_factory=self._make_background_session_exit_notifier,
            background_session_owner_factory=self._current_background_session_owner,
            process_manager_callback=self._set_background_process_manager,
            active_task_store_factory=self._get_active_task_store,
            get_message_count=lambda session_id: get_storage_message_count(self.storage, session_id),
            file_change_recorder=self._record_file_changes,
            storage=self.storage,
            preview_run_file_change_revert=self.preview_run_file_change_revert,
        )
        
        logger.info(f"agent.init | tools={', '.join(self.tools.tool_names)}")

    async def _load_history(self, session_id: str) -> list[ChatMessage]:
        """
        從儲存區載入對話歷史。
        
        Load conversation history from storage.
        
        Args:
            session_id: Internal session ID.
        
        Returns:
            ChatMessage 物件列表，供 LLM 使用。
            List of ChatMessage objects for LLM consumption.
        """
        return await self.message_history.load_history(session_id)

    async def _save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        儲存訊息到儲存區。
        
        Save a message to storage.
        
        Args:
            session_id: Internal session ID.
            role: 訊息角色（"user"、"assistant" 或 "tool"）。
                  Message role ("user", "assistant", or "tool").
            content: 訊息內容。Message content.
            tool_name: 如果是工具結果，記錄工具名稱。
                       Tool name if this is a tool result.
        """
        await self.message_history.save_message(
            session_id,
            role,
            content,
            tool_name=tool_name,
            metadata=metadata,
        )

    def _log_prepared_messages(self, log_id: str, messages: list[dict[str, Any]]) -> None:
        """Log prepared prompt/messages when prompt logging is enabled."""
        self.prompt_logging.log_prepared_messages(log_id, messages)

    async def _execute_messages(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        allow_tools: bool,
        provider_override: LLMProvider | None = None,
        tool_result_session_id: str | None = None,
        tool_registry: ToolRegistry | None = None,
        on_tool_before_execute: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_tool_after_execute: Callable[[str, dict[str, Any], str], Awaitable[None]] | None = None,
        on_llm_status: Callable[[str], Awaitable[None]] | None = None,
        on_response_delta: Callable[[str, str, str, int], Awaitable[None]] | None = None,
        refresh_system_prompt: Callable[[], str] | None = None,
        max_tool_iterations: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
        work_state_summary: str = "",
    ) -> ExecutionResult:
        """Run the shared LLM execution loop for main and delegated agents."""
        return await self.execution_engine.execute_messages(
            log_id,
            chat_messages,
            allow_tools=allow_tools,
            provider_override=provider_override,
            tool_result_session_id=tool_result_session_id,
            tool_registry=tool_registry,
            on_tool_before_execute=on_tool_before_execute,
            on_tool_after_execute=on_tool_after_execute,
            on_llm_status=on_llm_status,
            on_response_delta=on_response_delta,
            refresh_system_prompt=refresh_system_prompt,
            max_tool_iterations=max_tool_iterations,
            should_cancel=should_cancel,
            work_state_summary=work_state_summary,
        )

    def _build_subagent_tools(self, prompt_type: str, *, workspace: Path | None = None) -> ToolRegistry:
        """Build the tool registry exposed to one subagent profile."""
        return self.subagents.build_tools(prompt_type, workspace=workspace)

    def _get_current_images(self) -> list[str] | None:
        """Return images attached to the current active turn."""
        return self.turn_context.current_images()

    def _get_current_audios(self) -> list[str] | None:
        """Return audios attached to the current active turn."""
        return self.turn_context.current_audios()

    def _get_current_videos(self) -> list[str] | None:
        """Return videos attached to the current active turn."""
        return self.turn_context.current_videos()

    def _queue_outbound_media(self, kind: str, payload: str) -> str | None:
        """Queue one media payload to be attached to the current assistant reply."""
        return self.turn_context.queue_outbound_media(kind, payload)

    def _get_queued_outbound_media(self) -> dict[str, list[str]]:
        """Return queued outbound media for the current turn."""
        return self.turn_context.queued_outbound_media()

    @staticmethod
    def _augment_message_for_media(
        current_message: str,
        user_images: list[str] | None,
        user_audios: list[str] | None,
        user_videos: list[str] | None,
        user_image_files: list[str] | None = None,
        user_audio_files: list[str] | None = None,
        user_video_files: list[str] | None = None,
    ) -> str:
        """Add lightweight prompt hints when the current turn includes media."""
        return AgentMediaService.augment_message_for_media(
            current_message,
            user_images,
            user_audios,
            user_videos,
            user_image_files=user_image_files,
            user_audio_files=user_audio_files,
            user_video_files=user_video_files,
        )

    async def call_llm(
        self,
        session_id: str,
        current_message: str,
        channel: str | None = None,
        allow_tools: bool = True,
        user_images: list[str] | None = None,
        user_image_files: list[str] | None = None,
        user_audio_files: list[str] | None = None,
        user_video_files: list[str] | None = None,
        *,
        external_chat_id: str | None = None,
        emit_tool_progress: bool = False,
        task_intent: TaskIntent | None = None,
    ) -> ExecutionResult:
        """
        呼叫 LLM 生成對話回應。
        
        Call LLM to generate a response for the current conversation.
        
        如果 LLM 請求工具呼叫，會處理工具執行迴圈。
        Handles tool execution loop if LLM requests tool calls.
        
        Args:
            session_id: Internal session ID for loading history.
            current_message: 本輪使用者輸入內容。
                             The current user message for this turn.
            channel: 頻道名稱（例如 "telegram"、"console"）。用於上下文。
                      Channel name (e.g., "telegram", "console"). Used in context.
            allow_tools: 是否允許使用工具。
                         Whether to allow tool execution.
        
        Returns:
            ExecutionResult：可見文字與本輪工具執行統計（供背景 skill 複盤觸發）。
        
        Raises:
            RuntimeError: 如果工具執行失敗或超過最大迭代次數。
                          If tool execution fails or exceeds max iterations.
        """
        return await self.llm_calls.call_llm(
            session_id,
            current_message,
            channel=channel,
            allow_tools=allow_tools,
            user_images=user_images,
            user_image_files=user_image_files,
            user_audio_files=user_audio_files,
            user_video_files=user_video_files,
            external_chat_id=external_chat_id,
            emit_tool_progress=emit_tool_progress,
            task_intent=task_intent,
        )

    def _skill_review_tool_registry(self) -> ToolRegistry | None:
        """Tools allowed during background skill review (subset of main registry)."""
        return self.skill_review.tool_registry()

    def _should_schedule_skill_review(self, result: ExecutionResult) -> bool:
        """Return whether the current turn warrants a background skill review."""
        if not self.config.skill_review_enabled:
            return False
        if result.used_configure_skill:
            return False
        if result.executed_tool_calls < self.config.skill_review_min_tool_calls:
            return False
        return self._skill_review_tool_registry() is not None

    def _maybe_schedule_skill_review(self, session_id: str, result: ExecutionResult) -> None:
        """Fire-and-forget background pass after a heavy tool turn without skill upsert."""
        if self.curator is None:
            return
        self.curator.schedule_skill_review(
            session_id,
            result,
            run_id=self.turn_context.current_run_id(),
            channel=self.turn_context.current_channel(),
            external_chat_id=self.turn_context.current_external_chat_id(),
        )

    async def get_curator_status(self, session_id: str) -> dict[str, Any] | None:
        """Return background curator runtime status for one session."""
        if self.curator is None:
            return None
        return self.curator.status(session_id)

    async def get_curator_history(self, session_id: str, *, limit: int = 10) -> list[dict[str, Any]] | None:
        """Return recent persisted curator run history for one session."""
        if self.curator is None:
            return None
        return self.curator.history(session_id, limit=limit)

    async def run_curator_now(
        self,
        session_id: str,
        *,
        scope: str | None = None,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Schedule a manual full curator pass for one session."""
        if self.curator is None:
            return None
        latest_run = await self.storage.get_latest_run(session_id)
        scheduled = self.curator.schedule_manual_run(
            session_id=session_id,
            run_id=latest_run.run_id if latest_run is not None else None,
            scope=scope,
            channel=channel,
            external_chat_id=external_chat_id,
        )
        status = dict(self.curator.status(session_id))
        status["scheduled"] = scheduled
        return status

    async def pause_curator(self, session_id: str) -> dict[str, Any] | None:
        """Pause future curator scheduling for one session."""
        if self.curator is None:
            return None
        return self.curator.pause(session_id)

    async def resume_curator(self, session_id: str) -> dict[str, Any] | None:
        """Resume future curator scheduling for one session."""
        if self.curator is None:
            return None
        return self.curator.resume(session_id)

    def _record_learning(
        self,
        session_id: str,
        *,
        kind: str,
        target_id: str,
        summary: str,
        source_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist one learned artifact into the session learning ledger."""
        if self.learning_ledger is None:
            return
        self.learning_ledger.record_learning(
            session_id,
            kind=kind,
            target_id=target_id,
            summary=summary,
            source_run_id=source_run_id,
            metadata=metadata,
        )

    def _skill_description(self, skill_name: str, session_id: str) -> str:
        """Return the best available description for one skill in the current session scope."""
        skills_loader = getattr(self._context_builder, "skills_loader", None)
        session_skills_dir_resolver = getattr(self._context_builder, "get_session_skills_dir", None)
        if skills_loader is None or not callable(session_skills_dir_resolver):
            return ""
        try:
            session_skills_dir = session_skills_dir_resolver(session_id)
            for skill in skills_loader.get_skills(session_skills_dir):
                if skill.name == skill_name:
                    return str(skill.description or "").strip()
        except Exception:
            logger.exception("[%s] learning.skill-metadata.failed | skill=%s", session_id, skill_name)
        return ""

    def _finalize_learning_reuse(self, session_id: str, run_id: str, success: bool) -> None:
        """Mark any skills read during one run as reused in the learning ledger."""
        skill_names = sorted(self._run_skill_reads.pop(run_id, set()))
        if not skill_names or self.learning_ledger is None:
            return
        outcome = "success" if success else "failed"
        for skill_name in skill_names:
            description = self._skill_description(skill_name, session_id)
            summary = description or f"Skill '{skill_name}' was reused by the agent."
            metadata = {"source": "read_skill", "skill_name": skill_name}
            if description:
                metadata["description"] = description
            self.learning_ledger.mark_used(
                session_id,
                kind="skill",
                target_id=skill_name,
                outcome=outcome,
                summary=summary,
                source_run_id=run_id,
                metadata=metadata,
            )

    async def _run_skill_review(self, session_id: str) -> list[dict[str, Any]]:
        tool_registry = self._skill_review_tool_registry()
        if tool_registry is None:
            return []
        token = self._current_session_id.set(session_id)
        try:
            return await self.skill_review.run(session_id, tool_registry=tool_registry)
        finally:
            self._current_session_id.reset(token)

    async def run_subagent(
        self,
        task: str,
        prompt_type: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Run or resume a delegated subagent task through a child storage session."""
        return await self.subagents.run(task, prompt_type=prompt_type, task_id=task_id)

    async def run_subagents_many(
        self,
        tasks: list[dict[str, Any]],
        max_parallel: int | None = None,
    ) -> str:
        """Run multiple read-only or research child tasks concurrently."""
        return await self.subagents.run_many(tasks, max_parallel=max_parallel)

    async def run_workflow(self, workflow: str, task: str, start_step: str | None = None) -> str:
        """Run one fixed multi-step orchestration workflow."""
        return await self.workflows.run_from_step(workflow, task, start_step=start_step)

    async def run_verify(
        self,
        *,
        action: str = "auto",
        path: str = ".",
        pytest_args: tuple[str, ...] = (),
    ) -> ExecutionResult:
        """Run deterministic verification through the registered verify tool."""
        session_id = self._get_current_session_id()
        run_id = self.turn_context.current_run_id()
        if session_id is None or run_id is None:
            return ExecutionResult(content="Error: No active run is available for deterministic verification.", had_tool_error=True)

        tool_args: dict[str, Any] = {
            "action": str(action or "auto").strip() or "auto",
            "path": str(path or ".").strip() or ".",
        }
        if pytest_args:
            tool_args["pytest_args"] = [str(item) for item in pytest_args if str(item).strip()]

        before = self._make_tool_progress_hook(
            channel=self.turn_context.current_channel(),
            external_chat_id=self.turn_context.current_external_chat_id(),
            session_id=session_id,
            run_id=run_id,
            enabled=True,
        )
        after = self._make_tool_result_hook(
            channel=self.turn_context.current_channel(),
            external_chat_id=self.turn_context.current_external_chat_id(),
            session_id=session_id,
            run_id=run_id,
            enabled=True,
        )

        if before is not None:
            await before("verify", tool_args)
        result = await self.tools.execute("verify", tool_args)
        if after is not None:
            await after("verify", tool_args, result)

        verification = classify_verification_result(result)
        return ExecutionResult(
            content=result,
            executed_tool_calls=1,
            had_tool_error=str(result or "").lstrip().startswith("Error:"),
            verification_attempted=bool(verification["attempted"]),
            verification_passed=bool(verification["ok"]),
        )

    async def process(self, user_message: UserMessage) -> AssistantMessage:
        """
        處理使用者訊息的主要入口函式。

        參數：
            user_message: UserMessage 統一格式的訊息

        回傳：
            AssistantMessage: 統一格式的回覆
        """
        turn = self.turn_inputs.prepare(user_message)
        return await self.turn_runner.run_user_turn(
            user_message=user_message,
            turn=turn,
            llm_configured=self.llm_configured,
        )

    async def _maybe_consolidate_memory(self, session_id: str) -> None:
        """
        檢查是否需要進行記憶整合並執行。
        
        Check if memory consolidation is needed and run it.
        
        當訊息數量超過閾值時，將未整合的訊息整合到長期記憶中。
        Consolidates unconsolidated messages into long-term memory when
        the message count exceeds the threshold.
        
        Args:
            session_id: Internal session ID.
        """
        await self.memory_consolidation.maybe_consolidate(session_id)
        self._maybe_update_user_overlay(session_id)

    async def _maybe_update_user_profile(self, session_id: str) -> None:
        """Check whether this session's USER.md should be refreshed."""
        await self.user_profile_update.maybe_update(session_id)
        self._maybe_update_user_overlay(session_id)

    async def _maybe_update_active_task(self, session_id: str) -> None:
        """Check whether this session's ACTIVE_TASK.md should be refreshed."""
        await self.active_task_update.maybe_update(session_id)

    async def _maybe_apply_immediate_task_transition(
        self,
        session_id: str,
        response_text: str,
        exec_result: ExecutionResult,
    ) -> None:
        """Apply conservative immediate task-state transitions before background maintenance runs."""
        await self.active_task_commands.apply_immediate_transition(
            session_id,
            response_text,
            had_tool_error=exec_result.had_tool_error,
        )

    async def _maybe_apply_completion_gate_result(
        self,
        session_id: str,
        result: CompletionGateResult,
    ) -> None:
        """Apply completion-gate task-state updates when safe."""
        await self.active_task_commands.apply_completion_gate_result(session_id, result)

    async def _maybe_apply_work_progress(self, session_id: str, progress: WorkProgressUpdate, state) -> None:
        """Apply final structured work progress hints to ACTIVE_TASK when useful."""
        await self.active_task_commands.apply_work_progress(session_id, progress, state)

    async def _get_work_state(self, session_id: str):
        """Return persisted structured work state when supported by storage."""
        return await get_storage_work_state(self.storage, session_id)

    async def _save_work_state(self, state) -> None:
        """Persist structured work state when supported by storage."""
        if state is None:
            return
        await upsert_storage_work_state(self.storage, state)

    async def _clear_work_state(self, session_id: str) -> None:
        """Remove persisted structured work state when supported by storage."""
        await clear_storage_work_state(self.storage, session_id)

    async def _get_work_state_summary(self, session_id: str) -> str:
        """Render the current persisted work state into a compact summary string."""
        state = await self._get_work_state(session_id)
        return self.work_progress.render_state_summary(state)

    def _is_run_cancel_requested(self, session_id: str, run_id: str | None) -> bool:
        """Return whether cooperative cancellation was requested for the current run."""
        if run_id is None:
            return False
        return self.run_state.is_cancel_requested(session_id, run_id)

    def get_active_run(self, session_id: str):
        """Return the active run state for one session, if any."""
        return self.run_state.get_active(session_id)

    async def request_run_cancel(
        self,
        session_id: str,
        run_id: str,
        *,
        channel: str | None = None,
        external_chat_id: str | None = None,
    ) -> bool:
        """Request cooperative cancellation for one active run."""
        current = self.run_state.get_active(session_id)
        already_requested = bool(current is not None and current.run_id == run_id and current.cancel_requested)
        active = self.run_state.request_cancel(session_id, run_id)
        if active is None:
            return False
        if already_requested:
            return True
        killed_sessions = await self._cancel_owned_background_sessions(session_id, run_id)
        await self._emit_run_event(
            session_id,
            run_id,
            "run_cancel_requested",
            {
                "status": "cancelling",
                "owned_background_sessions_cancelled": len(killed_sessions),
                "owned_background_session_ids": [session.session_id for session in killed_sessions],
            },
            channel=channel,
            external_chat_id=external_chat_id,
        )
        return True

    async def _maybe_update_recent_summary(self, session_id: str) -> None:
        """Check whether RECENT_SUMMARY.md should be refreshed."""
        await self.recent_summary_update.maybe_update(session_id)

    def _read_memory_snapshot(self, session_id: str) -> str:
        """Return the current memory text used for curator change detection."""
        return self.memory.read(session_id)

    def _read_recent_summary_snapshot(self, session_id: str) -> str:
        """Return the current recent-summary text used for curator change detection."""
        memory_dir = getattr(self._context_builder, "memory_dir", None)
        if memory_dir is None:
            return ""
        return RecentSummaryStore(memory_dir, app_home=self.app_home, workspace_root=self.tool_workspace).read(session_id)

    def _read_user_profile_snapshot(self, session_id: str) -> str:
        """Return the managed USER.md profile block used for curator change detection."""
        if self.app_home is None:
            return ""
        bootstrap_dir = getattr(self._context_builder, "bootstrap_dir", None)
        store = create_user_profile_store(
            self.app_home,
            session_id,
            bootstrap_dir=bootstrap_dir,
            workspace_root=self.tool_workspace,
        )
        return store.read_managed_block()

    def _read_active_task_snapshot(self, session_id: str) -> str:
        """Return the ACTIVE_TASK.md managed block used for curator change detection."""
        if self.app_home is None:
            return ""
        return create_active_task_store(
            self.app_home,
            session_id,
            workspace_root=self.tool_workspace,
        ).read_managed_block()

    def _read_skill_snapshot(self, session_id: str) -> str:
        """Return the mutable session skill fingerprint used for curator change detection."""
        workspace = self.tool_workspace or getattr(self._context_builder, "workspace", None)
        if workspace is None:
            return ""
        return fingerprint_text_directory(get_session_skills_dir(session_id, workspace_root=workspace))

    def _clear_active_task(self, session_id: str) -> None:
        """Reset ACTIVE_TASK.md for one session."""
        self.active_task_commands.clear(session_id)

    def _get_active_task_store(self, session_id: str):
        return self.active_task_commands.get_store(session_id)

    async def show_active_task(self, session_id: str) -> str | None:
        """Return the current ACTIVE_TASK block for user display, if any."""
        return await self.active_task_commands.show(session_id)

    async def show_active_task_full(self, session_id: str) -> str | None:
        """Return the full ACTIVE_TASK block for user display, if any."""
        return await self.active_task_commands.show_full(session_id)

    async def show_active_task_history(self, session_id: str, *, limit: int = 10) -> str | None:
        """Return recent ACTIVE_TASK events for user display, if any."""
        return await self.active_task_commands.show_history(session_id, limit=limit)

    async def set_active_task_from_text(self, session_id: str, task_text: str) -> str | None:
        """Create or replace the current ACTIVE_TASK from explicit user text."""
        return await self.active_task_commands.set_from_text(session_id, task_text)

    async def activate_active_task(self, session_id: str) -> str | None:
        """Mark the current ACTIVE_TASK as active again."""
        return await self.active_task_commands.activate(session_id)

    async def reopen_active_task(self, session_id: str) -> str | None:
        """Reopen a terminal ACTIVE_TASK and resume it as active."""
        return await self.active_task_commands.reopen(session_id)

    async def block_active_task(self, session_id: str, reason: str) -> str | None:
        """Mark the current ACTIVE_TASK as blocked with one explicit reason."""
        return await self.active_task_commands.block(session_id, reason)

    async def wait_on_active_task(self, session_id: str, question: str) -> str | None:
        """Mark the current ACTIVE_TASK as waiting for user input."""
        return await self.active_task_commands.wait_on(session_id, question)

    async def set_active_task_current_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the current step for the active task."""
        return await self.active_task_commands.set_current_step(session_id, step_text)

    async def set_active_task_next_step(self, session_id: str, step_text: str) -> str | None:
        """Replace the planned next step for the active task."""
        return await self.active_task_commands.set_next_step(session_id, step_text)

    async def advance_active_task(self, session_id: str) -> str | None:
        """Promote the next step into the current step and mark the previous step complete."""
        return await self.active_task_commands.advance(session_id)

    async def complete_active_task_step(self, session_id: str, next_step_override: str | None = None) -> str | None:
        """Complete the current step and either advance or finish the task."""
        return await self.active_task_commands.complete_step(session_id, next_step_override=next_step_override)

    async def mark_active_task_status(self, session_id: str, status: str) -> str | None:
        """Set the current ACTIVE_TASK status when one exists."""
        return await self.active_task_commands.mark_status(session_id, status)

    async def reset_active_task(self, session_id: str) -> None:
        """Clear the current ACTIVE_TASK state for one session."""
        await self.active_task_commands.reset(session_id)
        await self._clear_work_state(session_id)

    async def reset_history(self, session_id: str | None = None) -> None:
        """
        清除對話歷史。
        
        Clear conversation history.
        
        Args:
            session_id: Internal session ID. If None, clears all sessions.
        """
        await self.history_reset.reset(session_id)
