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

import json
from contextvars import ContextVar
import re
import time
from pathlib import Path
from typing import Any

from ..bus.message import UserMessage, AssistantMessage
from ..llms import LLMProvider, ChatMessage
from ..storage import StorageProvider, StoredMessage
from ..context.builder import ContextBuilder
from ..documents.memory import MemoryStore, consolidate
from ..documents.user_profile import UserProfileConsolidator, UserProfileStore
from ..search.base import SearchStore
from ..tools import (
    ToolRegistry,
    ReadFileTool,
    WriteFileTool,
    ListDirTool,
    EditFileTool,
    ExecTool,
    SearchHistoryTool,
    SearchKnowledgeTool,
    WebSearchTool,
    WebFetchTool,
    ReadSkillTool,
)
from ..utils.log import logger
from ..config import AgentConfig, MemoryConfig, ToolsConfig, LogConfig, SearchConfig, UserProfileConfig


INTERNAL_CONTROL_BLOCK_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>.*?</(?:think|thinking)>",
    re.IGNORECASE | re.DOTALL,
)

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
    EMPTY_RESPONSE_FALLBACK = "抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。"

    def _get_system_prompt_log_path(self) -> Path:
        """Return the daily file path for full system prompt logs."""
        logs_root = (self.app_home or Path.home() / ".opensprite") / "logs" / "system-prompts"
        logs_root.mkdir(parents=True, exist_ok=True)
        filename = f"system-prompts-{time.strftime('%Y-%m-%d')}.log"
        return logs_root / filename

    def _write_full_system_prompt_log(self, log_id: str, content: str) -> None:
        """Append the full system prompt to a dedicated log file."""
        try:
            log_path = self._get_system_prompt_log_path()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            entry = (
                f"[{timestamp}] [{log_id}] prompt.system.begin\n"
                f"{content}\n"
                f"[{timestamp}] [{log_id}] prompt.system.end\n\n"
            )
            with log_path.open("a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"[{log_id}] prompt.file.error | error={e}")

    @staticmethod
    def _sanitize_response_content(content: str) -> str:
        """Remove provider-internal control blocks from visible replies."""
        cleaned = INTERNAL_CONTROL_BLOCK_RE.sub("", content or "")
        return cleaned.strip()

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
            text = INTERNAL_CONTROL_BLOCK_RE.sub("", text)
            text = LOG_WHITESPACE_RE.sub(" ", text).strip() or "<multimodal>"
            suffix_parts = []
            if image_count:
                suffix_parts.append(f"images={image_count}")
            if other_items:
                suffix_parts.append(f"items={other_items}")
            if suffix_parts:
                text = f"{text} [{' '.join(suffix_parts)}]"
        else:
            text = INTERNAL_CONTROL_BLOCK_RE.sub("", str(content or ""))
            text = LOG_WHITESPACE_RE.sub(" ", text).strip()

        if not text:
            return "<empty>"
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

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
    ):
        ...
        self.memory_config = memory_config or MemoryConfig()
        self.tools_config = tools_config or ToolsConfig()
        self.log_config = log_config or LogConfig()
        self.search_config = search_config or SearchConfig()
        self.user_profile_config = user_profile_config or UserProfileConfig()
        self.search_store = search_store
        self.provider = provider
        self._current_chat_id: ContextVar[str | None] = ContextVar("current_chat_id", default=None)

        # 如果沒給 storage，用記憶體 storage
        if storage is None:
            from ..storage import MemoryStorage
            storage = MemoryStorage()
        self.storage = storage

        self.app_home: Path | None = None
        self.tool_workspace: Path | None = None

        # 如果沒給 context_builder，自動建立 app home / bootstrap / workspace
        self._context_builder = context_builder
        if context_builder is None:
            try:
                from ..context.paths import get_app_home, get_tool_workspace, sync_templates
                from ..context import FileContextBuilder

                self.app_home = get_app_home()
                self.tool_workspace = get_tool_workspace(self.app_home)
                sync_templates(self.app_home)
                context_builder = FileContextBuilder(
                    app_home=self.app_home,
                    tool_workspace=self.tool_workspace,
                )
                self._context_builder = context_builder
            except Exception as e:
                raise RuntimeError(f"無法建立 ContextBuilder: {e}")
        else:
            self.app_home = getattr(context_builder, "app_home", None)
            self.tool_workspace = getattr(context_builder, "tool_workspace", None)
            if self.tool_workspace is None:
                self.tool_workspace = getattr(context_builder, "workspace", Path.cwd())

        # Tools
        self.tools = tools or ToolRegistry()
        if not self.tools.tool_names:
            self._register_default_tools()

        # Memory store (long-term memory)
        memory_dir = getattr(self._context_builder, "memory_dir", Path.cwd() / "memory")
        self.memory = MemoryStore(memory_dir)
        self.user_profile: UserProfileConsolidator | None = None
        # Register save_memory tool
        self._register_memory_tool()

        if self.app_home is not None:
            from ..context.paths import get_user_profile_file, get_user_profile_state_file

            profile_store = UserProfileStore(
                user_profile_file=get_user_profile_file(self.app_home),
                state_file=get_user_profile_state_file(self.app_home),
            )
            self.user_profile = UserProfileConsolidator(
                storage=self.storage,
                provider=self.provider,
                model=self.provider.get_default_model(),
                profile_store=profile_store,
                threshold=self.user_profile_config.threshold,
                lookback_messages=self.user_profile_config.lookback_messages,
                enabled=self.user_profile_config.enabled,
            )

    def _register_memory_tool(self) -> None:
        """Register the save_memory tool."""
        # Dynamic tool for saving memory
        from ..tools.base import Tool
        
        class SaveMemoryTool(Tool):
            name = "save_memory"
            description = "Save important information to long-term memory. Include all existing facts plus new ones."
            parameters = {
                "type": "object",
                "properties": {
                    "memory_update": {"type": "string", "description": "Updated memory as markdown"}
                },
                "required": ["memory_update"]
            }
            
            def __init__(self, memory_store: MemoryStore, get_chat_id: callable):
                self.memory_store = memory_store
                self.get_chat_id = get_chat_id
            
            async def execute(self, memory_update: str, **kwargs: Any) -> str:
                chat_id = self.get_chat_id()
                if not chat_id:
                    return "Error: current chat_id is unavailable. save_memory requires an active chat context."
                current = self.memory_store.read(chat_id)
                if memory_update != current:
                    self.memory_store.write(chat_id, memory_update)
                    return f"Memory saved ({len(memory_update)} chars)"
                return "Memory unchanged"
        
        # Pass a lambda that returns current chat_id
        self.tools.register(SaveMemoryTool(self.memory, self._get_current_chat_id))

    def _get_current_chat_id(self) -> str | None:
        """Return the current task-local chat id."""
        return self._current_chat_id.get()

    def _get_current_workspace(self) -> Path:
        """Resolve the current task-local workspace."""
        from ..context.paths import get_chat_workspace

        workspace_root = self.tool_workspace or getattr(self._context_builder, "workspace", Path.cwd())
        chat_id = self._get_current_chat_id() or "default"
        return get_chat_workspace(chat_id, workspace_root=workspace_root)

    def _register_default_tools(self) -> None:
        """
        註冊代理人的預設工具。
        
        Register default tools for the agent.
        
        註冊檔案系統工具、Shell 執行、網頁搜尋和網頁抓取。
        Registers filesystem tools, shell execution, web search, and web fetch.
        """
        skills_loader = getattr(self._context_builder, "skills_loader", None)
        
        # 檔案工具
        self.tools.register(ReadFileTool(workspace_resolver=self._get_current_workspace, skills_loader=skills_loader))
        self.tools.register(WriteFileTool(workspace_resolver=self._get_current_workspace))
        self.tools.register(EditFileTool(workspace_resolver=self._get_current_workspace))
        self.tools.register(ListDirTool(workspace_resolver=self._get_current_workspace))
        
        # 技能工具
        if skills_loader:
            self.tools.register(ReadSkillTool(skills_loader=skills_loader))
        
        # 執行命令
        self.tools.register(ExecTool(workspace_resolver=self._get_current_workspace))
        
        # 網路工具
        web_search_config = {}
        web_fetch_config = {}
        if hasattr(self.tools_config, 'web_search'):
            web_search_config = self.tools_config.web_search or {}
        if hasattr(self.tools_config, 'web_fetch'):
            web_fetch_config = self.tools_config.web_fetch or {}

        self.tools.register(WebSearchTool(config=web_search_config))
        self.tools.register(WebFetchTool(
            max_chars=web_fetch_config.get("max_chars", 50000),
            timeout=web_fetch_config.get("timeout", 30),
            prefer_trafilatura=web_fetch_config.get("prefer_trafilatura", True),
            firecrawl_api_key=web_fetch_config.get("firecrawl_api_key")
        ))

        # Delegate Tool
        from ..tools.delegate import DelegateTool
        self.tools.register(DelegateTool(run_subagent=self.run_subagent))

        if self.search_store is not None:
            self.tools.register(
                SearchHistoryTool(
                    store=self.search_store,
                    get_chat_id=self._get_current_chat_id,
                    default_limit=self.search_config.history_top_k,
                )
            )
            self.tools.register(
                SearchKnowledgeTool(
                    store=self.search_store,
                    get_chat_id=self._get_current_chat_id,
                    default_limit=self.search_config.knowledge_top_k,
                )
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
        # 從 storage 取訊息（使用 max_history 限制數量）
        stored_messages = await self.storage.get_messages(
            chat_id, 
            limit=self.memory_config.max_history
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
                self._write_full_system_prompt_log(log_id, str(system_msg.get("content", "")))
                max_chars = 240
                if self.log_config.log_system_prompt_lines > 0:
                    max_chars = max(120, self.log_config.log_system_prompt_lines * 120)
                logger.info(
                    f"[{log_id}] prompt.system | {self._format_log_preview(system_msg.get('content', ''), max_chars=max_chars)}"
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
    ) -> str:
        """Run the shared LLM execution loop for main and delegated agents."""
        tools = None
        if allow_tools and self.tools.tool_names:
            tools = self.tools.get_definitions()
            logger.info(f"[{log_id}] tools.enabled | names={', '.join(self.tools.tool_names)}")

        tool_results_history = []

        for iteration in range(self.tools_config.max_tool_iterations):
            logger.info(
                f"[{log_id}] llm.request | iter={iteration + 1} messages={len(chat_messages)} "
                f"tools={'on' if tools else 'off'} tail={self._summarize_messages(chat_messages)}"
            )
            try:
                response = await self.provider.chat(
                    messages=chat_messages,
                    tools=tools,
                )
            except Exception:
                logger.exception(
                    f"[{log_id}] llm.error | iter={iteration + 1} messages={len(chat_messages)} "
                    f"tools={'on' if tools else 'off'} tail={self._summarize_messages(chat_messages)}"
                )
                raise

            raw_content = response.content or ""
            response.content = self._sanitize_response_content(raw_content)
            logger.info(
                f"[{log_id}] llm.response | iter={iteration + 1} model={response.model} raw_len={len(raw_content)} "
                f"visible_len={len(response.content)} tool_calls={len(response.tool_calls or [])} "
                f"preview={self._format_log_preview(response.content)}"
            )
            if raw_content and not response.content:
                logger.warning(
                    f"[{log_id}] llm.sanitized-empty | iter={iteration + 1} raw_preview={self._format_log_preview(raw_content, max_chars=240)}"
                )

            if response.tool_calls:
                if not tools:
                    logger.warning(
                        f"[{log_id}] llm.tool-calls-ignored | iter={iteration + 1} count={len(response.tool_calls)} tools=off"
                    )
                    if not response.content:
                        return self.EMPTY_RESPONSE_FALLBACK

                    return response.content

                logger.info(f"[{log_id}] llm.tool-calls | iter={iteration + 1} count={len(response.tool_calls)}")

                tool_calls_api = []
                for tc in response.tool_calls:
                    tool_calls_api.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    })

                chat_messages.append(ChatMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=tool_calls_api
                ))

                for tc in response.tool_calls:
                    tool_name = tc.name
                    tool_args = tc.arguments
                    args_preview = self._format_log_preview(json.dumps(tool_args, ensure_ascii=False), max_chars=200)
                    logger.info(f"[{log_id}] tool.run | id={tc.id} name={tool_name} args={args_preview}")

                    result = await self.tools.execute(tool_name, tool_args)
                    logger.info(
                        f"[{log_id}] tool.result | name={tool_name} preview={self._format_log_preview(result, max_chars=200)}"
                    )

                    tool_results_history.append(f"{tool_name}: {result[:200]}")
                    chat_messages.append(ChatMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id
                    ))

                    if tool_result_chat_id is not None:
                        await self._save_message(tool_result_chat_id, "tool", result, tool_name=tool_name)
                        if self.search_store is not None:
                            try:
                                await self.search_store.index_tool_result(tool_result_chat_id, tool_name, tool_args, result)
                            except Exception as e:
                                logger.warning("[{}] Failed to index tool result for search: {}", tool_result_chat_id, e)

                continue

            if not response.content:
                logger.warning(f"[{log_id}] llm.empty-visible-response | using_fallback=true")
                return self.EMPTY_RESPONSE_FALLBACK

            return response.content

        logger.warning(f"[{log_id}] llm.max-iterations | limit={self.tools_config.max_tool_iterations}")

        history_msg = ""
        if tool_results_history:
            history_msg = f"\n\n我嘗試了以下工具但未能完成任務：\n" + "\n".join(f"- {r}" for r in tool_results_history[-5:])

        return f"我嘗試完成你的請求，但超過了最大迭代次數（{self.tools_config.max_tool_iterations}次）。請將任務拆分為較小的步驟。{history_msg}"

    async def call_llm(
        self,
        chat_id: str,
        current_message: str,
        channel: str | None = None,
        allow_tools: bool = True,
        user_images: list[str] | None = None,
    ) -> str:
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
            LLM 的文字回應。The LLM's text response.
        
        Raises:
            RuntimeError: 如果工具執行失敗或超過最大迭代次數。
                          If tool execution fails or exceeds max iterations.
        """
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
        full_messages = self._context_builder.build_messages(
            history=history_dicts,
            current_message=current_message,
            current_images=user_images,
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
        return await self._execute_messages(
            chat_id,
            chat_messages,
            allow_tools=allow_tools,
            tool_result_chat_id=chat_id if allow_tools else None,
        )

    async def run_subagent(self, task: str, prompt_type: str = "writer") -> str:
        """Run a delegated subagent task through the shared execution core."""
        from .subagent_builder import SubagentMessageBuilder
        from ..subagent_prompts import ALL_SUBAGENTS

        if prompt_type not in ALL_SUBAGENTS:
            available = ", ".join(ALL_SUBAGENTS)
            return f"Error: unknown subagent type '{prompt_type}'. Available: {available}"

        parent_chat_id = self._get_current_chat_id() or "default"
        log_id = f"{parent_chat_id}:subagent:{prompt_type}"
        workspace = self._get_current_workspace()

        subagent_builder = SubagentMessageBuilder()
        chat_messages = subagent_builder.build_messages(task, prompt_type=prompt_type, workspace=workspace)
        self._log_prepared_messages(
            log_id,
            [{"role": msg.role, "content": msg.content} for msg in chat_messages],
        )
        logger.info(
            f"[{log_id}] subagent.run | workspace={workspace} task={self._format_log_preview(task, max_chars=200)}"
        )
        return await self._execute_messages(log_id, chat_messages, allow_tools=False)

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

        user_metadata = {
            **dict(user_message.metadata or {}),
            "channel": channel,
            "transport_chat_id": user_message.chat_id,
            "sender_id": user_message.sender_id,
            "sender_name": user_message.sender_name,
            "images_count": len(user_message.images or []),
        }
        user_metadata = {key: value for key, value in user_metadata.items() if value is not None}

        token = self._current_chat_id.set(session_chat_id)
        try:
            # 1. 把使用者訊息存入 storage
            await self._save_message(session_chat_id, "user", user_message.text, metadata=user_metadata)

            # 2. 呼叫 LLM（傳入 channel 和圖片）
            logger.info(f"[{session_chat_id}] agent.run | status=processing")
            response = await self.call_llm(
                session_chat_id,
                current_message=user_message.text,
                channel=channel,
                user_images=user_message.images
            )
            
            logger.info(
                f"[{session_chat_id}] outbound | text={self._format_log_preview(response, max_chars=200)}"
            )

            assistant_metadata = {
                "channel": channel,
                "transport_chat_id": user_message.chat_id,
            }
            assistant_metadata = {key: value for key, value in assistant_metadata.items() if value is not None}

            # 3. 把 AI 回覆存入 storage
            await self._save_message(session_chat_id, "assistant", response, metadata=assistant_metadata)

            # 4. 檢查是否需要 consolidation
            await self._maybe_consolidate_memory(session_chat_id)
            await self._maybe_update_user_profile(session_chat_id)

            # 5. 回傳
            return AssistantMessage(
                text=response,
                channel=channel or "unknown",
                chat_id=user_message.chat_id,
                session_chat_id=session_chat_id,
                metadata=assistant_metadata,
            )
        except Exception:
            logger.exception(
                f"[{session_chat_id}] Agent.process failed: channel={channel}, "
                f"text_len={len(user_message.text or '')}, images={len(user_message.images or [])}"
            )
            raise
        finally:
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
        # Get total message count
        messages = await self.storage.get_messages(chat_id, limit=1000)
        message_count = len(messages)
        
        # Get last consolidated for this chat
        last_consolidated = await self.storage.get_consolidated_index(chat_id)
        
        # Check if we should consolidate
        unconsolidated = message_count - last_consolidated
        if unconsolidated >= self.memory_config.threshold:
            logger.info(f"[{chat_id}] memory.consolidate | pending={unconsolidated}")
            try:
                # Get messages to consolidate
                old_messages = messages[last_consolidated:]
                # Convert to dicts (handle both object and dict formats)
                msg_dicts = []
                for m in old_messages:
                    if isinstance(m, dict):
                        msg_dicts.append({"role": m.get("role", "?"), "content": m.get("content", "")})
                    else:
                        msg_dicts.append({"role": m.role, "content": m.content})
                
                success = await consolidate(
                    memory_store=self.memory,
                    chat_id=chat_id,
                    messages=msg_dicts,
                    provider=self.provider,
                    model=self.provider.get_default_model(),
                )
                if success:
                    await self.storage.set_consolidated_index(chat_id, message_count)
                    logger.info(f"[{chat_id}] memory.consolidated | total_messages={message_count}")
            except Exception as e:
                logger.error(f"[{chat_id}] memory.consolidate.error | error={e}")

    async def _maybe_update_user_profile(self, chat_id: str) -> None:
        """Check whether the global USER.md profile should be refreshed."""
        if self.user_profile is None:
            return

        try:
            await self.user_profile.maybe_update(chat_id)
        except Exception as e:
            logger.error(f"[{chat_id}] profile.update.error | error={e}")

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
                if self.search_store is not None:
                    try:
                        await self.search_store.clear_chat(c)
                    except Exception as e:
                        logger.warning("[{}] Failed to clear search index: {}", c, e)
