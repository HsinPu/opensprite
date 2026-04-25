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

import asyncio
import base64
import binascii
from contextvars import ContextVar
from contextlib import AsyncExitStack
import json
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..bus.events import OutboundMessage, RunEvent
from ..bus.message import UserMessage, AssistantMessage
from ..llms import LLMProvider, ChatMessage
from ..storage import StorageProvider, StoredMessage
from ..storage.base import get_storage_message_count
from ..documents.active_task import ActiveTaskConsolidator, _extract_task_field, build_initial_active_task_block, build_task_block_from_text, create_active_task_store, infer_immediate_task_transition, should_replace_active_task
from ..context.builder import ContextBuilder
from ..documents.memory import MemoryStore
from ..context.paths import get_chat_workspace, get_recent_summary_state_file
from ..documents.recent_summary import RecentSummaryConsolidator, RecentSummaryStore
from ..media import MediaRouter
from ..documents.user_profile import UserProfileConsolidator, create_user_profile_store
from ..search.base import SearchStore
from ..subagent_session import (
    build_child_subagent_chat_id,
    extract_subagent_prompt_type,
    new_subagent_task_id,
    validate_subagent_task_id,
)
from ..tools import ToolRegistry
from ..tools.process_runtime import BackgroundSession
from ..tools.shell_runtime import format_captured_output
from ..utils import count_messages_tokens, count_text_tokens, sanitize_assistant_visible_text, strip_assistant_internal_scaffolding
from ..utils.log import logger
from ..config import AgentConfig, MemoryConfig, ToolsConfig, LogConfig, SearchConfig, UserProfileConfig, ActiveTaskConfig, RecentSummaryConfig, MessagesConfig, Config
from .consolidation import MemoryConsolidationService, RecentSummaryUpdateService, UserProfileUpdateService, ActiveTaskUpdateService
from .execution import ExecutionEngine, ExecutionResult
from .skill_review import (
    SKILL_REVIEW_SYSTEM,
    build_skill_review_user_content,
    format_stored_messages_for_transcript,
)
from .subagent_policy import build_subagent_tool_registry, profile_for_subagent
from .tool_registration import register_default_tools, register_memory_tool


