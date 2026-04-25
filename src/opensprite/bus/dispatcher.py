"""
opensprite/bus/dispatcher.py - 訊息排程中心

角色：訊息調度中心
- 接收外面傳來的訊息，排隊交給 Agent 處理
- 將 Agent 的回覆排隊發送出去
- 支援多個對話同時並行處理

設計理念：
- 支援多個對話同時進行
- inbound / outbound 分離（解耦）
- 對話歷史由 Agent + Storage 管理
- 用 MessageBus 接收/發送訊息

流程：
  外部 → enqueue() → inbound Queue → Agent → outbound Queue → channel handler → 外部

"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import shlex
from typing import Any, Awaitable, Callable
from . import MessageBus, InboundMessage, OutboundMessage, RunEvent
from .message import UserMessage, AssistantMessage
from ..config import MessagesConfig
from ..cron.presentation import render_cron_jobs
from ..cron.types import CronSchedule
from ..utils.log import logger


@dataclass
class Conversation:
    """
    單一對話的狀態
    
    這裡只追蹤「是否正在處理」，
    對話歷史由 Agent + Storage 管理。
    """
    chat_id: str
    pending: asyncio.Event = field(default_factory=asyncio.Event)  # 等待回覆


ResponseHandler = Callable[[AssistantMessage, str, str | None], Awaitable[None]]
RunEventHandler = Callable[[RunEvent], Awaitable[None]]


class MessageQueue:
    """
    訊息佇列管理器（Bus 版本）
    
    負責：
    - inbound: 接收新訊息
    - outbound: 發送回覆
    - **非同步並行處理多個對話**（每個訊息spawn成獨立task）
    - 對話歷史由 Agent + Storage 管理
    
    架構：
    ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
    │   inbound   │ ──→  │  AgentLoop   │ ──→  │   outbound   │
    │    Queue    │      │  (處理訊息)   │      │    Queue     │
    └──────────────┘      └──────────────┘      └──────────────┘
                                                            │
                                                            ▼
                                             channel response handlers
    """
    
    def __init__(self, agent, bus: MessageBus | None = None, messages_config: MessagesConfig | None = None):
        """
        初始化
        
        參數：
            agent: AgentLoop 實例
            bus: MessageBus 實例（可選，預設新建）
        """
        self.agent = agent
        self.bus = bus or MessageBus()
        if hasattr(agent, "_message_bus"):
            agent._message_bus = self.bus
        self.messages = messages_config or getattr(agent, "messages", None) or MessagesConfig()
        self.conversations: dict[str, Conversation] = {}  # chat_id -> Conversation
        self.running = False
        # 追蹤所有 active tasks: chat_id -> list of tasks
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._session_tails: dict[str, asyncio.Task] = {}
        self._response_handlers: dict[str, ResponseHandler] = {}
        self._run_event_handlers: dict[str, RunEventHandler] = {}
        # Outbound 消費者任務
        self._outbound_task: asyncio.Task | None = None
        self._run_event_task: asyncio.Task | None = None

    @staticmethod
    def normalize_channel(channel: str | None) -> str:
        """Normalize channel names for routing."""
        return (channel or "unknown").strip() or "unknown"
    
    def get_or_create_conversation(self, chat_id: str) -> Conversation:
        """
        取得或建立對話
        
        參數：
            chat_id: 聊天室 ID
        
        回傳：
            Conversation 物件
        """
        if chat_id not in self.conversations:
            self.conversations[chat_id] = Conversation(chat_id=chat_id)
        return self.conversations[chat_id]

    @staticmethod
    def build_session_chat_id(channel: str | None, chat_id: str | None) -> str:
        """Build an internal session ID namespaced by channel."""
        normalized_channel = MessageQueue.normalize_channel(channel)
        normalized_chat_id = (chat_id or "default").strip() or "default"
        return f"{normalized_channel}:{normalized_chat_id}"

    @classmethod
    def resolve_session_chat_id(cls, chat_id: str, channel: str | None = None) -> str:
        """Resolve external or already-namespaced chat IDs to internal session IDs."""
        if ":" in chat_id:
            return chat_id
        return cls.build_session_chat_id(channel or "cli", chat_id)

    def register_response_handler(self, channel: str, handler: ResponseHandler) -> None:
        """Register the outbound response handler for a channel."""
        normalized_channel = self.normalize_channel(channel)
        self._response_handlers[normalized_channel] = handler

    def register_run_event_handler(self, channel: str, handler: RunEventHandler) -> None:
        """Register the structured run event handler for a channel."""
        normalized_channel = self.normalize_channel(channel)
        self._run_event_handlers[normalized_channel] = handler

    @staticmethod
    def _first_command(text: str | None) -> str:
        """Return the first whitespace-delimited command token, or empty string."""
        parts = (text or "").strip().split(maxsplit=1)
        if not parts:
            return ""
        return parts[0].lower()

    @staticmethod
    def is_stop_command(text: str | None) -> bool:
        """Return whether a message should interrupt the current session."""
        command = MessageQueue._first_command(text)
        return command in {"/stop", "/stop@openspritebot"}

    @staticmethod
    def is_reset_command(text: str | None) -> bool:
        """Return whether a message should reset the current session."""
        command = MessageQueue._first_command(text)
        return command in {"/reset", "/reset@openspritebot"}

    @staticmethod
    def is_cron_command(text: str | None) -> bool:
        """Return whether a message should use immediate cron command handling."""
        command = MessageQueue._first_command(text)
        return command in {"/cron", "/cron@openspritebot"}

    @staticmethod
    def is_task_command(text: str | None) -> bool:
        """Return whether a message should use immediate task command handling."""
        command = MessageQueue._first_command(text)
        return command in {"/task", "/task@openspritebot"}

    @staticmethod
    def _parse_cron_command(text: str | None) -> tuple[str, list[str]]:
        """Parse the cron command into an action and remaining args."""
        try:
            parts = shlex.split((text or "").strip())
        except ValueError:
            return "error", []
        if not parts:
            return "help", []
        args = parts[1:]
        if not args:
            return "help", []
        return args[0].lower(), args[1:]

    def _cron_help_text(self) -> str:
        """Return the built-in cron command help text."""
        return self.messages.cron.help_text

    def _task_help_text(self) -> str:
        """Return the built-in task command help text."""
        return self.messages.task.help_text

    def _cron_default_timezone(self) -> str:
        tools_config = getattr(self.agent, "tools_config", None)
        cron_config = getattr(tools_config, "cron", None)
        return getattr(cron_config, "default_timezone", "UTC") or "UTC"

    @staticmethod
    def _extract_cron_options(args: list[str]) -> tuple[list[str], str | None, bool]:
        """Split cron command arguments into positional args and flags."""
        positional: list[str] = []
        tz: str | None = None
        deliver = True
        i = 0
        while i < len(args):
            token = args[i]
            if token == "--tz":
                if i + 1 >= len(args):
                    raise ValueError("--tz requires a timezone value")
                tz = args[i + 1]
                i += 2
                continue
            if token == "--no-deliver":
                deliver = False
                i += 1
                continue
            if token == "--deliver":
                deliver = True
                i += 1
                continue
            positional.append(token)
            i += 1
        return positional, tz, deliver

    @staticmethod
    def _parse_cron_add_schedule(args: list[str]) -> tuple[CronSchedule, str, bool]:
        """Parse `/cron add ...` arguments into schedule and payload settings."""
        positional, tz, deliver = MessageQueue._extract_cron_options(args)
        if len(positional) < 3:
            raise ValueError("error_add_usage")

        mode = positional[0].lower()
        schedule_value = positional[1]
        message = " ".join(positional[2:]).strip()
        if not message:
            raise ValueError("error_message_required")

        if mode == "every":
            try:
                every_seconds = int(schedule_value)
            except ValueError as exc:
                raise ValueError("error_every_requires_integer") from exc
            if every_seconds <= 0:
                raise ValueError("error_every_requires_positive")
            if tz is not None:
                raise ValueError("error_tz_only_for_cron")
            return CronSchedule(kind="every", every_ms=every_seconds * 1000), message, deliver

        if mode == "at":
            try:
                dt = datetime.fromisoformat(schedule_value)
            except ValueError as exc:
                raise ValueError("error_at_requires_iso") from exc
            if tz is not None:
                raise ValueError("error_tz_only_for_cron")
            if dt.tzinfo is None:
                from zoneinfo import ZoneInfo

                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000)), message, deliver

        if mode == "cron":
            return CronSchedule(kind="cron", expr=schedule_value, tz=tz or "UTC"), message, deliver

        raise ValueError("error_unknown_schedule_mode")

    async def _handle_cron_command(self, session_chat_id: str, text: str | None) -> str:
        """Handle immediate cron management commands for the active session."""
        action, args = self._parse_cron_command(text)
        if action == "error":
            return self.messages.cron.error_prefix.format(message=self.messages.cron.error_invalid_quoting)
        if action in {"help", "--help", "-h"}:
            return self._cron_help_text()

        cron_manager = getattr(self.agent, "cron_manager", None)
        if cron_manager is None:
            return self.messages.cron.unavailable

        service = await cron_manager.get_or_create_service(session_chat_id)

        if action == "add":
            try:
                schedule, message, deliver = self._parse_cron_add_schedule(args)
            except ValueError as exc:
                key = str(exc)
                details = getattr(self.messages.cron, key, key)
                return self.messages.cron.error_prefix.format(message=details)

            if ":" in session_chat_id:
                channel, chat_id = session_chat_id.split(":", 1)
            else:
                channel, chat_id = "default", session_chat_id

            delete_after = schedule.kind == "at"
            try:
                job = service.add_job(
                    name=message[:30],
                    schedule=schedule,
                    message=message,
                    deliver=deliver,
                    channel=channel,
                    chat_id=chat_id,
                    delete_after_run=delete_after,
                )
            except ValueError as exc:
                return self.messages.cron.error_prefix.format(message=str(exc))
            return self.messages.cron.created_job.format(name=job.name, job_id=job.id)

        if action == "list":
            jobs = service.list_jobs(include_disabled=True)
            return render_cron_jobs(jobs, self.messages.cron, default_timezone=self._cron_default_timezone())

        if action in {"pause", "disable"}:
            if not args:
                return self.messages.cron.error_job_id_required_pause
            job_id = args[0]
            if service.pause_job(job_id):
                return self.messages.cron.paused_job.format(job_id=job_id)
            return self.messages.cron.job_not_found_or_paused.format(job_id=job_id)

        if action in {"enable", "resume"}:
            if not args:
                return self.messages.cron.error_job_id_required_enable
            job_id = args[0]
            if service.enable_job(job_id):
                return self.messages.cron.enabled_job.format(job_id=job_id)
            return self.messages.cron.job_not_found_or_enabled.format(job_id=job_id)

        if action in {"run", "trigger"}:
            if not args:
                return self.messages.cron.error_job_id_required_run
            job_id = args[0]
            if await service.run_job(job_id):
                return self.messages.cron.ran_job.format(job_id=job_id)
            return self.messages.cron.job_not_found.format(job_id=job_id)

        if action in {"remove", "rm", "delete"}:
            if not args:
                return self.messages.cron.error_job_id_required_remove
            job_id = args[0]
            if service.remove_job(job_id):
                return self.messages.cron.removed_job.format(job_id=job_id)
            return self.messages.cron.job_not_found.format(job_id=job_id)

        return self._cron_help_text()

    @staticmethod
    def _parse_task_command(text: str | None) -> tuple[str, list[str]]:
        """Parse the task command into an action and remaining args."""
        try:
            parts = shlex.split((text or "").strip())
        except ValueError:
            return "error", []
        if not parts:
            return "help", []
        args = parts[1:]
        if not args:
            return "help", []
        return args[0].lower(), args[1:]

    async def _handle_task_command(self, session_chat_id: str, text: str | None) -> str:
        """Handle immediate task management commands for the active session."""
        action, args = self._parse_task_command(text)
        if action == "error":
            return self._task_help_text()
        if action in {"help", "--help", "-h"}:
            return self._task_help_text()

        show_task = getattr(self.agent, "show_active_task", None)
        show_task_full = getattr(self.agent, "show_active_task_full", None)
        show_history = getattr(self.agent, "show_active_task_history", None)
        set_task = getattr(self.agent, "set_active_task_from_text", None)
        mark_status = getattr(self.agent, "mark_active_task_status", None)
        reset_task = getattr(self.agent, "reset_active_task", None)
        activate_task = getattr(self.agent, "activate_active_task", None)
        block_task = getattr(self.agent, "block_active_task", None)
        wait_task = getattr(self.agent, "wait_on_active_task", None)
        reopen_task = getattr(self.agent, "reopen_active_task", None)
        current_step_task = getattr(self.agent, "set_active_task_current_step", None)
        complete_step_task = getattr(self.agent, "complete_active_task_step", None)
        next_step_task = getattr(self.agent, "set_active_task_next_step", None)
        advance_task = getattr(self.agent, "advance_active_task", None)

        if action in {"show", "status"}:
            if not callable(show_task):
                return self.messages.task.unavailable
            if args and args[0].lower() in {"full", "raw"}:
                if not callable(show_task_full):
                    return self.messages.task.unavailable
                rendered = await show_task_full(session_chat_id)
            else:
                rendered = await show_task(session_chat_id)
            return rendered or self.messages.task.no_active_task

        if action in {"history", "log"}:
            if not callable(show_history):
                return self.messages.task.unavailable
            limit = 10
            if args:
                try:
                    limit = int(args[0])
                except ValueError:
                    return self.messages.task.error_history_limit
                if limit <= 0:
                    return self.messages.task.error_history_limit
            rendered = await show_history(session_chat_id, limit=limit)
            return rendered or self.messages.task.no_history

        if action in {"reset", "clear"}:
            if not callable(show_task) or not callable(reset_task):
                return self.messages.task.unavailable
            rendered = await show_task(session_chat_id)
            if not rendered:
                return self.messages.task.no_active_task
            await reset_task(session_chat_id)
            return self.messages.task.reset_done

        if action == "done":
            if not callable(mark_status):
                return self.messages.task.unavailable
            rendered = await mark_status(session_chat_id, "done")
            if not rendered:
                return self.messages.task.no_active_task
            return f"{self.messages.task.marked_done}\n\n{rendered}"

        if action in {"activate", "resume"}:
            if not callable(activate_task):
                return self.messages.task.unavailable
            rendered = await activate_task(session_chat_id)
            if not rendered:
                return self.messages.task.no_active_task
            return f"{self.messages.task.marked_active}\n\n{rendered}"

        if action == "reopen":
            if not callable(reopen_task):
                return self.messages.task.unavailable
            rendered = await reopen_task(session_chat_id)
            if not rendered:
                return self.messages.task.no_active_task
            return f"{self.messages.task.reopened}\n\n{rendered}"

        if action in {"cancel", "cancelled"}:
            if not callable(mark_status):
                return self.messages.task.unavailable
            rendered = await mark_status(session_chat_id, "cancelled")
            if not rendered:
                return self.messages.task.no_active_task
            return f"{self.messages.task.marked_cancelled}\n\n{rendered}"

        if action == "block":
            if not callable(block_task):
                return self.messages.task.unavailable
            reason = " ".join(args).strip()
            if not reason:
                return self.messages.task.error_block_usage
            rendered = await block_task(session_chat_id, reason)
            if not rendered:
                return self.messages.task.no_active_task
            return f"{self.messages.task.marked_blocked}\n\n{rendered}"

        if action == "wait":
            if not callable(wait_task):
                return self.messages.task.unavailable
            question = " ".join(args).strip()
            if not question:
                return self.messages.task.error_wait_usage
            rendered = await wait_task(session_chat_id, question)
            if not rendered:
                return self.messages.task.no_active_task
            return f"{self.messages.task.marked_waiting}\n\n{rendered}"

        if action == "step":
            if not callable(current_step_task):
                return self.messages.task.unavailable
            step_text = " ".join(args).strip()
            if not step_text:
                return self.messages.task.error_step_usage
            rendered = await current_step_task(session_chat_id, step_text)
            if not rendered:
                return self.messages.task.no_active_task
            return f"{self.messages.task.updated_current_step}\n\n{rendered}"

        if action == "complete":
            if not callable(complete_step_task):
                return self.messages.task.unavailable
            next_step_override = " ".join(args).strip() or None
            rendered = await complete_step_task(session_chat_id, next_step_override)
            if rendered is None:
                return self.messages.task.no_active_task
            return f"{self.messages.task.completed_current_step}\n\n{rendered}"

        if action == "next":
            if args:
                if not callable(next_step_task):
                    return self.messages.task.unavailable
                step_text = " ".join(args).strip()
                rendered = await next_step_task(session_chat_id, step_text)
                if not rendered:
                    return self.messages.task.no_active_task
                return f"{self.messages.task.updated_next_step}\n\n{rendered}"
            if not callable(advance_task):
                return self.messages.task.unavailable
            rendered = await advance_task(session_chat_id)
            if rendered is None:
                if not callable(show_task):
                    return self.messages.task.unavailable
                current = await show_task(session_chat_id)
                if current is None:
                    return self.messages.task.no_active_task
                return self.messages.task.no_next_step
            return f"{self.messages.task.advanced_to_next_step}\n\n{rendered}"

        if action == "set":
            if not callable(set_task):
                return self.messages.task.unavailable
            task_text = " ".join(args).strip()
            if not task_text:
                return self.messages.task.error_set_usage
            rendered = await set_task(session_chat_id, task_text)
            if not rendered:
                return self.messages.task.error_set_usage
            return f"{self.messages.task.set_done}\n\n{rendered}"

        return self._task_help_text()

    async def _publish_stop_response(
        self,
        *,
        channel: str,
        transport_chat_id: str,
        session_chat_id: str,
        cancelled: int,
    ) -> None:
        """Publish the acknowledgement for an immediate stop command."""
        content = self.messages.queue.stop_cancelled if cancelled > 0 else self.messages.queue.stop_idle
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                content=content,
            )
        )

    async def _publish_reset_response(
        self,
        *,
        channel: str,
        transport_chat_id: str,
        session_chat_id: str,
        cancelled: int,
    ) -> None:
        """Publish the acknowledgement for an immediate reset command."""
        content = (
            self.messages.queue.reset_done_with_cancelled
            if cancelled > 0
            else self.messages.queue.reset_done
        )
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                content=content,
            )
        )

    async def _publish_cron_response(
        self,
        *,
        channel: str,
        transport_chat_id: str,
        session_chat_id: str,
        content: str,
    ) -> None:
        """Publish the acknowledgement for an immediate cron command."""
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                content=content,
            )
        )

    async def _publish_task_response(
        self,
        *,
        channel: str,
        transport_chat_id: str,
        session_chat_id: str,
        content: str,
    ) -> None:
        """Publish the acknowledgement for an immediate task command."""
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                content=content,
            )
        )

    def unregister_response_handler(self, channel: str) -> None:
        """Remove the outbound response handler for a channel."""
        normalized_channel = self.normalize_channel(channel)
        self._response_handlers.pop(normalized_channel, None)

    def unregister_run_event_handler(self, channel: str) -> None:
        """Remove the run event handler for a channel."""
        normalized_channel = self.normalize_channel(channel)
        self._run_event_handlers.pop(normalized_channel, None)
    
    async def enqueue(self, user_message: UserMessage) -> None:
        """
        把訊息加入 inbound queue
        
        參數：
            user_message: 統一格式的訊息
        """
        channel = self.normalize_channel(user_message.channel)
        transport_chat_id = (user_message.chat_id or "default").strip() or "default"
        session_chat_id = user_message.session_chat_id or self.build_session_chat_id(channel, transport_chat_id)
        metadata = dict(user_message.metadata or {})

        if self.is_stop_command(user_message.text):
            cancelled = await self.cancel_chat(session_chat_id)
            await self._publish_stop_response(
                channel=channel,
                transport_chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                cancelled=cancelled,
            )
            return

        if self.is_reset_command(user_message.text):
            cancelled = await self.cancel_chat(session_chat_id)
            await self.agent.reset_history(session_chat_id)
            await self._publish_reset_response(
                channel=channel,
                transport_chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                cancelled=cancelled,
            )
            return

        if self.is_cron_command(user_message.text):
            response_text = await self._handle_cron_command(session_chat_id, user_message.text)
            await self._publish_cron_response(
                channel=channel,
                transport_chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                content=response_text,
            )
            return

        if self.is_task_command(user_message.text):
            response_text = await self._handle_task_command(session_chat_id, user_message.text)
            await self._publish_task_response(
                channel=channel,
                transport_chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                content=response_text,
            )
            return

        inbound = InboundMessage(
            channel=channel,
            sender_id=user_message.sender_id or user_message.sender or "unknown",
            sender_name=user_message.sender_name,
            chat_id=transport_chat_id,
            session_chat_id=session_chat_id,
            content=user_message.text,
            images=list(user_message.images or []),
            audios=list(user_message.audios or []),
            videos=list(user_message.videos or []),
            metadata=metadata,
            raw=user_message.raw,
        )
        await self.bus.publish_inbound(inbound)
    
    async def enqueue_raw(
        self,
        content: str,
        chat_id: str = "default",
        channel: str = "cli",
        sender_id: str = "user",
        sender_name: str | None = None,
        images: list[str] | None = None,
        audios: list[str] | None = None,
        videos: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        raw: Any = None,
        session_chat_id: str | None = None,
    ) -> None:
        """
        直接發送原始訊息到 inbound queue（不需 UserMessage 格式）
        
        參數：
            content: 訊息內容
            chat_id: 聊天室 ID
            channel: 頻道名稱
            sender_id: 發送者 ID
            metadata: 額外資料
        """
        await self.enqueue(
            UserMessage(
                text=content,
                channel=channel,
                chat_id=chat_id,
                session_chat_id=session_chat_id,
                sender_id=sender_id,
                sender_name=sender_name,
                images=images,
                audios=audios,
                videos=videos,
                metadata=dict(metadata or {}),
                raw=raw,
            )
        )
    
    async def _process_message(self, inbound: InboundMessage) -> None:
        """
        處理單一訊息（會spawn成独立task）
        
        參數：
            inbound: InboundMessage
        """
        transport_chat_id = inbound.chat_id
        session_chat_id = inbound.session_chat_id or self.build_session_chat_id(inbound.channel, transport_chat_id)
        
        try:
            # 取得或建立對話
            self.get_or_create_conversation(session_chat_id)
            
            # 轉換成 UserMessage 給 Agent
            user_message = UserMessage(
                text=inbound.content,
                channel=inbound.channel,
                chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                sender_id=inbound.sender_id,
                sender_name=inbound.sender_name,
                images=inbound.images or None,
                audios=inbound.audios or None,
                videos=inbound.videos or None,
                metadata=dict(inbound.metadata),
                raw=inbound.raw,
            )
            
            # 把訊息傳給 Agent 處理
            response = await self.agent.process(user_message)
            response_channel = response.channel if response.channel and response.channel != "unknown" else inbound.channel
            
            # 放到 outbound queue（而不是直接發送）
            outbound = OutboundMessage(
                channel=response_channel,
                chat_id=response.chat_id or transport_chat_id,
                session_chat_id=response.session_chat_id or session_chat_id,
                content=response.text,
                metadata=dict(response.metadata or {}),
                raw=response.raw,
            )
            await self.bus.publish_outbound(outbound)
                
        except asyncio.CancelledError:
            # Task 被取消時優雅退出
            pass
        except Exception as e:
            logger.exception(f"[{session_chat_id}] 處理訊息時發生錯誤: {e}")
            # 發送錯誤訊息到 outbound
            outbound = OutboundMessage(
                channel=inbound.channel,
                chat_id=transport_chat_id,
                session_chat_id=session_chat_id,
                content=f"抱歉，處理您的訊息時發生錯誤: {str(e)[:100]}"
            )
            await self.bus.publish_outbound(outbound)
            if hasattr(self, 'on_error'):
                await self.on_error(session_chat_id, str(e))

    async def _run_session_message(
        self,
        inbound: InboundMessage,
        session_chat_id: str,
        previous_task: asyncio.Task | None,
    ) -> None:
        """Serialize processing within one session while keeping sessions concurrent."""
        if previous_task is not None:
            try:
                await previous_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        await self._process_message(inbound)
    
    async def _consume_outbound(self) -> None:
        """
        消費 outbound queue 的任務（獨立運作）
        不斷從 outbound 取訊息並依 channel 分派
        """
        while self.running:
            try:
                outbound = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0
                )

                normalized_channel = self.normalize_channel(outbound.channel)
                response = AssistantMessage(
                    text=outbound.content,
                    channel=normalized_channel,
                    chat_id=outbound.chat_id,
                    session_chat_id=outbound.session_chat_id,
                    metadata=dict(outbound.metadata),
                    raw=outbound.raw,
                )

                handler = self._response_handlers.get(normalized_channel)
                if handler is not None:
                    await handler(response, normalized_channel, outbound.chat_id)
                    continue

                if hasattr(self, "on_response"):
                    await self.on_response(response, normalized_channel, outbound.chat_id)
                    continue

                logger.warning("No outbound handler registered for channel '{}'", normalized_channel)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.exception(f"Outbound consumer 發生錯誤: {e}")

    async def _consume_run_events(self) -> None:
        """Consume structured run events and dispatch them to channel handlers."""
        while self.running:
            try:
                event = await asyncio.wait_for(
                    self.bus.consume_run_event(),
                    timeout=1.0,
                )
                normalized_channel = self.normalize_channel(event.channel)
                handler = self._run_event_handlers.get(normalized_channel)
                if handler is None:
                    continue
                await handler(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.exception(f"Run event consumer 發生錯誤: {e}")
    
    async def process_queue(self) -> None:
        """
        處理 inbound queue 中的訊息（非同步迴圈）
        
        不同 session 會並行處理；同一個 session 會依序處理
        結果會丟到 outbound queue，由 _consume_outbound 發送
        """
        self.running = True
        
        # 啟動 outbound 消費者
        self._outbound_task = asyncio.create_task(self._consume_outbound())
        self._run_event_task = asyncio.create_task(self._consume_run_events())
        
        while self.running:
            try:
                # 等待 inbound 訊息（超時 1 秒，檢查是否要停止）
                try:
                    inbound = await asyncio.wait_for(
                        self.bus.consume_inbound(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                chat_id = inbound.session_chat_id or self.build_session_chat_id(inbound.channel, inbound.chat_id)
                
                previous_task = self._session_tails.get(chat_id)
                task = asyncio.create_task(self._run_session_message(inbound, chat_id, previous_task))
                self._session_tails[chat_id] = task
                
                # 追蹤這個 chat_id 的 tasks
                if chat_id not in self._active_tasks:
                    self._active_tasks[chat_id] = []
                self._active_tasks[chat_id].append(task)
                
                # Task 完成後自動清理
                task.add_done_callback(
                    lambda t, cid=chat_id: self._active_tasks.get(cid, []).remove(t) 
                    if t in self._active_tasks.get(cid, []) else None
                )
                task.add_done_callback(
                    lambda t, cid=chat_id: self._session_tails.pop(cid, None)
                    if self._session_tails.get(cid) is t else None
                )
                
            except asyncio.CancelledError:
                self.running = False
                break
            except Exception as e:
                logger.exception(f"Inbound consumer 發生錯誤: {e}")
    
    async def cancel_chat(self, chat_id: str, channel: str | None = None) -> int:
        """
        取消特定 chat_id 的所有正在處理的任務
        
        參數：
            chat_id: 聊天室 ID
        
        回傳：
            int: 被取消的任務數量
        """
        session_chat_id = self.resolve_session_chat_id(chat_id, channel)
        tasks = self._active_tasks.pop(session_chat_id, [])
        cancelled = 0
        for task in tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        return cancelled
    
    async def cancel_all(self) -> int:
        """
        取消所有正在處理的任務
        
        回傳：
            int: 被取消的任務數量
        """
        total = 0
        chat_ids = list(self._active_tasks.keys())
        for chat_id in chat_ids:
            total += await self.cancel_chat(chat_id)
        return total
    
    async def stop(self) -> None:
        """停止處理並取消所有進行中的任務"""
        self.running = False
        
        # 取消 outbound 消費者
        if self._outbound_task and not self._outbound_task.done():
            self._outbound_task.cancel()
            try:
                await self._outbound_task
            except asyncio.CancelledError:
                pass

        if self._run_event_task and not self._run_event_task.done():
            self._run_event_task.cancel()
            try:
                await self._run_event_task
            except asyncio.CancelledError:
                pass
        
        # 取消所有處理中的任務
        await self.cancel_all()
    
    async def reset_conversation(self, chat_id: str, channel: str | None = None) -> None:
        """
        重置特定對話的歷史
        
        參數：
            chat_id: 聊天室 ID
        """
        # 先取消這個chat正在處理的任務
        session_chat_id = self.resolve_session_chat_id(chat_id, channel)
        await self.cancel_chat(session_chat_id)
        # 讓 Agent 去清除 Storage 裡的歷史
        await self.agent.reset_history(session_chat_id)
    
    @property
    def queue_sizes(self) -> tuple[int, int]:
        """回傳 (inbound_size, outbound_size)"""
        return (self.bus.inbound_size, self.bus.outbound_size)


# ============================================
# 使用範例（Console 版本）
# ============================================

"""
# 建立 Queue 版本（Bus 版）

import asyncio
from ..agent import AgentLoop
from ..config import Config
from ..llms import OpenAILLM
from ..storage import MemoryStorage
from .dispatcher import MessageQueue
from .message import UserMessage

async def main():
    # 1. 建立 Storage（可替換）
    storage = MemoryStorage()
    
    # 2. 建立 LLM
    llm = OpenAILLM(
        api_key="your-key",
        default_model="gpt-4o-mini"
    )
    
    # 3. 建立 Agent（傳入 storage）
    config = Config.load_agent_template_config()
    agent = AgentLoop(config, llm, storage)
    
    # 4. 建立 Queue（Bus 版本）
    mq = MessageQueue(agent)
    
    # 5. 定義收到回覆時要做什麼
    async def on_response(response, channel, chat_id):
        logger.info(f"[{channel}] 🤖: {response.text}")

    mq.register_response_handler("cli", on_response)
    
    # 6. 啟動處理迴圈（在背景執行）
    processor = asyncio.create_task(mq.process_queue())
    
    # 7. 主執行緒處理輸入
    while True:
        line = input("\n你: ").strip()
        
        if line.lower() == "/exit":
            await mq.stop()
            break
        
        if line.lower() == "/reset":
            await mq.reset_conversation("default")
            logger.info("歷史已清除")
            continue
        
        if line.lower() == "/queues":
            inbound, outbound = mq.queue_sizes
            logger.info(f"Queue sizes: inbound={inbound}, outbound={outbound}")
            continue
        
        # 解析 chat_id
        if line.startswith("@"):
            parts = line[1:].split(" ", 1)
            chat_id = parts[0]
            text = parts[1] if len(parts) > 1 else ""
        else:
            chat_id = "default"
            text = line
        
        # 加入佇列（現在用 enqueue_raw 更方便）
        await mq.enqueue_raw(content=text, chat_id=chat_id, channel="cli")

asyncio.run(main())
"""