LOG_WHITESPACE_RE = re.compile(r"\s+")


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
    MCP_INITIAL_RETRY_BACKOFF_SECONDS = 15.0
    MCP_MAX_RETRY_BACKOFF_SECONDS = 300.0
    OUTBOUND_MEDIA_KEYS = {
        "image": "images",
        "voice": "voices",
        "audio": "audios",
        "video": "videos",
    }
    INBOUND_IMAGE_EXTENSIONS = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    INBOUND_AUDIO_EXTENSIONS = {
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/webm": "webm",
        "audio/mp4": "m4a",
    }
    INBOUND_VIDEO_EXTENSIONS = {
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
        "video/x-matroska": "mkv",
    }

    @staticmethod
    def _sanitize_log_filename(value: str) -> str:
        """Sanitize a string for use in per-prompt log filenames."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
        return cleaned[:80] or "prompt"

    def _get_system_prompt_log_path(self, log_id: str) -> Path:
        """Return a unique file path for one full system prompt log entry."""
        logs_root = (self.app_home or Path.home() / ".opensprite") / "logs" / "system-prompts"
        if ":subagent:" in log_id:
            logs_root = logs_root / "subagents"
        dated_root = logs_root / time.strftime("%Y-%m-%d")
        dated_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%H-%M-%S")
        suffix = str(time.time_ns())[-6:]
        safe_log_id = self._sanitize_log_filename(log_id)
        filename = f"{timestamp}_{safe_log_id}_{suffix}.md"
        return dated_root / filename

    def _write_full_system_prompt_log(self, log_id: str, content: str) -> None:
        """Write the full system prompt to a dedicated per-prompt log file."""
        try:
            log_path = self._get_system_prompt_log_path(log_id)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            entry = (
                f"[{timestamp}] [{log_id}] prompt.system.begin\n"
                f"{content}\n"
                f"[{timestamp}] [{log_id}] prompt.system.end\n"
            )
            with log_path.open("w", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"[{log_id}] prompt.file.error | error={e}")

    @staticmethod
    def _sanitize_response_content(content: str) -> str:
        """Remove provider-internal control blocks from visible replies."""
        return sanitize_assistant_visible_text(content)

    @staticmethod
    def _format_log_preview(content: str | list[dict[str, Any]] | None, max_chars: int = 160) -> str:
        """Build a compact, single-line preview for logs."""
        if isinstance(content, list):
            text_parts: list[str] = []
            image_count = 0
            other_items = 0
            for item in content:
                if not isinstance(item, dict):
                    other_items += 1
                    continue
                item_type = item.get("type")
                if item_type == "text":
                    text_parts.append(str(item.get("text", "")))
                elif item_type == "image_url":
                    image_count += 1
                else:
                    other_items += 1

            text = " ".join(part for part in text_parts if part)
            text = strip_assistant_internal_scaffolding(text)
            text = LOG_WHITESPACE_RE.sub(" ", text).strip() or "<multimodal>"
            suffix_parts = []
            if image_count:
                suffix_parts.append(f"images={image_count}")
            if other_items:
                suffix_parts.append(f"items={other_items}")
            if suffix_parts:
                text = f"{text} [{' '.join(suffix_parts)}]"
        else:
            text = strip_assistant_internal_scaffolding(str(content or ""))
            text = LOG_WHITESPACE_RE.sub(" ", text).strip()

        if not text:
            return "<empty>"
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    @staticmethod
    def _json_safe_event_value(value: Any) -> Any:
        """Convert run event payload values into JSON-safe shapes."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): AgentLoop._json_safe_event_value(val) for key, val in value.items()}
        if isinstance(value, (list, tuple)):
            return [AgentLoop._json_safe_event_value(item) for item in value]
        return str(value)

    @classmethod
    def _json_safe_event_payload(cls, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Return a JSON-serializable event payload dictionary."""
        if not payload:
            return {}
        return {
            str(key): cls._json_safe_event_value(value)
            for key, value in payload.items()
        }

    @staticmethod
    def _summarize_messages(messages: list[ChatMessage], tail: int = 4) -> str:
        """Build a compact summary of the trailing chat messages for diagnostics."""
        summary = []
        for msg in messages[-tail:]:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content_kind = f"list[{len(content)}]"
            else:
                content_kind = f"str[{len(content or '')}]"
            summary.append(
                f"{getattr(msg, 'role', '?')}({content_kind},tool_id={'y' if getattr(msg, 'tool_call_id', None) else 'n'},tool_calls={len(getattr(msg, 'tool_calls', None) or [])})"
            )
        return ", ".join(summary) if summary else "<empty>"

    @staticmethod
    def _extract_available_subagents(system_prompt: str) -> list[str]:
        """Parse the Available Subagents section from a rendered system prompt."""
        in_section = False
        subagents: list[str] = []

        for raw_line in system_prompt.splitlines():
            line = raw_line.strip()
            if not in_section:
                if line in {"# Available Subagents", "## Available Subagents"}:
                    in_section = True
                continue

            if not line:
                continue
            if line == "---" or line.startswith("#"):
                break
            if not line.startswith("- `"):
                continue

            end_tick = line.find("`", 3)
            if end_tick <= 3:
                continue
            subagents.append(line[3:end_tick])

        return subagents

    @staticmethod
    def _tool_warrants_progress_notice(tool_name: str) -> bool:
        """Whether to send a short interim message before this tool runs (main agent only)."""
        if tool_name in {"read_skill", "delegate"}:
            return True
        return tool_name.startswith("mcp_")

    @staticmethod
    def _format_tool_progress_message(tool_name: str, tool_args: dict[str, Any]) -> str:
        """User-facing one-line status for skill / subagent / MCP tool execution."""
        args = tool_args or {}
        if tool_name == "read_skill":
            name = args.get("skill_name") or "?"
            return f"正在讀取技能〈{name}〉…"
        if tool_name == "delegate":
            task_id = args.get("task_id")
            ptype = args.get("prompt_type") or "writer"
            if task_id:
                return f"正在續跑子代理任務（{task_id}）…"
            return f"正在委派子代理（{ptype}）…"
        if tool_name.startswith("mcp_"):
            tail = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
            return f"正在呼叫 MCP：{tail}…"
        return "處理中…"

    def _make_tool_progress_hook(
        self,
        *,
        channel: str | None,
        transport_chat_id: str | None,
        session_chat_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any]], Awaitable[None]] | None:
        """Publish run telemetry and a brief outbound status before selected tools run."""
        if not enabled or run_id is None:
            return None
        bus = self._message_bus
        ch = channel
        tid = str(transport_chat_id) if transport_chat_id is not None else None
        sid = session_chat_id
        rid = run_id

        async def _hook(tool_name: str, tool_args: dict[str, Any]) -> None:
            await self._emit_run_event(
                sid,
                rid,
                "tool_started",
                {
                    "tool_name": tool_name,
                    "args_preview": self._format_log_preview(json.dumps(tool_args or {}, ensure_ascii=False), max_chars=240),
                },
                channel=ch,
                transport_chat_id=tid,
            )
            if tool_name == "verify":
                await self._emit_run_event(
                    sid,
                    rid,
                    "verification_started",
                    {
                        "action": (tool_args or {}).get("action", "auto"),
                        "path": (tool_args or {}).get("path", "."),
                    },
                    channel=ch,
                    transport_chat_id=tid,
                )
            if bus is None or not ch or tid is None or not AgentLoop._tool_warrants_progress_notice(tool_name):
                return
            text = AgentLoop._format_tool_progress_message(tool_name, tool_args)
            await bus.publish_outbound(
                OutboundMessage(
                    channel=ch,
                    chat_id=tid,
                    session_chat_id=sid,
                    content=text,
                    metadata={"interim": True, "kind": "tool_progress", "tool_name": tool_name},
                )
            )

        return _hook

    def _make_tool_result_hook(
        self,
        *,
        channel: str | None,
        transport_chat_id: str | None,
        session_chat_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any], str], Awaitable[None]] | None:
        """Publish structured run telemetry after a tool finishes."""
        if not enabled or run_id is None:
            return None
        tid = str(transport_chat_id) if transport_chat_id is not None else None
        rid = run_id

        async def _hook(tool_name: str, tool_args: dict[str, Any], result: str) -> None:
            await self._emit_run_event(
                session_chat_id,
                rid,
                "tool_result",
                {
                    "tool_name": tool_name,
                    "ok": not str(result or "").lstrip().startswith("Error:"),
                    "result_len": len(result or ""),
                    "result_preview": self._format_log_preview(result, max_chars=240),
                },
                channel=channel,
                transport_chat_id=tid,
            )
            if tool_name == "verify":
                await self._emit_run_event(
                    session_chat_id,
                    rid,
                    "verification_result",
                    {
                        "action": (tool_args or {}).get("action", "auto"),
                        "path": (tool_args or {}).get("path", "."),
                        "ok": not str(result or "").lstrip().startswith("Error:"),
                        "result_preview": self._format_log_preview(result, max_chars=240),
                    },
                    channel=channel,
                    transport_chat_id=tid,
                )

        return _hook

    def _make_llm_status_hook(
        self,
        *,
        channel: str | None,
        transport_chat_id: str | None,
        session_chat_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str], Awaitable[None]] | None:
        """在 LLM 長時間等待或重試前，對使用者發送短暫狀態（與工具進度相同走 MessageBus）。"""
        if not enabled or run_id is None:
            return None
        bus = self._message_bus
        ch = channel
        tid = str(transport_chat_id) if transport_chat_id is not None else None
        sid = session_chat_id
        rid = run_id

        async def _hook(text: str) -> None:
            await self._emit_run_event(
                sid,
                rid,
                "llm_status",
                {"message": text},
                channel=ch,
                transport_chat_id=tid,
            )
            if bus is None or not ch or tid is None:
                return
            await bus.publish_outbound(
                OutboundMessage(
                    channel=ch,
                    chat_id=tid,
                    session_chat_id=sid,
                    content=text,
                    metadata={"interim": True, "kind": "llm_wait"},
                )
            )

        return _hook

    async def _create_run(
        self,
        chat_id: str,
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
            await creator(chat_id, run_id, status=status, metadata=metadata)
        except Exception as e:
            logger.warning("[{}] run.create.failed | run_id={} error={}", chat_id, run_id, e)

    async def _update_run_status(
        self,
        chat_id: str,
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
            await updater(chat_id, run_id, status, metadata=metadata, finished_at=finished_at)
        except Exception as e:
            logger.warning("[{}] run.update.failed | run_id={} status={} error={}", chat_id, run_id, status, e)

    async def _emit_run_event(
        self,
        chat_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        transport_chat_id: str | None = None,
    ) -> None:
        """Persist and publish one structured run event."""
        created_at = time.time()
        safe_payload = self._json_safe_event_payload(payload)
        add_event = getattr(self.storage, "add_run_event", None)
        if callable(add_event):
            try:
                await add_event(chat_id, run_id, event_type, payload=safe_payload, created_at=created_at)
            except Exception as e:
                logger.warning("[{}] run.event.persist.failed | run_id={} type={} error={}", chat_id, run_id, event_type, e)

        if self._message_bus is None or not channel or transport_chat_id is None:
            return
        try:
            await self._message_bus.publish_run_event(
                RunEvent(
                    channel=channel,
                    chat_id=str(transport_chat_id),
                    session_chat_id=chat_id,
                    run_id=run_id,
                    event_type=event_type,
                    payload=safe_payload,
                    created_at=created_at,
                )
            )
        except Exception as e:
            logger.warning("[{}] run.event.publish.failed | run_id={} type={} error={}", chat_id, run_id, event_type, e)

    @staticmethod
    def _format_background_session_exit_message(session: BackgroundSession) -> str:
        """Render a concise outbound notice when a managed background session exits."""
        output_tail = format_captured_output(
            session.output_chunks,
            max_chars=1200,
        )
        runtime_seconds = max(
            0.0,
            (session.finished_at or time.monotonic()) - session.started_at,
        )
        return "\n".join(
            [
                "Background session finished.",
                f"Session ID: {session.session_id}",
                f"Termination: {session.termination_reason or 'exit'}",
                f"Exit code: {session.exit_code}",
                f"Runtime: {runtime_seconds:.2f}s",
                "Output tail:",
                output_tail,
            ]
        )

    def _make_background_session_exit_notifier(self) -> Callable[[BackgroundSession], Awaitable[None]] | None:
        """Build an outbound notifier for managed background session completion."""
        channel = self._current_channel.get()
        transport_chat_id = self._current_transport_chat_id.get()
        session_chat_id = self._get_current_chat_id()
        if (
            not self._message_bus
            or not channel
            or transport_chat_id is None
            or session_chat_id is None
        ):
            return None

        bus = self._message_bus
        ch = channel
        tid = str(transport_chat_id)
        sid = session_chat_id

        async def _notify(session: BackgroundSession) -> None:
            content = AgentLoop._format_background_session_exit_message(session)
            metadata = {
                "channel": ch,
                "transport_chat_id": tid,
                "kind": "background_session_exit",
                "session_id": session.session_id,
                "termination_reason": session.termination_reason or "exit",
                "exit_code": session.exit_code,
            }
            await self._save_message(sid, "assistant", content, metadata=metadata)
            await bus.publish_outbound(
                OutboundMessage(
                    channel=ch,
                    chat_id=tid,
                    session_chat_id=sid,
                    content=content,
                    metadata=metadata,
                )
            )

        return _notify

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
        self._current_chat_id: ContextVar[str | None] = ContextVar("current_chat_id", default=None)
        self._current_channel: ContextVar[str | None] = ContextVar("current_channel", default=None)
        self._current_transport_chat_id: ContextVar[str | None] = ContextVar(
            "current_transport_chat_id", default=None
        )
        self._current_images: ContextVar[list[str] | None] = ContextVar("current_images", default=None)
        self._current_audios: ContextVar[list[str] | None] = ContextVar("current_audios", default=None)
        self._current_videos: ContextVar[list[str] | None] = ContextVar("current_videos", default=None)
        self._current_outbound_media: ContextVar[dict[str, list[str]] | None] = ContextVar(
            "current_outbound_media",
            default=None,
        )
        self._current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
        self.app_home: Path | None = None
        self.tool_workspace: Path | None = None
        self.config_path: Path | None = Path(config_path).expanduser().resolve() if config_path is not None else None
        self._mcp_servers = dict(self.tools_config.mcp_servers)
        self._mcp_tool_names: set[str] = set()
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._mcp_connect_lock = asyncio.Lock()
        self._mcp_connect_failures = 0
        self._mcp_retry_after = 0.0
        self._skill_review_tasks: dict[str, asyncio.Task] = {}
        self._skill_review_rerun: set[str] = set()
        self._maintenance_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._maintenance_rerun: set[tuple[str, str]] = set()
        # Set by runtime after MessageQueue is created; used for interim tool progress outbound messages.
        self._message_bus: Any = None

        self.storage = self._setup_storage(storage)
        self._context_builder = self._setup_context_builder(context_builder)
        self.tools = self._setup_tools(tools)
        self.memory = self._setup_memory_store()
        self.memory_consolidation = self._setup_memory_consolidation()
        self._register_memory_tool()
        self.execution_engine = self._setup_execution_engine()
        self.user_profile_update = self._setup_user_profile_update()
        self.active_task_update = self._setup_active_task_update()
        self.recent_summary_update = self._setup_recent_summary_update()

    def _trim_history_to_token_budget(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        channel: str | None,
        chat_id: str,
        tool_schema_tokens: int = 0,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Trim oldest history messages when the prompt would exceed the history token budget."""
        budget = self._effective_context_token_budget()
        base_messages = self._context_builder.build_messages(
            history=[],
            current_message=current_message,
            current_images=None,
            channel=channel,
            chat_id=chat_id,
        )
        base_tokens = count_messages_tokens(base_messages, model=self.provider.get_default_model()) + tool_schema_tokens
        if budget <= 0 or not history:
            history_tokens = count_messages_tokens(history, model=self.provider.get_default_model()) if history else 0
            return history, base_tokens, history_tokens, base_tokens + history_tokens

        if base_tokens >= budget:
            logger.warning(
                f"[{chat_id}] prompt.trim | base_tokens={base_tokens} budget={budget} history_retained=0 reason=base-exceeds-budget"
            )
            return [], base_tokens, 0, base_tokens

        trimmed_reversed: list[dict[str, Any]] = []
        running_tokens = base_tokens
        retained_history_tokens = 0
        for message in reversed(history):
            message_tokens = count_messages_tokens([message], model=self.provider.get_default_model())
            if trimmed_reversed and running_tokens + message_tokens > budget:
                break
            if not trimmed_reversed and running_tokens + message_tokens > budget:
                logger.warning(
                    f"[{chat_id}] prompt.trim | base_tokens={base_tokens} first_history_tokens={message_tokens} budget={budget} history_retained=0 reason=first-message-exceeds-budget"
                )
                return [], base_tokens, 0, base_tokens
            trimmed_reversed.append(message)
            running_tokens += message_tokens
            retained_history_tokens += message_tokens

        trimmed_history = list(reversed(trimmed_reversed))
        if len(trimmed_history) != len(history):
            logger.info(
                f"[{chat_id}] prompt.trim | budget={budget} base_tokens={base_tokens} history_before={len(history)} history_after={len(trimmed_history)} estimated_tokens={running_tokens}"
            )
        return trimmed_history, base_tokens, retained_history_tokens, running_tokens

    def _effective_context_token_budget(self) -> int:
        """Return the prompt token budget after applying model window and output reserve."""
        history_budget = max(0, self.config.history_token_budget)
        if self.llm_context_window_tokens is None:
            return history_budget

        output_reserve = max(0, self.llm_chat_max_tokens)
        model_input_budget = max(1, self.llm_context_window_tokens - output_reserve)
        if history_budget <= 0:
            return model_input_budget
        return min(history_budget, model_input_budget)

    def _estimate_tool_schema_tokens(self, *, allow_tools: bool, tool_registry: ToolRegistry | None = None) -> int:
        """Estimate token cost of tool schemas sent with the request."""
        if not allow_tools:
            return 0

        active_tools = tool_registry or self.tools
        if not active_tools.tool_names:
            return 0

        try:
            tool_schema_text = json.dumps(active_tools.get_definitions(), ensure_ascii=False, sort_keys=True)
        except Exception:
            return 0

        return count_text_tokens(tool_schema_text, model=self.provider.get_default_model())

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
        return MemoryStore(memory_dir)

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
                profile_store_factory=lambda chat_id: create_user_profile_store(
                    self.app_home,
                    chat_id,
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

        summary_store = RecentSummaryStore(memory_dir)
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
            active_task_store_factory=lambda chat_id: create_active_task_store(
                self.app_home,
                chat_id,
                workspace_root=self.tool_workspace,
            ),
            threshold=self.active_task_config.threshold,
            lookback_messages=self.active_task_config.lookback_messages,
            enabled=self.active_task_config.enabled,
            llm=self.active_task_config.llm,
        )
        return ActiveTaskUpdateService(consolidator)

    def _clear_recent_summary(self, chat_id: str) -> None:
        memory_dir = getattr(self._context_builder, "memory_dir", None)
        if memory_dir is None:
            return
        RecentSummaryStore(memory_dir, get_recent_summary_state_file(memory_dir)).clear(chat_id)

    def _register_memory_tool(self) -> None:
        """Register the save_memory tool."""
        register_memory_tool(self.tools, self.memory, self._get_current_chat_id)

    def _sync_runtime_mcp_tools_context(self) -> None:
        """Expose connected MCP tools to context builders that support prompt summaries."""
        if not hasattr(self._context_builder, "set_runtime_mcp_tools"):
            return

        mcp_tools = sorted(
            [
                (tool.name, tool.description)
                for tool_name in self.tools.tool_names
                for tool in [self.tools.get(tool_name)]
                if tool is not None and tool.name.startswith("mcp_")
            ],
            key=lambda item: item[0],
        )
        self._context_builder.set_runtime_mcp_tools(mcp_tools)

    def _get_config_path(self) -> Path | None:
        if self.config_path is not None:
            return self.config_path
        if self.app_home is not None:
            return (self.app_home / "opensprite.json").resolve()
        return None

    async def connect_mcp(self) -> None:
        """Connect configured MCP servers once and register their tools."""
        now = time.monotonic()
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers or now < self._mcp_retry_after:
            return

        async with self._mcp_connect_lock:
            now = time.monotonic()
            if self._mcp_connected or self._mcp_connecting or not self._mcp_servers or now < self._mcp_retry_after:
                return

            self._mcp_connecting = True
            stack: AsyncExitStack | None = None
            preexisting_tool_names = set(self.tools.tool_names)
            try:
                from ..tools.mcp import connect_mcp_servers

                stack = AsyncExitStack()
                await stack.__aenter__()
                await connect_mcp_servers(self._mcp_servers, self.tools, stack)
                self._mcp_stack = stack
                self._mcp_connected = True
                self._mcp_connect_failures = 0
                self._mcp_retry_after = 0.0
                self._mcp_tool_names = {
                    name for name in self.tools.tool_names
                    if name.startswith("mcp_") and name not in preexisting_tool_names
                }
                self._sync_runtime_mcp_tools_context()
                logger.info("agent.mcp.connected | tools={}", ", ".join(self.tools.tool_names))
            except BaseException as exc:
                for name in list(self.tools.tool_names):
                    if name.startswith("mcp_") and name not in preexisting_tool_names:
                        self.tools.unregister(name)
                self._mcp_connected = False
                self._mcp_tool_names.clear()
                self._mcp_connect_failures += 1
                retry_delay = min(
                    self.MCP_INITIAL_RETRY_BACKOFF_SECONDS * (2 ** (self._mcp_connect_failures - 1)),
                    self.MCP_MAX_RETRY_BACKOFF_SECONDS,
                )
                self._mcp_retry_after = time.monotonic() + retry_delay
                logger.error(
                    "agent.mcp.connect.error | error={} retry_in_s={} failures={}",
                    exc,
                    retry_delay,
                    self._mcp_connect_failures,
                )
                if stack is not None:
                    try:
                        await stack.aclose()
                    except Exception:
                        pass
                self._mcp_stack = None
            finally:
                self._mcp_connecting = False

    async def close_mcp(self) -> None:
        """Close any active MCP sessions and reset lifecycle flags."""
        async with self._mcp_connect_lock:
            stack = self._mcp_stack
            self._mcp_stack = None
            self._mcp_connected = False
            self._mcp_connecting = False
            for tool_name in list(self._mcp_tool_names):
                self.tools.unregister(tool_name)
            self._mcp_tool_names.clear()
            self._sync_runtime_mcp_tools_context()

        if stack is None:
            return

        try:
            await stack.aclose()
        except Exception as exc:
            logger.warning("agent.mcp.close.error | error={}", exc)

    def _schedule_background_maintenance(
        self,
        *,
        kind: str,
        chat_id: str,
        runner: Callable[[str], Awaitable[None]],
    ) -> None:
        """Run one maintenance path in the background with per-chat coalescing."""
        key = (kind, chat_id)
        existing = self._maintenance_tasks.get(key)
        if existing is not None and not existing.done():
            self._maintenance_rerun.add(key)
            return

        task: asyncio.Task | None = None

        async def _run() -> None:
            try:
                while True:
                    self._maintenance_rerun.discard(key)
                    await runner(chat_id)
                    if key not in self._maintenance_rerun:
                        break
                    logger.info("[{}] maintenance.rerun | kind={}", chat_id, kind)
            except asyncio.CancelledError:
                pass
            finally:
                if task is not None and self._maintenance_tasks.get(key) is task:
                    self._maintenance_tasks.pop(key, None)
                self._maintenance_rerun.discard(key)

        task = asyncio.create_task(_run())
        self._maintenance_tasks[key] = task

    def _schedule_post_response_maintenance(self, chat_id: str) -> None:
        """Queue post-response document maintenance without blocking the reply."""
        self._schedule_background_maintenance(
            kind="memory",
            chat_id=chat_id,
            runner=self._maybe_consolidate_memory,
        )
        self._schedule_background_maintenance(
            kind="recent_summary",
            chat_id=chat_id,
            runner=self._maybe_update_recent_summary,
        )
        self._schedule_background_maintenance(
            kind="user_profile",
            chat_id=chat_id,
            runner=self._maybe_update_user_profile,
        )
        self._schedule_background_maintenance(
            kind="active_task",
            chat_id=chat_id,
            runner=self._maybe_update_active_task,
        )

    async def wait_for_background_maintenance(self) -> None:
        """Wait until all currently scheduled maintenance tasks finish."""
        while True:
            tasks = [task for task in self._maintenance_tasks.values() if not task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close_background_maintenance(self) -> None:
        """Cancel and drain any in-flight maintenance tasks."""
        tasks = [task for task in self._maintenance_tasks.values() if not task.done()]
        self._maintenance_tasks.clear()
        self._maintenance_rerun.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait_for_background_skill_reviews(self) -> None:
        """Wait until all currently scheduled skill review tasks finish."""
        while True:
            tasks = [task for task in self._skill_review_tasks.values() if not task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close_background_skill_reviews(self) -> None:
        """Cancel and drain any in-flight skill review tasks."""
        tasks = [task for task in self._skill_review_tasks.values() if not task.done()]
        self._skill_review_tasks.clear()
        self._skill_review_rerun.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close_background_processes(self) -> None:
        """Terminate managed background exec sessions before the event loop closes."""
        process_tool = self.tools.get("process")
        manager = getattr(process_tool, "manager", None)
        close = getattr(manager, "close", None)
        if close is not None:
            await close()

    async def _maybe_seed_active_task(self, chat_id: str, current_message: str) -> None:
        """Create a minimal ACTIVE_TASK.md before the first heavy turn when no task is active yet."""
        if not self.active_task_config.enabled or self.app_home is None:
            return

        store = create_active_task_store(
            self.app_home,
            chat_id,
            workspace_root=self.tool_workspace,
        )
        current_status = store.read_status()
        replacing = False
        if current_status in {"active", "blocked", "waiting_user"}:
            if not should_replace_active_task(store.read_managed_block(), current_message):
                return
            replacing = True

        initial_task = build_initial_active_task_block(current_message)
        if not initial_task:
            return

        store.write_managed_block(initial_task)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, max(0, message_count - 1))
        compact_message = re.sub(r"\s+", " ", current_message).strip()
        if len(compact_message) > 120:
            compact_message = compact_message[:117].rstrip() + "..."
        store.append_event(
            "seed",
            "immediate",
            details={"replace": replacing, "message": compact_message},
        )
        logger.info("[{}] active_task.seeded | replace={}", chat_id, replacing)

    async def reload_mcp_from_config(self) -> str:
        """Reload MCP settings from disk and reconnect MCP tools for this agent."""
        config_path = self._get_config_path()
        if config_path is None:
            return "Error: MCP config path is unavailable."

        loaded = Config.load(config_path)
        self.tools_config.mcp_servers_file = loaded.tools.mcp_servers_file
        self.tools_config.mcp_servers = dict(loaded.tools.mcp_servers)
        self._mcp_servers = dict(loaded.tools.mcp_servers)
        self._mcp_connect_failures = 0
        self._mcp_retry_after = 0.0

        await self.close_mcp()
        if not self._mcp_servers:
            return "MCP configuration reloaded. No MCP servers are configured now."

        await self.connect_mcp()
        if not self._mcp_connected:
            return "MCP configuration reloaded, but no MCP servers connected successfully."

        connected_tools = ", ".join(sorted(self._mcp_tool_names)) or "(none)"
        return f"MCP configuration reloaded. Connected tools: {connected_tools}"

    def _get_current_chat_id(self) -> str | None:
        """Return the current task-local chat id."""
        return self._current_chat_id.get()

    def _get_current_workspace(self) -> Path:
        """Resolve the current task-local workspace."""
        workspace_root = self.tool_workspace or getattr(self._context_builder, "workspace", Path.cwd())
        chat_id = self._get_current_chat_id() or "default"
        return get_chat_workspace(chat_id, workspace_root=workspace_root)

    @staticmethod
    def _decode_media_data_url(payload: str, media_prefix: str) -> tuple[str, bytes] | None:
        """Decode a media data URL into a MIME type and bytes."""
        value = str(payload or "").strip()
        if not value.startswith("data:"):
            return None

        header, separator, encoded = value.partition(",")
        if not separator or ";base64" not in header.lower():
            return None

        mime_type = header[5:].split(";", 1)[0].strip().lower()
        if not mime_type.startswith(f"{media_prefix}/"):
            return None

        try:
            return mime_type, base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None

    def _persist_inbound_media(
        self,
        chat_id: str,
        media_items: list[str] | None,
        *,
        media_prefix: str,
        directory_name: str,
        extensions: dict[str, str],
    ) -> list[str]:
        """Persist inbound media data URLs under a chat workspace directory."""
        if not media_items:
            return []

        workspace_root = self.tool_workspace or getattr(self._context_builder, "workspace", Path.cwd())
        workspace = get_chat_workspace(chat_id, workspace_root=workspace_root, app_home=self.app_home)
        media_dir = workspace / directory_name
        saved_files: list[str] = []

        for index, item in enumerate(media_items, start=1):
            decoded = self._decode_media_data_url(item, media_prefix)
            if decoded is None:
                logger.warning(
                    "[{}] inbound.{}.persist.skip | index={} reason=unsupported-payload",
                    chat_id,
                    media_prefix,
                    index,
                )
                continue

            mime_type, media_bytes = decoded
            extension = extensions.get(mime_type, "bin")
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                filename = f"inbound-{timestamp}-{time.time_ns()}-{index}.{extension}"
                target = media_dir / filename
                target.write_bytes(media_bytes)
                saved_files.append(target.relative_to(workspace).as_posix())
                logger.info("[{}] inbound.{}.persisted | file={}", chat_id, media_prefix, target)
            except Exception as exc:
                logger.warning("[{}] inbound.{}.persist.failed | index={} error={}", chat_id, media_prefix, index, exc)

        return saved_files

    def _persist_inbound_images(self, chat_id: str, images: list[str] | None) -> list[str]:
        """Persist inbound image data URLs under the chat workspace images directory."""
        return self._persist_inbound_media(
            chat_id,
            images,
            media_prefix="image",
            directory_name="images",
            extensions=self.INBOUND_IMAGE_EXTENSIONS,
        )

    def _persist_inbound_audios(self, chat_id: str, audios: list[str] | None) -> list[str]:
        """Persist inbound audio data URLs under the chat workspace audios directory."""
        return self._persist_inbound_media(
            chat_id,
            audios,
            media_prefix="audio",
            directory_name="audios",
            extensions=self.INBOUND_AUDIO_EXTENSIONS,
        )

    def _persist_inbound_videos(self, chat_id: str, videos: list[str] | None) -> list[str]:
        """Persist inbound video data URLs under the chat workspace videos directory."""
        return self._persist_inbound_media(
            chat_id,
            videos,
            media_prefix="video",
            directory_name="videos",
            extensions=self.INBOUND_VIDEO_EXTENSIONS,
        )

    @staticmethod
    def _is_media_only_message(user_message: UserMessage) -> bool:
        """Return whether a turn only carries media without user instructions."""
        has_media = bool(user_message.images or user_message.audios or user_message.videos)
        return has_media and not (user_message.text or "").strip()

    @staticmethod
    def _format_saved_media_history_content(
        *,
        image_files: list[str],
        audio_files: list[str],
        video_files: list[str],
    ) -> str:
        """Format saved media paths as readable user-message history content."""
        lines = ["[Media-only message saved to workspace]"]
        if image_files:
            lines.append("Images: " + ", ".join(image_files))
        if audio_files:
            lines.append("Audios: " + ", ".join(audio_files))
        if video_files:
            lines.append("Videos: " + ", ".join(video_files))
        return "\n".join(lines)

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
            get_chat_id=self._get_current_chat_id,
            run_subagent=self.run_subagent,
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
            active_task_store_factory=self._get_active_task_store,
            get_message_count=lambda chat_id: get_storage_message_count(self.storage, chat_id),
        )
        
        logger.info(f"agent.init | tools={', '.join(self.tools.tool_names)}")

    async def _load_history(self, chat_id: str) -> list[ChatMessage]:
        """
        從儲存區載入對話歷史。
        
        Load conversation history from storage.
        
        Args:
            chat_id: 聊天室 ID。The chat session ID.
        
        Returns:
            ChatMessage 物件列表，供 LLM 使用。
            List of ChatMessage objects for LLM consumption.
        """
        # 從 storage 取訊息（使用 agent.max_history 限制數量）
        stored_messages = await self.storage.get_messages(
            chat_id, 
            limit=self.config.max_history
        )

        # 轉換成 ChatMessage 格式
        chat_messages = []
        for m in stored_messages:
            if isinstance(m, dict):
                chat_messages.append(ChatMessage(role=m.get("role", "?"), content=m.get("content", "")))
            else:
                chat_messages.append(ChatMessage(role=m.role, content=m.content))
        
        return chat_messages

    async def _save_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        儲存訊息到儲存區。
        
        Save a message to storage.
        
        Args:
            chat_id: 聊天室 ID。The chat session ID.
            role: 訊息角色（"user"、"assistant" 或 "tool"）。
                  Message role ("user", "assistant", or "tool").
            content: 訊息內容。Message content.
            tool_name: 如果是工具結果，記錄工具名稱。
                       Tool name if this is a tool result.
        """
        created_at = time.time()
        await self.storage.add_message(
            chat_id,
            StoredMessage(
                role=role,
                content=content,
                timestamp=created_at,
                tool_name=tool_name,
                metadata=dict(metadata or {}),
            )
        )
        if self.search_store is not None:
            try:
                await self.search_store.index_message(
                    chat_id=chat_id,
                    role=role,
                    content=content,
                    tool_name=tool_name,
                    created_at=created_at,
                )
            except Exception as e:
                logger.warning("[{}] Failed to index message for search: {}", chat_id, e)

    def _log_prepared_messages(self, log_id: str, messages: list[dict[str, Any]]) -> None:
        """Log prepared prompt/messages when prompt logging is enabled."""
        if not self.log_config.log_system_prompt:
            return

        try:
            system_msg = next((m for m in messages if m.get("role") == "system"), None)
            if system_msg:
                system_prompt = str(system_msg.get("content", ""))
                self._write_full_system_prompt_log(log_id, system_prompt)
                max_chars = 240
                if self.log_config.log_system_prompt_lines > 0:
                    max_chars = max(120, self.log_config.log_system_prompt_lines * 120)
                logger.info(
                    f"[{log_id}] prompt.system | {self._format_log_preview(system_prompt, max_chars=max_chars)}"
                )
                if ":subagent:" not in log_id:
                    available_subagents = self._extract_available_subagents(system_prompt)
                    names = ", ".join(available_subagents) if available_subagents else "<none>"
                    logger.info(
                        f"[{log_id}] prompt.subagents | count={len(available_subagents)} names={names}"
                    )

            for index, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                if role == "system":
                    continue
                logger.info(
                    f"[{log_id}] prompt.message[{index}] | role={role} preview={self._format_log_preview(msg.get('content', ''))}"
                )
        except Exception as e:
            logger.error(f"[{log_id}] prompt.log.error | error={e}")

    async def _execute_messages(
        self,
        log_id: str,
        chat_messages: list[ChatMessage],
        *,
        allow_tools: bool,
        tool_result_chat_id: str | None = None,
        tool_registry: ToolRegistry | None = None,
        on_tool_before_execute: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_tool_after_execute: Callable[[str, dict[str, Any], str], Awaitable[None]] | None = None,
        on_llm_status: Callable[[str], Awaitable[None]] | None = None,
        refresh_system_prompt: Callable[[], str] | None = None,
        max_tool_iterations: int | None = None,
    ) -> ExecutionResult:
        """Run the shared LLM execution loop for main and delegated agents."""
        return await self.execution_engine.execute_messages(
            log_id,
            chat_messages,
            allow_tools=allow_tools,
            tool_result_chat_id=tool_result_chat_id,
            tool_registry=tool_registry,
            on_tool_before_execute=on_tool_before_execute,
            on_tool_after_execute=on_tool_after_execute,
            on_llm_status=on_llm_status,
            refresh_system_prompt=refresh_system_prompt,
            max_tool_iterations=max_tool_iterations,
        )

    def _build_subagent_tools(self, prompt_type: str, *, workspace: Path | None = None) -> ToolRegistry:
        """Build the tool registry exposed to one subagent profile."""
        return build_subagent_tool_registry(
            self.tools,
            prompt_type,
            app_home=self.app_home,
            session_workspace=workspace or self._get_current_workspace(),
        )

    def _get_current_images(self) -> list[str] | None:
        """Return images attached to the current active turn."""
        return self._current_images.get()

    def _get_current_audios(self) -> list[str] | None:
        """Return audios attached to the current active turn."""
        return self._current_audios.get()

    def _get_current_videos(self) -> list[str] | None:
        """Return videos attached to the current active turn."""
        return self._current_videos.get()

    def _queue_outbound_media(self, kind: str, payload: str) -> str | None:
        """Queue one media payload to be attached to the current assistant reply."""
        media = self._current_outbound_media.get()
        if media is None:
            return "Error: outbound media can only be queued while processing a user message."

        key = self.OUTBOUND_MEDIA_KEYS.get(kind)
        if key is None:
            return f"Error: unsupported outbound media kind: {kind}"

        value = str(payload or "").strip()
        if not value:
            return "Error: outbound media payload cannot be empty."

        media.setdefault(key, []).append(value)
        return None

    def _get_queued_outbound_media(self) -> dict[str, list[str]]:
        """Return queued outbound media for the current turn."""
        media = self._current_outbound_media.get() or {}
        return {key: list(media.get(key) or []) for key in ("images", "voices", "audios", "videos")}

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
        hints: list[str] = []
        if user_images:
            hints.append(
                f"User attached {len(user_images)} image(s). Use analyze_image or ocr_image only if the user's text asks for visual understanding or text extraction."
            )
            if user_image_files:
                hints.append(f"Saved inbound image file(s) under the chat workspace: {', '.join(user_image_files)}.")
        if user_audios:
            hints.append(
                f"User attached {len(user_audios)} audio clip(s). Use transcribe_audio only if the user's text asks for spoken content."
            )
            if user_audio_files:
                hints.append(f"Saved inbound audio file(s) under the chat workspace: {', '.join(user_audio_files)}.")
        if user_videos:
            hints.append(
                f"User attached {len(user_videos)} video clip(s). Use analyze_video only if the user's text asks for video understanding."
            )
            if user_video_files:
                hints.append(f"Saved inbound video file(s) under the chat workspace: {', '.join(user_video_files)}.")
        if not hints:
            return current_message
        return f"{current_message}\n\n[{ ' '.join(hints) }]"

    async def call_llm(
        self,
        chat_id: str,
        current_message: str,
        channel: str | None = None,
        allow_tools: bool = True,
        user_images: list[str] | None = None,
        user_image_files: list[str] | None = None,
        user_audio_files: list[str] | None = None,
        user_video_files: list[str] | None = None,
        *,
        transport_chat_id: str | None = None,
        emit_tool_progress: bool = False,
    ) -> ExecutionResult:
        """
        呼叫 LLM 生成對話回應。
        
        Call LLM to generate a response for the current conversation.
        
        如果 LLM 請求工具呼叫，會處理工具執行迴圈。
        Handles tool execution loop if LLM requests tool calls.
        
        Args:
            chat_id: 聊天室 ID，用於載入歷史。
                      The chat session ID for loading history.
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
        await self._maybe_seed_active_task(chat_id, current_message)

        # 從 storage 載入歷史
        logger.info(f"[{chat_id}] history.load | requested=true")
        history_messages = await self._load_history(chat_id)

        # 過濾掉 tool 訊息（tool results 只能在同一輪對話中使用）
        filtered = []
        for m in history_messages:
            role = m.get("role", "?") if isinstance(m, dict) else getattr(m, "role", "?")
            if role != "tool":
                filtered.append(m)
        history_messages = filtered

        # The current user message is already passed explicitly to the context builder.
        # Drop the newest persisted user message for this turn to avoid duplicate/blank user entries.
        if history_messages:
            latest = history_messages[-1]
            latest_role = latest.get("role", "?") if isinstance(latest, dict) else getattr(latest, "role", "?")
            latest_content = latest.get("content", "") if isinstance(latest, dict) else getattr(latest, "content", "")
            if latest_role == "user" and latest_content == current_message:
                history_messages = history_messages[:-1]

        # 轉換成 dict 格式（給 context builder 用）
        history_dicts = []
        for m in history_messages:
            if isinstance(m, dict):
                msg = {"role": m.get("role", "?"), "content": m.get("content", "")}
                if m.get("tool_call_id"):
                    msg["tool_call_id"] = m["tool_call_id"]
            else:
                msg = {"role": m.role, "content": m.content}
                if getattr(m, "tool_call_id", None):
                    msg["tool_call_id"] = m.tool_call_id
            history_dicts.append(msg)

        # 用 context builder 組 messages
        logger.info(
            f"[{chat_id}] prompt.build | history={len(history_dicts)} channel={channel or '-'} images={len(user_images or [])}"
        )
        current_audios = self._get_current_audios()
        current_videos = self._get_current_videos()
        prompt_message = self._augment_message_for_media(
            current_message,
            user_images,
            current_audios,
            current_videos,
            user_image_files=user_image_files,
            user_audio_files=user_audio_files,
            user_video_files=user_video_files,
        )
        tool_schema_tokens = self._estimate_tool_schema_tokens(allow_tools=allow_tools)
        history_dicts, base_tokens, history_tokens, final_tokens = self._trim_history_to_token_budget(
            history=history_dicts,
            current_message=prompt_message,
            channel=channel,
            chat_id=chat_id,
            tool_schema_tokens=tool_schema_tokens,
        )
        effective_context_budget = self._effective_context_token_budget()
        logger.info(
            f"[{chat_id}] prompt.tokens | budget={effective_context_budget} "
            f"history_budget={self.config.history_token_budget} model_window={self.llm_context_window_tokens or '-'} "
            f"output_reserve={self.llm_chat_max_tokens} base={base_tokens} tools={tool_schema_tokens} "
            f"history={history_tokens} final_estimated={final_tokens}"
        )
        self._sync_runtime_mcp_tools_context()
        full_messages = self._context_builder.build_messages(
            history=history_dicts,
            current_message=prompt_message,
            current_images=None,
            channel=channel,
            chat_id=chat_id,
        )

        # 轉換成 ChatMessage 格式
        chat_messages = []
        for m in full_messages:
            msg = ChatMessage(role=m["role"], content=m.get("content", ""))
            if m.get("tool_call_id"):
                msg.tool_call_id = m["tool_call_id"]
            if m.get("tool_calls"):
                msg.tool_calls = m["tool_calls"]
            chat_messages.append(msg)

        self._log_prepared_messages(chat_id, full_messages)
        run_id = self._current_run_id.get()
        on_tool_before_execute = self._make_tool_progress_hook(
            channel=channel,
            transport_chat_id=transport_chat_id,
            session_chat_id=chat_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        on_tool_after_execute = self._make_tool_result_hook(
            channel=channel,
            transport_chat_id=transport_chat_id,
            session_chat_id=chat_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        on_llm_status = self._make_llm_status_hook(
            channel=channel,
            transport_chat_id=transport_chat_id,
            session_chat_id=chat_id,
            run_id=run_id,
            enabled=emit_tool_progress,
        )
        execute_kwargs = {
            "allow_tools": allow_tools,
            "tool_result_chat_id": chat_id if allow_tools else None,
            "on_tool_before_execute": on_tool_before_execute,
            "on_llm_status": on_llm_status,
            "refresh_system_prompt": lambda: self._context_builder.build_system_prompt(chat_id),
        }
        if on_tool_after_execute is not None:
            execute_kwargs["on_tool_after_execute"] = on_tool_after_execute
        return await self._execute_messages(chat_id, chat_messages, **execute_kwargs)

    def _skill_review_tool_registry(self) -> ToolRegistry | None:
        """Tools allowed during background skill review (subset of main registry)."""
        allowed = frozenset({"read_skill", "configure_skill"})
        available = set(self.tools.tool_names)
        if not allowed.issubset(available):
            return None
        excluded = available - allowed
        return self.tools.filtered(exclude_names=excluded)

    def _maybe_schedule_skill_review(self, chat_id: str, result: ExecutionResult) -> None:
        """Fire-and-forget background pass after a heavy tool turn without skill upsert."""
        if not self.config.skill_review_enabled:
            return
        if result.used_configure_skill:
            return
        if result.executed_tool_calls < self.config.skill_review_min_tool_calls:
            return
        if self._skill_review_tool_registry() is None:
            return

        existing = self._skill_review_tasks.get(chat_id)
        if existing is not None and not existing.done():
            self._skill_review_rerun.add(chat_id)
            return

        task: asyncio.Task | None = None

        async def _run() -> None:
            try:
                while True:
                    self._skill_review_rerun.discard(chat_id)
                    try:
                        await self._run_skill_review(chat_id)
                    except Exception:
                        logger.exception("[%s] skill.review.failed", chat_id)
                    if chat_id not in self._skill_review_rerun:
                        break
                    logger.info("[%s] skill.review.rerun", chat_id)
            except asyncio.CancelledError:
                pass
            finally:
                if task is not None and self._skill_review_tasks.get(chat_id) is task:
                    self._skill_review_tasks.pop(chat_id, None)
                self._skill_review_rerun.discard(chat_id)

        try:
            task = asyncio.get_running_loop().create_task(_run())
            self._skill_review_tasks[chat_id] = task
        except RuntimeError:
            logger.warning("[%s] skill.review.skip | reason=no-running-event-loop", chat_id)

    async def _run_skill_review(self, chat_id: str) -> None:
        tool_registry = self._skill_review_tool_registry()
        if tool_registry is None:
            return

        stored = await self.storage.get_messages(chat_id, limit=self.config.skill_review_transcript_messages)
        transcript = format_stored_messages_for_transcript(stored)
        if len(transcript) < 80:
            logger.info("[%s] skill.review.skip | reason=transcript-too-short", chat_id)
            return

        user_content = build_skill_review_user_content(transcript)
        chat_messages = [
            ChatMessage(role="system", content=SKILL_REVIEW_SYSTEM),
            ChatMessage(role="user", content=user_content),
        ]
        log_id = f"{chat_id}:skill-review"
        token = self._current_chat_id.set(chat_id)
        try:
            await self._execute_messages(
                log_id,
                chat_messages,
                allow_tools=True,
                tool_result_chat_id=None,
                tool_registry=tool_registry,
                on_tool_before_execute=None,
                refresh_system_prompt=lambda: self._context_builder.build_system_prompt(chat_id),
                max_tool_iterations=self.config.skill_review_max_tool_iterations,
            )
        finally:
            self._current_chat_id.reset(token)
        logger.info("[%s] skill.review.done", chat_id)

    async def run_subagent(
        self,
        task: str,
        prompt_type: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Run or resume a delegated subagent task through a child storage session."""
        from .subagent_builder import SubagentMessageBuilder
        from ..subagent_prompts import get_all_subagents

        task_text = str(task or "").strip()
        if not task_text:
            return "Error: subagent task must be a non-empty string."

        workspace = self._get_current_workspace()
        subagents = get_all_subagents(self.app_home, session_workspace=workspace)
        parent_chat_id = self._get_current_chat_id() or "default"

        resume_task_id = str(task_id or "").strip() or None
        is_resume = resume_task_id is not None
        if resume_task_id:
            validation_error = validate_subagent_task_id(resume_task_id)
            if validation_error:
                return validation_error
            child_task_id = resume_task_id
        else:
            child_task_id = new_subagent_task_id()

        child_chat_id = build_child_subagent_chat_id(parent_chat_id, child_task_id)
        existing_child_messages = await self.storage.get_messages(child_chat_id)
        if is_resume and not existing_child_messages:
            return f"Error: unknown task_id '{child_task_id}' for current chat. Start a new delegate task instead."

        stored_prompt_type = extract_subagent_prompt_type(existing_child_messages)
        requested_prompt_type = str(prompt_type).strip() if prompt_type is not None else ""
        effective_prompt_type = requested_prompt_type or stored_prompt_type or "writer"
        if stored_prompt_type and requested_prompt_type and requested_prompt_type != stored_prompt_type:
            return (
                f"Error: task_id '{child_task_id}' was created with prompt_type '{stored_prompt_type}', "
                f"not '{requested_prompt_type}'. Omit prompt_type or use the original prompt_type to resume."
            )
        if effective_prompt_type not in subagents:
            available = ", ".join(subagents)
            return f"Error: unknown subagent type '{effective_prompt_type}'. Available: {available}"

        try:
            subagent_tools = self._build_subagent_tools(effective_prompt_type, workspace=workspace)
            subagent_profile = profile_for_subagent(
                effective_prompt_type,
                app_home=self.app_home,
                session_workspace=workspace,
            )
        except ValueError as e:
            return f"Error: {str(e)}"

        await self._save_message(
            child_chat_id,
            "user",
            task_text,
            metadata={
                "kind": "subagent_task",
                "task_id": child_task_id,
                "parent_chat_id": parent_chat_id,
                "prompt_type": effective_prompt_type,
                "resume": is_resume,
            },
        )

        log_id = f"{parent_chat_id}:subagent:{effective_prompt_type}:{child_task_id}"

        subagent_builder = SubagentMessageBuilder(
            skills_loader=getattr(self._context_builder, "skills_loader", None)
        )
        chat_messages = [
            ChatMessage(
                role="system",
                content=subagent_builder.build_system_prompt(
                    effective_prompt_type,
                    workspace=workspace,
                    app_home=self.app_home,
                ),
            )
        ]
        stored_child_messages = await self.storage.get_messages(child_chat_id, limit=self.config.max_history)
        for message in stored_child_messages:
            role = message.get("role", "?") if isinstance(message, dict) else getattr(message, "role", "?")
            if role == "tool":
                continue
            content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
            chat_messages.append(ChatMessage(role=role, content=content))

        self._log_prepared_messages(
            log_id,
            [{"role": msg.role, "content": msg.content} for msg in chat_messages],
        )
        logger.info(
            f"[{log_id}] subagent.run | child_chat_id={child_chat_id} resume={is_resume} "
            f"workspace={workspace} task={self._format_log_preview(task_text, max_chars=200)}"
        )
        logger.info(
            f"[{log_id}] subagent.tools | profile={subagent_profile.name} names={', '.join(subagent_tools.tool_names) or '<none>'}"
        )
        sub_result = await self._execute_messages(
            log_id,
            chat_messages,
            allow_tools=bool(subagent_tools.tool_names),
            tool_result_chat_id=child_chat_id,
            tool_registry=subagent_tools,
        )
        await self._save_message(
            child_chat_id,
            "assistant",
            sub_result.content,
            metadata={
                "kind": "subagent_result",
                "task_id": child_task_id,
                "parent_chat_id": parent_chat_id,
                "prompt_type": effective_prompt_type,
            },
        )
        return (
            f"Task ID: {child_task_id}\n"
            f"Subagent: {effective_prompt_type}\n\n"
            f"Result:\n{sub_result.content}"
        )

    async def process(self, user_message: UserMessage) -> AssistantMessage:
        """
        處理使用者訊息的主要入口函式。

        參數：
            user_message: UserMessage 統一格式的訊息

        回傳：
            AssistantMessage: 統一格式的回覆
        """
        session_chat_id = user_message.session_chat_id or user_message.chat_id or "default"
        channel = user_message.channel or None

        if ":" not in session_chat_id:
            logger.warning(
                "Received non-namespaced chat_id '{}' in Agent.process; this may mix sessions if MessageQueue is bypassed",
                session_chat_id,
            )
        
        sender = user_message.sender_name or user_message.sender_id or "-"
        logger.info(
            f"[{session_chat_id}] inbound | channel={channel or '-'} sender={sender} images={len(user_message.images or [])} "
            f"text={self._format_log_preview(user_message.text, max_chars=200)}"
        )
        image_files = self._persist_inbound_images(session_chat_id, user_message.images)
        audio_files = self._persist_inbound_audios(session_chat_id, user_message.audios)
        video_files = self._persist_inbound_videos(session_chat_id, user_message.videos)

        user_metadata = {
            **dict(user_message.metadata or {}),
            "channel": channel,
            "transport_chat_id": user_message.chat_id,
            "sender_id": user_message.sender_id,
            "sender_name": user_message.sender_name,
            "images_count": len(user_message.images or []),
            "image_files": image_files or None,
            "images_dir": "images" if image_files else None,
            "audios_count": len(user_message.audios or []),
            "audio_files": audio_files or None,
            "audios_dir": "audios" if audio_files else None,
            "videos_count": len(user_message.videos or []),
            "video_files": video_files or None,
            "videos_dir": "videos" if video_files else None,
        }
        user_metadata = {key: value for key, value in user_metadata.items() if value is not None}
        assistant_metadata = {
            "channel": channel,
            "transport_chat_id": user_message.chat_id,
        }
        assistant_metadata = {key: value for key, value in assistant_metadata.items() if value is not None}

        transport_chat_id = str(user_message.chat_id) if user_message.chat_id is not None else None
        run_id = f"run_{uuid4().hex}"
        run_metadata = {
            "channel": channel,
            "transport_chat_id": transport_chat_id,
            "sender_id": user_message.sender_id,
            "sender_name": user_message.sender_name,
        }
        run_metadata = {key: value for key, value in run_metadata.items() if value is not None}
        await self._create_run(session_chat_id, run_id, status="running", metadata=run_metadata)
        await self._emit_run_event(
            session_chat_id,
            run_id,
            "run_started",
            {
                "status": "running",
                "text_len": len(user_message.text or ""),
                "images_count": len(user_message.images or []),
                "audios_count": len(user_message.audios or []),
                "videos_count": len(user_message.videos or []),
            },
            channel=channel,
            transport_chat_id=transport_chat_id,
        )

        if self._is_media_only_message(user_message):
            media_history_content = self._format_saved_media_history_content(
                image_files=image_files,
                audio_files=audio_files,
                video_files=video_files,
            )
            await self._save_message(session_chat_id, "user", media_history_content, metadata=user_metadata)
            response = self.messages.agent.media_saved_ack
            logger.info(
                f"[{session_chat_id}] outbound | media_only=true text={self._format_log_preview(response, max_chars=200)}"
            )
            await self._save_message(session_chat_id, "assistant", response, metadata=assistant_metadata)
            finished_at = time.time()
            await self._emit_run_event(
                session_chat_id,
                run_id,
                "run_finished",
                {"status": "completed", "reason": "media_only", "response_len": len(response or "")},
                channel=channel,
                transport_chat_id=transport_chat_id,
            )
            await self._update_run_status(session_chat_id, run_id, "completed", finished_at=finished_at)
            return AssistantMessage(
                text=response,
                channel=channel or "unknown",
                chat_id=user_message.chat_id,
                session_chat_id=session_chat_id,
                metadata=assistant_metadata,
            )

        token = self._current_chat_id.set(session_chat_id)
        channel_token = self._current_channel.set(channel)
        transport_chat_id_token = self._current_transport_chat_id.set(
            str(user_message.chat_id) if user_message.chat_id is not None else None
        )
        images_token = self._current_images.set(list(user_message.images or []))
        audios_token = self._current_audios.set(list(user_message.audios or []))
        videos_token = self._current_videos.set(list(user_message.videos or []))
        outbound_media_token = self._current_outbound_media.set(
            {"images": [], "voices": [], "audios": [], "videos": []}
        )
        run_token = self._current_run_id.set(run_id)
        try:
            if not self.llm_configured:
                logger.warning("[{}] agent.skip | reason=llm-not-configured", session_chat_id)
                await self._save_message(session_chat_id, "user", user_message.text, metadata=user_metadata)
                response = self.messages.agent.llm_not_configured
                logger.info(
                    f"[{session_chat_id}] outbound | text={self._format_log_preview(response, max_chars=200)}"
                )
                await self._save_message(session_chat_id, "assistant", response, metadata=assistant_metadata)
                finished_at = time.time()
                await self._emit_run_event(
                    session_chat_id,
                    run_id,
                    "run_finished",
                    {"status": "completed", "reason": "llm_not_configured", "response_len": len(response or "")},
                    channel=channel,
                    transport_chat_id=transport_chat_id,
                )
                await self._update_run_status(session_chat_id, run_id, "completed", finished_at=finished_at)
                return AssistantMessage(
                    text=response,
                    channel=channel or "unknown",
                    chat_id=user_message.chat_id,
                    session_chat_id=session_chat_id,
                    metadata=assistant_metadata,
                )

            await self.connect_mcp()

            # 1. 把使用者訊息存入 storage
            await self._save_message(session_chat_id, "user", user_message.text, metadata=user_metadata)

            # 2. 呼叫 LLM（傳入 channel 和圖片）
            logger.info(f"[{session_chat_id}] agent.run | status=processing")
            await self._emit_run_event(
                session_chat_id,
                run_id,
                "llm_status",
                {"message": "processing"},
                channel=channel,
                transport_chat_id=transport_chat_id,
            )
            exec_result = await self.call_llm(
                session_chat_id,
                current_message=user_message.text,
                channel=channel,
                user_images=user_message.images,
                user_image_files=image_files,
                user_audio_files=audio_files,
                user_video_files=video_files,
                transport_chat_id=transport_chat_id,
                emit_tool_progress=True,
            )
            response = exec_result.content
            outbound_media = self._get_queued_outbound_media()

            logger.info(
                f"[{session_chat_id}] outbound | text={self._format_log_preview(response, max_chars=200)}"
            )

            # 3. 把 AI 回覆存入 storage
            await self._save_message(session_chat_id, "assistant", response, metadata=assistant_metadata)

            # 3.5 先套用保守的即時 task 狀態轉換，再交給背景更新做細化
            await self._maybe_apply_immediate_task_transition(session_chat_id, response, exec_result)

            # 4. 在背景排程維護工作，避免拖慢主回覆
            self._schedule_post_response_maintenance(session_chat_id)

            self._maybe_schedule_skill_review(session_chat_id, exec_result)

            finished_at = time.time()
            await self._emit_run_event(
                session_chat_id,
                run_id,
                "run_finished",
                {
                    "status": "completed",
                    "response_len": len(response or ""),
                    "executed_tool_calls": exec_result.executed_tool_calls,
                    "had_tool_error": exec_result.had_tool_error,
                    "context_compactions": exec_result.context_compactions,
                },
                channel=channel,
                transport_chat_id=transport_chat_id,
            )
            await self._update_run_status(
                session_chat_id,
                run_id,
                "completed",
                metadata={
                    "executed_tool_calls": exec_result.executed_tool_calls,
                    "had_tool_error": exec_result.had_tool_error,
                    "context_compactions": exec_result.context_compactions,
                },
                finished_at=finished_at,
            )

            # 5. 回傳
            return AssistantMessage(
                text=response,
                channel=channel or "unknown",
                chat_id=user_message.chat_id,
                session_chat_id=session_chat_id,
                images=outbound_media["images"] or None,
                voices=outbound_media["voices"] or None,
                audios=outbound_media["audios"] or None,
                videos=outbound_media["videos"] or None,
                metadata=assistant_metadata,
            )
        except asyncio.CancelledError:
            finished_at = time.time()
            await self._emit_run_event(
                session_chat_id,
                run_id,
                "run_failed",
                {"status": "cancelled", "error": "cancelled"},
                channel=channel,
                transport_chat_id=transport_chat_id,
            )
            await self._update_run_status(session_chat_id, run_id, "cancelled", finished_at=finished_at)
            raise
        except Exception as exc:
            logger.exception(
                f"[{session_chat_id}] Agent.process failed: channel={channel}, "
                f"text_len={len(user_message.text or '')}, images={len(user_message.images or [])}, audios={len(user_message.audios or [])}, videos={len(user_message.videos or [])}"
            )
            finished_at = time.time()
            await self._emit_run_event(
                session_chat_id,
                run_id,
                "run_failed",
                {
                    "status": "failed",
                    "error": self._format_log_preview(f"{type(exc).__name__}: {exc}", max_chars=240),
                },
                channel=channel,
                transport_chat_id=transport_chat_id,
            )
            await self._update_run_status(session_chat_id, run_id, "failed", finished_at=finished_at)
            raise
        finally:
            self._current_run_id.reset(run_token)
            self._current_outbound_media.reset(outbound_media_token)
            self._current_videos.reset(videos_token)
            self._current_audios.reset(audios_token)
            self._current_images.reset(images_token)
            self._current_transport_chat_id.reset(transport_chat_id_token)
            self._current_channel.reset(channel_token)
            self._current_chat_id.reset(token)

    async def _maybe_consolidate_memory(self, chat_id: str) -> None:
        """
        檢查是否需要進行記憶整合並執行。
        
        Check if memory consolidation is needed and run it.
        
        當訊息數量超過閾值時，將未整合的訊息整合到長期記憶中。
        Consolidates unconsolidated messages into long-term memory when
        the message count exceeds the threshold.
        
        Args:
            chat_id: 聊天室 ID。The chat session ID.
        """
        await self.memory_consolidation.maybe_consolidate(chat_id)

    async def _maybe_update_user_profile(self, chat_id: str) -> None:
        """Check whether this chat's USER.md (session workspace) should be refreshed."""
        await self.user_profile_update.maybe_update(chat_id)

    async def _maybe_update_active_task(self, chat_id: str) -> None:
        """Check whether this chat's ACTIVE_TASK.md should be refreshed."""
        await self.active_task_update.maybe_update(chat_id)

    async def _maybe_apply_immediate_task_transition(
        self,
        chat_id: str,
        response_text: str,
        exec_result: ExecutionResult,
    ) -> None:
        """Apply conservative immediate task-state transitions before background maintenance runs."""
        store = self._get_active_task_store(chat_id)
        if store is None:
            return
        if store.read_status() not in {"active", "blocked", "waiting_user"}:
            return

        transition = infer_immediate_task_transition(
            response_text,
            had_tool_error=exec_result.had_tool_error,
        )
        if transition is None:
            return

        status, detail = transition
        if status == "waiting_user":
            store.update_fields(status="waiting_user", open_questions=[detail or "need user input"], force=True)
        elif status == "blocked":
            store.update_fields(status="blocked", open_questions=[detail or "blocked"], force=True)
        else:
            return

        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("auto_direct_transition", "immediate", details={"status": status, "reason": detail or ""})

    async def _maybe_update_recent_summary(self, chat_id: str) -> None:
        """Check whether RECENT_SUMMARY.md should be refreshed."""
        await self.recent_summary_update.maybe_update(chat_id)

    def _clear_active_task(self, chat_id: str) -> None:
        """Reset ACTIVE_TASK.md for one chat session."""
        if self.app_home is None:
            return
        create_active_task_store(
            self.app_home,
            chat_id,
            workspace_root=self.tool_workspace,
        ).clear(chat_id)

    def _get_active_task_store(self, chat_id: str):
        if self.app_home is None:
            return None
        return create_active_task_store(
            self.app_home,
            chat_id,
            workspace_root=self.tool_workspace,
        )

    async def show_active_task(self, chat_id: str) -> str | None:
        """Return the current ACTIVE_TASK block for user display, if any."""
        store = self._get_active_task_store(chat_id)
        if store is None:
            return None
        return store.render_for_user()

    async def show_active_task_full(self, chat_id: str) -> str | None:
        """Return the full ACTIVE_TASK block for user display, if any."""
        store = self._get_active_task_store(chat_id)
        if store is None:
            return None
        return store.render_full_for_user()

    async def show_active_task_history(self, chat_id: str, *, limit: int = 10) -> str | None:
        """Return recent ACTIVE_TASK events for user display, if any."""
        store = self._get_active_task_store(chat_id)
        if store is None:
            return None
        return store.render_history(limit=limit)

    async def set_active_task_from_text(self, chat_id: str, task_text: str) -> str | None:
        """Create or replace the current ACTIVE_TASK from explicit user text."""
        store = self._get_active_task_store(chat_id)
        if store is None:
            return None
        task_block = build_task_block_from_text(task_text, force=True)
        if not task_block:
            return None
        store.write_managed_block(task_block)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("set", "user", details={"task": task_text})
        return store.render_full_for_user()

    async def activate_active_task(self, chat_id: str) -> str | None:
        """Mark the current ACTIVE_TASK as active again."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="active", open_questions=["none"], force=True)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("activate", "user")
        return f"# Active Task\n\n{rendered}"

    async def reopen_active_task(self, chat_id: str) -> str | None:
        """Reopen a terminal ACTIVE_TASK and resume it as active."""
        store = self._get_active_task_store(chat_id)
        if store is None:
            return None
        if store.read_status() not in {"done", "cancelled"}:
            return None
        rendered = store.update_fields(status="active", force=True)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("reopen", "user")
        return f"# Active Task\n\n{rendered}"

    async def block_active_task(self, chat_id: str, reason: str) -> str | None:
        """Mark the current ACTIVE_TASK as blocked with one explicit reason."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="blocked", open_questions=[reason], force=True)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("block", "user", details={"reason": reason})
        return f"# Active Task\n\n{rendered}"

    async def wait_on_active_task(self, chat_id: str, question: str) -> str | None:
        """Mark the current ACTIVE_TASK as waiting for user input."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="waiting_user", open_questions=[question], force=True)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("wait", "user", details={"question": question})
        return f"# Active Task\n\n{rendered}"

    async def set_active_task_current_step(self, chat_id: str, step_text: str) -> str | None:
        """Replace the current step for the active task."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(status="active", current_step=step_text, force=True)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("set_current_step", "user", details={"current_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def set_active_task_next_step(self, chat_id: str, step_text: str) -> str | None:
        """Replace the planned next step for the active task."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        rendered = store.update_fields(next_step=step_text, force=True)
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event("set_next_step", "user", details={"next_step": step_text})
        return f"# Active Task\n\n{rendered}"

    async def advance_active_task(self, chat_id: str) -> str | None:
        """Promote the next step into the current step and mark the previous step complete."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        next_step = _extract_task_field(current_block, "Next step")
        if next_step == "not set":
            return None
        rendered = store.update_fields(
            status="active",
            current_step=next_step,
            next_step="not set",
            append_completed_step=current_step,
            force=True,
        )
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event(
            "advance",
            "user",
            details={"completed_step": current_step, "new_current_step": next_step},
        )
        return f"# Active Task\n\n{rendered}"

    async def complete_active_task_step(self, chat_id: str, next_step_override: str | None = None) -> str | None:
        """Complete the current step and either advance or finish the task."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        current_block = store.read_managed_block()
        current_step = _extract_task_field(current_block, "Current step")
        rendered = store.complete_current_step(next_step_override=next_step_override)
        if rendered is None:
            return None
        message_count = await get_storage_message_count(self.storage, chat_id)
        store.set_processed_index(chat_id, message_count)
        store.append_event(
            "complete_step",
            "user",
            details={
                "completed_step": current_step,
                "next_step_override": next_step_override or "",
            },
        )
        return f"# Active Task\n\n{rendered}"

    async def mark_active_task_status(self, chat_id: str, status: str) -> str | None:
        """Set the current ACTIVE_TASK status when one exists."""
        store = self._get_active_task_store(chat_id)
        if store is None or store.read_status() == "inactive":
            return None
        store.update_fields(status=status, open_questions=["none"] if status in {"active", "done", "cancelled"} else None, force=True)
        if status in {"done", "cancelled"}:
            message_count = await get_storage_message_count(self.storage, chat_id)
            store.set_processed_index(chat_id, message_count)
        store.append_event(status, "user")
        return store.render_full_for_user()

    async def reset_active_task(self, chat_id: str) -> None:
        """Clear the current ACTIVE_TASK state for one session."""
        store = self._get_active_task_store(chat_id)
        if store is None:
            return
        self._clear_active_task(chat_id)
        store.append_event("reset", "user")

    async def reset_history(self, chat_id: str | None = None) -> None:
        """
        清除對話歷史。
        
        Clear conversation history.
        
        Args:
            chat_id: 聊天室 ID。如果為 None 則清除所有聊天室。
                      The chat session ID. If None, clears all chats.
        """
        if chat_id:
            await self.storage.clear_messages(chat_id)
            self._clear_active_task(chat_id)
            self._clear_recent_summary(chat_id)
            if self.search_store is not None:
                try:
                    await self.search_store.clear_chat(chat_id)
                except Exception as e:
                    logger.warning("[{}] Failed to clear search index: {}", chat_id, e)
        else:
            # 清除所有聊天室
            all_chats = await self.storage.get_all_chats()
            for c in all_chats:
                await self.storage.clear_messages(c)
                self._clear_active_task(c)
                self._clear_recent_summary(c)
                if self.search_store is not None:
                    try:
                        await self.search_store.clear_chat(c)
                    except Exception as e:
                        logger.warning("[{}] Failed to clear search index: {}", c, e)
