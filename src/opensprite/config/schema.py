"""opensprite/config/schema.py - 設定檔定義"""
import json
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..channels.registry import coerce_channel_instances, default_channel_instances


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    provider: str | None = None
    name: str | None = None
    auth_type: Literal["api_key", "openai_codex_oauth", "github_copilot_oauth"] = "api_key"
    api_mode: Literal["chat_completions", "responses"] | None = None
    api_key: str = ""
    model: str = ""
    base_url: str | None = None
    enabled: bool = False
    context_window_tokens: int | None = Field(default=None, ge=1)
    reasoning_enabled: bool = True
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = "medium"
    reasoning_max_tokens: int | None = Field(default=None, ge=1)
    reasoning_exclude: bool = False
    provider_sort: Literal["price", "throughput", "latency"] | None = None
    require_parameters: bool = False


class LLMsConfig(BaseModel):
    """LLM configuration with support for multiple providers."""

    providers: dict[str, ProviderConfig] = {}
    providers_file: str = "llm.providers.json"
    default: str | None = None
    api_key: str = ""
    model: str = ""
    base_url: str | None = None
    context_window_tokens: int | None = Field(default=None, ge=1)
    temperature: float
    max_tokens: int
    top_p: float = Field(ge=0.0, le=1.0)
    frequency_penalty: float = Field(ge=-2.0, le=2.0)
    presence_penalty: float = Field(ge=-2.0, le=2.0)
    # 主對話（ExecutionEngine）呼叫 LLM 時是否帶入 temperature / max_tokens / top_p / penalties
    pass_decoding_params: bool

    def get_active(self) -> ProviderConfig:
        """Get the active provider configuration."""
        if self.providers and self.default and self.default in self.providers:
            provider = self.providers[self.default]
            if provider.context_window_tokens is not None or self.context_window_tokens is None:
                return provider
            return ProviderConfig(
                **{
                    **provider.model_dump(),
                    "context_window_tokens": self.context_window_tokens,
                }
            )
        return ProviderConfig(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            enabled=True,
            context_window_tokens=self.context_window_tokens,
        )


class DocumentLlmConfig(BaseModel):
    """LLM 解碼參數：背景文件合併（MEMORY / RECENT_SUMMARY / USER profile）的 API 呼叫。"""

    pass_decoding_params: bool
    temperature: float
    max_tokens: int
    top_p: float | None
    frequency_penalty: float | None
    presence_penalty: float | None

    def decoding_kwargs(self) -> dict[str, Any]:
        """供 provider.chat(..., **kwargs) 使用；關閉時五個參數皆為 None。"""
        if self.pass_decoding_params:
            return {
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "top_p": self.top_p,
                "frequency_penalty": self.frequency_penalty,
                "presence_penalty": self.presence_penalty,
            }
        return {
            "temperature": None,
            "max_tokens": None,
            "top_p": None,
            "frequency_penalty": None,
            "presence_penalty": None,
        }


# 舊名稱保留，與 memory.llm 設定語意相同
MemoryLlmConfig = DocumentLlmConfig


class AgentConfig(BaseModel):
    """Agent configuration."""
    
    max_history: int
    history_token_budget: int
    context_compaction_enabled: bool
    context_compaction_threshold_ratio: float = Field(gt=0.0, le=1.0)
    context_compaction_min_messages: int = Field(ge=2)
    context_compaction_strategy: Literal["deterministic", "llm"]
    context_compaction_llm: DocumentLlmConfig
    # After the main reply, optionally run a quiet LLM pass to upsert skills (extra API cost).
    skill_review_enabled: bool
    skill_review_min_tool_calls: int = Field(ge=1)
    skill_review_max_tool_iterations: int = Field(ge=1, le=100)
    skill_review_transcript_messages: int = Field(ge=5, le=500)
    worktree_sandbox_enabled: bool = False


class StorageConfig(BaseModel):
    """Storage configuration."""

    type: Literal["memory", "sqlite"]
    path: str


class ChannelsConfig(BaseModel):
    """Channel instances keyed by channel_instance_id."""

    instances: dict[str, dict[str, Any]] = Field(default_factory=default_channel_instances)


class AgentMessagesConfig(BaseModel):
    empty_response_fallback: str = "抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。"
    llm_not_configured: str = (
        "尚未設定 LLM，請在 OpenSprite Web UI 的 Settings > Providers / Models 設定後再試。"
    )
    media_saved_ack: str = "已收到並保存媒體檔案。需要我分析內容時，請直接告訴我要看哪一個檔案。"


class QueueMessagesConfig(BaseModel):
    stop_cancelled: str = "已停止目前這段對話。"
    stop_idle: str = "目前沒有正在執行的對話可停止。"
    reset_done: str = "已重置目前這段對話。"
    reset_done_with_cancelled: str = "已重置目前這段對話。 進行中的任務也已停止。"


class CronMessagesConfig(BaseModel):
    help_text: str = (
        "排程命令:\n"
        "/cron add every <seconds> <message> [--no-deliver]\n"
        "/cron add at <iso-datetime> <message> [--no-deliver]\n"
        "/cron add cron \"<expr>\" [--tz <timezone>] <message> [--no-deliver]\n"
        "/cron list\n"
        "/cron pause <job_id>\n"
        "/cron enable <job_id>\n"
        "/cron run <job_id>\n"
        "/cron remove <job_id>\n"
        "/cron help"
    )
    unavailable: str = "排程功能目前不可用。"
    error_prefix: str = "Error: {message}"
    error_invalid_quoting: str = "Invalid quoting in /cron command"
    error_add_usage: str = "Usage: /cron add every <seconds> <message>"
    error_message_required: str = "A non-empty message is required"
    error_every_requires_integer: str = "every requires an integer number of seconds"
    error_every_requires_positive: str = "every requires a value greater than 0"
    error_tz_only_for_cron: str = "--tz can only be used with cron schedules"
    error_at_requires_iso: str = "at requires ISO format like 2026-04-10T09:00:00"
    error_unknown_schedule_mode: str = "Unknown schedule mode. Use every, at, or cron"
    error_job_id_required_pause: str = "Error: job_id is required. Usage: /cron pause <job_id>"
    error_job_id_required_enable: str = "Error: job_id is required. Usage: /cron enable <job_id>"
    error_job_id_required_run: str = "Error: job_id is required. Usage: /cron run <job_id>"
    error_job_id_required_remove: str = "Error: job_id is required. Usage: /cron remove <job_id>"
    error_manager_unavailable: str = "Error: cron manager is unavailable"
    error_no_active_session: str = "Error: no active session context"
    error_message_required_for_add: str = "Error: message is required for add"
    error_invalid_iso_datetime: str = "Error: invalid ISO datetime format '{value}'. Expected YYYY-MM-DDTHH:MM:SS"
    error_schedule_required: str = "Error: either every_seconds, cron_expr, or at is required"
    error_unknown_action: str = "Unknown action: {action}"
    no_jobs: str = "No scheduled jobs."
    jobs_header: str = "Scheduled jobs:"
    job_list_item: str = "- {name} (id: {job_id}, {timing})"
    next_run_label: str = "Next run: {timestamp}"
    created_job: str = "Created job '{name}' (id: {job_id})"
    removed_job: str = "Removed job {job_id}"
    paused_job: str = "Paused job {job_id}"
    enabled_job: str = "Enabled job {job_id}"
    ran_job: str = "Ran job {job_id}"
    job_not_found: str = "Job {job_id} not found"
    job_not_found_or_paused: str = "Job {job_id} not found or already paused"
    job_not_found_or_enabled: str = "Job {job_id} not found or already enabled"


class TaskMessagesConfig(BaseModel):
    help_text: str = (
        "任務命令:\n"
        "/task show [full]\n"
        "/task history [limit]\n"
        "/task set <task description>\n"
        "/task activate\n"
        "/task reopen\n"
        "/task block <reason>\n"
        "/task wait <question>\n"
        "/task step <current step>\n"
        "/task complete [next step]\n"
        "/task next [next step]\n"
        "/task done\n"
        "/task cancel\n"
        "/task reset\n"
        "/task help"
    )
    unavailable: str = "任務追蹤功能目前不可用。"
    no_active_task: str = "目前沒有進行中的任務。"
    no_history: str = "目前沒有任務歷程。"
    set_done: str = "已設定目前任務。"
    reset_done: str = "已清除目前的任務狀態。"
    marked_done: str = "已將目前任務標記為完成。"
    marked_cancelled: str = "已將目前任務標記為取消。"
    marked_blocked: str = "已將目前任務標記為阻塞。"
    marked_waiting: str = "已將目前任務標記為等待使用者。"
    marked_active: str = "已重新啟用目前任務。"
    reopened: str = "已重新開啟目前任務。"
    updated_current_step: str = "已更新目前步驟。"
    updated_next_step: str = "已更新下一步。"
    advanced_to_next_step: str = "已將下一步提升為目前步驟。"
    completed_current_step: str = "已完成目前步驟。"
    error_set_usage: str = "Error: task description is required. Usage: /task set <task description>"
    error_block_usage: str = "Error: reason is required. Usage: /task block <reason>"
    error_wait_usage: str = "Error: question is required. Usage: /task wait <question>"
    error_step_usage: str = "Error: current step is required. Usage: /task step <current step>"
    error_history_limit: str = "Error: limit must be a positive integer. Usage: /task history [limit]"
    no_next_step: str = "目前沒有可前進的下一步。"


class CuratorMessagesConfig(BaseModel):
    help_text: str = (
        "背景整理命令:\n"
        "/curator status\n"
        "/curator history [limit]\n"
        "/curator run [maintenance|skills|memory|recent_summary|user_profile|active_task]\n"
        "/curator pause\n"
        "/curator resume\n"
        "/curator help"
    )
    unavailable: str = "背景整理功能目前不可用。"
    error_history_limit: str = "Error: limit must be a positive integer. Usage: /curator history [limit]"
    error_run_usage: str = "Usage: /curator run [maintenance|skills|memory|recent_summary|user_profile|active_task]"
    invalid_scope: str = "Unknown curator scope: {scope}. Valid scopes: {scopes}"
    run_scheduled: str = "已排入背景整理。"
    run_rerun_scheduled: str = "背景整理目前執行中，已排入下一輪。"
    run_paused: str = "背景整理目前已暫停，請先恢復。"
    paused_done: str = "已暫停背景整理。"
    resumed_done: str = "已恢復背景整理。"
    history_header: str = "Curator 歷史:"
    history_empty: str = "尚無背景整理紀錄。"
    history_changed_label: str = "- 變更: {value}"
    history_summary_label: str = "- 摘要: {value}"
    history_error_label: str = "- 錯誤: {value}"
    status_header: str = "Curator 狀態:"
    status_label: str = "- 狀態: {state}"
    paused_label: str = "- 已暫停: {value}"
    current_job_label: str = "- 目前工作: {value}"
    run_count_label: str = "- 執行次數: {value}"
    last_run_label: str = "- 上次執行: {value}"
    last_jobs_label: str = "- 上次工作: {jobs}"
    last_changed_label: str = "- 上次變更: {value}"
    last_summary_label: str = "- 上次摘要: {value}"
    last_error_label: str = "- 上次錯誤: {value}"
    rerun_pending_label: str = "- 待補跑: {value}"
    jobs_label: str = "- 工作: {jobs}"
    attached_run_label: str = "- 關聯 run: {run_id}"


class TelegramMessagesConfig(BaseModel):
    empty_message_fallback: str = "抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。"


class MessagesConfig(BaseModel):
    agent: AgentMessagesConfig = Field(default_factory=AgentMessagesConfig)
    queue: QueueMessagesConfig = Field(default_factory=QueueMessagesConfig)
    cron: CronMessagesConfig = Field(default_factory=CronMessagesConfig)
    task: TaskMessagesConfig = Field(default_factory=TaskMessagesConfig)
    curator: CuratorMessagesConfig = Field(default_factory=CuratorMessagesConfig)
    telegram: TelegramMessagesConfig = Field(default_factory=TelegramMessagesConfig)


class LogConfig(BaseModel):
    enabled: bool = False
    retention_days: int = 365
    level: str = "INFO"
    log_system_prompt: bool = True  # 是否印出 system prompt
    log_system_prompt_lines: int = 0  # 印出多少行，0 = 全部
    log_reasoning_details: bool = False  # 是否印出完整 LLM reasoning/thinking 內容


class MCPServerConfig(BaseModel):
    """MCP server connection configuration."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    tool_timeout: int = 30
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])


class VisionConfig(BaseModel):
    """Image analysis provider configuration."""

    enabled: bool = False
    provider: str = "minimax"
    api_key: str = ""
    model: str = ""
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_enabled_fields(self) -> "VisionConfig":
        if self.enabled:
            missing = [name for name, value in {"api_key": self.api_key, "model": self.model}.items() if not value]
            if missing:
                raise ValueError(f"vision config requires {', '.join(missing)} when enabled=true")
        return self


class OcrConfig(BaseModel):
    """OCR provider configuration."""

    enabled: bool = False
    provider: str = "minimax"
    api_key: str = ""
    model: str = ""
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_enabled_fields(self) -> "OcrConfig":
        if self.enabled:
            missing = [name for name, value in {"api_key": self.api_key, "model": self.model}.items() if not value]
            if missing:
                raise ValueError(f"ocr config requires {', '.join(missing)} when enabled=true")
        return self


class SpeechConfig(BaseModel):
    """Speech-to-text provider configuration."""

    enabled: bool = False
    provider: str = "minimax"
    api_key: str = ""
    model: str = ""
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_enabled_fields(self) -> "SpeechConfig":
        if self.enabled:
            missing = [name for name, value in {"api_key": self.api_key, "model": self.model}.items() if not value]
            if missing:
                raise ValueError(f"speech config requires {', '.join(missing)} when enabled=true")
        return self


class VideoConfig(BaseModel):
    """Video analysis provider configuration."""

    enabled: bool = False
    provider: str = "minimax"
    api_key: str = ""
    model: str = ""
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_enabled_fields(self) -> "VideoConfig":
        if self.enabled:
            missing = [name for name, value in {"api_key": self.api_key, "model": self.model}.items() if not value]
            if missing:
                raise ValueError(f"video config requires {', '.join(missing)} when enabled=true")
        return self


class ExecToolConfig(BaseModel):
    """Shell execution tool configuration."""

    timeout: int = Field(default=60, ge=1)
    notify_on_exit: bool = True
    notify_on_exit_empty_success: bool = False


class WebSearchToolConfig(BaseModel):
    """Web search tool configuration."""

    provider: Literal["brave", "duckduckgo", "tavily", "searxng", "jina"] = "duckduckgo"
    brave_api_key: str = ""
    tavily_api_key: str = ""
    jina_api_key: str = ""
    searxng_url: str = "https://searx.be"
    max_results: int = Field(default=25, ge=1)
    duckduckgo_max_pages: int = Field(default=10, ge=1)
    proxy: str | None = None


class WebFetchToolConfig(BaseModel):
    """Web fetch tool configuration."""

    max_chars: int = Field(default=50000, ge=1)
    max_response_size: int = Field(default=5 * 1024 * 1024, ge=1)
    timeout: int = Field(default=30, ge=1)
    prefer_trafilatura: bool = True
    firecrawl_api_key: str = ""


class CronToolConfig(BaseModel):
    """Cron tool configuration."""

    default_timezone: str = "UTC"


class ToolPermissionsConfig(BaseModel):
    """Centralized tool exposure and execution policy."""

    enabled: bool = True
    approval_mode: Literal["auto", "ask", "block"] | None = None
    approval_timeout_seconds: float = Field(default=300.0, gt=0)
    allowed_tools: list[str] = Field(default_factory=lambda: ["*"])
    denied_tools: list[str] = Field(default_factory=list)
    allowed_risk_levels: list[str] = Field(
        default_factory=lambda: [
            "read",
            "write",
            "execute",
            "network",
            "external_side_effect",
            "configuration",
            "delegation",
            "memory",
            "mcp",
        ]
    )
    denied_risk_levels: list[str] = Field(default_factory=list)
    approval_required_tools: list[str] = Field(default_factory=list)
    approval_required_risk_levels: list[str] = Field(default_factory=list)


class ToolsConfig(BaseModel):
    """Tool configurations."""

    model_config = ConfigDict(populate_by_name=True)

    max_tool_iterations: int = 100
    exec_tool: ExecToolConfig = Field(default_factory=ExecToolConfig, alias="exec")
    web_search: WebSearchToolConfig = Field(default_factory=WebSearchToolConfig)
    web_fetch: WebFetchToolConfig = Field(default_factory=WebFetchToolConfig)
    cron: CronToolConfig = Field(default_factory=CronToolConfig)
    permissions: ToolPermissionsConfig = Field(default_factory=ToolPermissionsConfig)
    mcp_servers_file: str = "mcp_servers.json"
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class MemoryConfig(BaseModel):
    """Memory configurations."""
    threshold: int  # Trigger consolidation after this many messages
    token_threshold: int
    llm: DocumentLlmConfig


class UserProfileConfig(BaseModel):
    """Auto-update settings for each chat's USER.md (session workspace root)."""

    enabled: bool
    threshold: int
    lookback_messages: int
    llm: DocumentLlmConfig


class RecentSummaryConfig(BaseModel):
    """Per-chat RECENT_SUMMARY.md update configuration."""

    enabled: bool
    threshold: int
    token_threshold: int
    lookback_messages: int
    keep_last_messages: int
    llm: DocumentLlmConfig


class ActiveTaskConfig(BaseModel):
    """Per-chat ACTIVE_TASK.md update configuration."""

    enabled: bool
    threshold: int
    lookback_messages: int
    llm: DocumentLlmConfig


class SearchEmbeddingConfig(BaseModel):
    """Embedding and hybrid reranking configuration."""

    enabled: bool = False
    provider: Literal["openai", "openrouter", "minimax"] = "openai"
    api_key: str = ""
    model: str = ""
    base_url: str | None = None
    batch_size: int = Field(default=16, ge=1, le=128)
    candidate_count: int = Field(default=20, ge=1, le=200)
    candidate_strategy: Literal["fts", "vector"] = "vector"
    vector_backend: Literal["exact", "sqlite_vec", "auto"] = "auto"
    vector_candidate_count: int = Field(default=50, ge=1, le=500)
    retry_failed_on_startup: bool = False

    @model_validator(mode="after")
    def validate_enabled_fields(self) -> "SearchEmbeddingConfig":
        if self.enabled and not self.model:
            raise ValueError("search.embedding.model is required when enabled=true")
        return self


class SearchConfig(BaseModel):
    """Search index configuration."""

    enabled: bool = True
    backend: Literal["sqlite"] = "sqlite"
    history_top_k: int = Field(default=5, ge=1)
    knowledge_top_k: int = Field(default=5, ge=1)
    embedding: SearchEmbeddingConfig = Field(default_factory=SearchEmbeddingConfig)


class Config:
    def __init__(self, llm: LLMsConfig, agent: AgentConfig, storage: StorageConfig,
                 channels: ChannelsConfig, log: LogConfig | None = None, tools: ToolsConfig | None = None,
                 memory: MemoryConfig | None = None, search: SearchConfig | None = None,
                 user_profile: UserProfileConfig | None = None, vision: VisionConfig | None = None,
                 ocr: OcrConfig | None = None, speech: SpeechConfig | None = None, video: VideoConfig | None = None,
                 active_task: ActiveTaskConfig | None = None,
                 recent_summary: RecentSummaryConfig | None = None, source_path: str | Path | None = None,
                 channels_file: str = "channels.json", search_file: str = "search.json", media_file: str = "media.json",
                 messages: MessagesConfig | None = None, messages_file: str = "messages.json"):
        self.llm = llm
        self.agent = agent
        self.storage = storage
        self.channels = channels
        self.log = log or LogConfig()
        self.tools = tools or ToolsConfig()
        self.memory = memory or MemoryConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("memory", {}))
        )
        self.search = search or SearchConfig()
        self.user_profile = user_profile or UserProfileConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("user_profile", {}))
        )
        self.active_task = active_task or ActiveTaskConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("active_task", {}))
        )
        self.recent_summary = recent_summary or RecentSummaryConfig(
            **Config._merge_document_section({}, Config.load_template_data().get("recent_summary", {}))
        )
        self.vision = vision or VisionConfig()
        self.ocr = ocr or OcrConfig()
        self.speech = speech or SpeechConfig()
        self.video = video or VideoConfig()
        self.messages = messages or MessagesConfig()
        self.source_path = Path(source_path).expanduser().resolve() if source_path is not None else None
        self.channels_file = channels_file
        self.search_file = search_file
        self.media_file = media_file
        self.messages_file = messages_file

        if self.agent is None:
            self.agent = Config.load_agent_template_config()

    @staticmethod
    def _resolve_mcp_servers_file(config_path: Path, mcp_servers_file: str | None) -> Path | None:
        if not mcp_servers_file:
            return None

        candidate = Path(mcp_servers_file).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        return candidate

    @staticmethod
    def _resolve_channels_file(config_path: Path, channels_file: str | None) -> Path | None:
        if not channels_file:
            return None

        candidate = Path(channels_file).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        return candidate

    @staticmethod
    def _resolve_search_file(config_path: Path, search_file: str | None) -> Path | None:
        if not search_file:
            return None

        candidate = Path(search_file).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        return candidate

    @staticmethod
    def _resolve_media_file(config_path: Path, media_file: str | None) -> Path | None:
        if not media_file:
            return None

        candidate = Path(media_file).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        return candidate

    @staticmethod
    def _resolve_messages_file(config_path: Path, messages_file: str | None) -> Path | None:
        if not messages_file:
            return None

        candidate = Path(messages_file).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        return candidate

    @staticmethod
    def _resolve_llm_providers_file(config_path: Path, providers_file: str | None) -> Path | None:
        if not providers_file:
            return None

        candidate = Path(providers_file).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        return candidate

    @classmethod
    def _load_mcp_servers_data(cls, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"MCP 設定檔必須是 JSON object：{path}")

        return data

    @classmethod
    def _load_channels_data(cls, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Channels 設定檔必須是 JSON object：{path}")

        return data

    @classmethod
    def _load_search_data(cls, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Search 設定檔必須是 JSON object：{path}")

        return data

    @classmethod
    def _load_media_data(cls, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Media 設定檔必須是 JSON object：{path}")

        return data

    @classmethod
    def _load_messages_data(cls, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Messages 設定檔必須是 JSON object：{path}")

        return data

    @classmethod
    def _load_llm_providers_data(cls, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"LLM providers 設定檔必須是 JSON object：{path}")

        return data

    @classmethod
    def _parse_mcp_servers(cls, raw_data: dict[str, Any], source: Path) -> dict[str, MCPServerConfig]:
        parsed: dict[str, MCPServerConfig] = {}
        for name, server in raw_data.items():
            if not isinstance(server, dict):
                raise ValueError(f"MCP server '{name}' 必須是 JSON object：{source}")
            parsed[name] = MCPServerConfig(**server)
        return parsed

    @classmethod
    def _merge_mcp_servers(
        cls,
        inline_servers: dict[str, Any],
        external_servers: dict[str, Any],
        *,
        config_path: Path,
        external_path: Path | None,
    ) -> dict[str, MCPServerConfig]:
        merged: dict[str, MCPServerConfig] = {}
        merged.update(cls._parse_mcp_servers(inline_servers, config_path))
        if external_path is not None:
            merged.update(cls._parse_mcp_servers(external_servers, external_path))
        return merged

    @classmethod
    def _write_json_file(cls, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    @classmethod
    def _build_default_mcp_servers_path(cls, config_path: Path) -> Path:
        return config_path.parent / "mcp_servers.json"

    @classmethod
    def _build_default_channels_path(cls, config_path: Path) -> Path:
        return config_path.parent / "channels.json"

    @classmethod
    def _build_default_search_path(cls, config_path: Path) -> Path:
        return config_path.parent / "search.json"

    @classmethod
    def _build_default_media_path(cls, config_path: Path) -> Path:
        return config_path.parent / "media.json"

    @classmethod
    def _build_default_messages_path(cls, config_path: Path) -> Path:
        return config_path.parent / "messages.json"

    @classmethod
    def _build_default_llm_providers_path(cls, config_path: Path) -> Path:
        return config_path.parent / "llm.providers.json"

    @classmethod
    def get_mcp_servers_file_path(
        cls,
        config_path: str | Path,
        tools_config: ToolsConfig | dict[str, Any] | None = None,
    ) -> Path:
        resolved_config_path = Path(config_path).expanduser().resolve()
        if isinstance(tools_config, ToolsConfig):
            configured_path = tools_config.mcp_servers_file
        elif isinstance(tools_config, dict):
            configured_path = tools_config.get("mcp_servers_file")
        else:
            configured_path = None

        target_path = cls._resolve_mcp_servers_file(resolved_config_path, configured_path)
        if target_path is None:
            target_path = cls._build_default_mcp_servers_path(resolved_config_path)
        return target_path

    @classmethod
    def get_channels_file_path(
        cls,
        config_path: str | Path,
        config_data: dict[str, Any] | None = None,
        channels_file: str | None = None,
    ) -> Path:
        resolved_config_path = Path(config_path).expanduser().resolve()
        configured_path = channels_file
        if configured_path is None and isinstance(config_data, dict):
            configured_path = config_data.get("channels_file")

        target_path = cls._resolve_channels_file(resolved_config_path, configured_path)
        if target_path is None:
            target_path = cls._build_default_channels_path(resolved_config_path)
        return target_path

    @classmethod
    def get_search_file_path(
        cls,
        config_path: str | Path,
        config_data: dict[str, Any] | None = None,
        search_file: str | None = None,
    ) -> Path:
        resolved_config_path = Path(config_path).expanduser().resolve()
        configured_path = search_file
        if configured_path is None and isinstance(config_data, dict):
            configured_path = config_data.get("search_file")

        target_path = cls._resolve_search_file(resolved_config_path, configured_path)
        if target_path is None:
            target_path = cls._build_default_search_path(resolved_config_path)
        return target_path

    @classmethod
    def get_media_file_path(
        cls,
        config_path: str | Path,
        config_data: dict[str, Any] | None = None,
        media_file: str | None = None,
    ) -> Path:
        resolved_config_path = Path(config_path).expanduser().resolve()
        configured_path = media_file
        if configured_path is None and isinstance(config_data, dict):
            configured_path = config_data.get("media_file")

        target_path = cls._resolve_media_file(resolved_config_path, configured_path)
        if target_path is None:
            target_path = cls._build_default_media_path(resolved_config_path)
        return target_path

    @classmethod
    def get_messages_file_path(
        cls,
        config_path: str | Path,
        config_data: dict[str, Any] | None = None,
        messages_file: str | None = None,
    ) -> Path:
        resolved_config_path = Path(config_path).expanduser().resolve()
        configured_path = messages_file
        if configured_path is None and isinstance(config_data, dict):
            configured_path = config_data.get("messages_file")

        target_path = cls._resolve_messages_file(resolved_config_path, configured_path)
        if target_path is None:
            target_path = cls._build_default_messages_path(resolved_config_path)
        return target_path

    @classmethod
    def get_llm_providers_file_path(
        cls,
        config_path: str | Path,
        llm_config: LLMsConfig | dict[str, Any] | None = None,
        providers_file: str | None = None,
    ) -> Path:
        resolved_config_path = Path(config_path).expanduser().resolve()
        configured_path = providers_file
        if configured_path is None:
            if isinstance(llm_config, LLMsConfig):
                configured_path = llm_config.providers_file
            elif isinstance(llm_config, dict):
                configured_path = llm_config.get("providers_file")

        target_path = cls._resolve_llm_providers_file(resolved_config_path, configured_path)
        if target_path is None:
            target_path = cls._build_default_llm_providers_path(resolved_config_path)
        return target_path

    @classmethod
    def tool_write_blocked_paths(cls, config_path: str | Path) -> frozenset[Path]:
        """Absolute paths that must not be modified via agent write_file/edit_file."""
        root = Path(config_path).expanduser().resolve(strict=False)
        default_siblings = (
            root,
            cls._build_default_channels_path(root),
            cls._build_default_search_path(root),
            cls._build_default_media_path(root),
            cls._build_default_messages_path(root),
            cls._build_default_mcp_servers_path(root),
            cls._build_default_llm_providers_path(root),
        )

        def _freeze(paths: tuple[Path, ...]) -> frozenset[Path]:
            out: set[Path] = set()
            for p in paths:
                try:
                    out.add(p.expanduser().resolve(strict=False))
                except OSError:
                    continue
            return frozenset(out)

        try:
            config = cls.load(root)
        except (FileNotFoundError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, ValidationError):
            return _freeze(default_siblings)

        collected: set[Path] = set()
        try:
            collected.add(root.resolve(strict=False))
        except OSError:
            collected.add(root)

        for p in (
            cls.get_channels_file_path(root, channels_file=config.channels_file),
            cls.get_search_file_path(root, search_file=config.search_file),
            cls.get_media_file_path(root, media_file=config.media_file),
            cls.get_messages_file_path(root, messages_file=config.messages_file),
            cls.get_mcp_servers_file_path(root, config.tools),
            cls.get_llm_providers_file_path(root, config.llm),
        ):
            try:
                collected.add(Path(p).expanduser().resolve(strict=False))
            except OSError:
                continue
        return frozenset(collected)

    @classmethod
    def ensure_mcp_servers_file(cls, config_path: str | Path, config_data: dict[str, Any] | None = None) -> Path:
        tools_data = config_data.get("tools", {}) if isinstance(config_data, dict) else None
        target_path = cls.get_mcp_servers_file_path(config_path, tools_data)

        if not target_path.exists():
            cls._copy_external_template(target_path, "mcp_servers")

        return target_path

    @classmethod
    def write_channels_file(
        cls,
        config_path: str | Path,
        channels_data: dict[str, Any],
        config_data: dict[str, Any] | None = None,
        channels_file: str | None = None,
    ) -> Path:
        target_path = cls.get_channels_file_path(config_path, config_data, channels_file)
        cls._write_json_file(target_path, channels_data)
        return target_path

    @classmethod
    def ensure_channels_file(cls, config_path: str | Path, config_data: dict[str, Any] | None = None) -> Path:
        channels_data = config_data.get("channels") if isinstance(config_data, dict) else None
        target_path = cls.get_channels_file_path(config_path, config_data)

        if not target_path.exists():
            cls._copy_external_template(target_path, "channels")
            if isinstance(channels_data, dict):
                cls._write_json_file(target_path, channels_data)

        return target_path

    @classmethod
    def write_search_file(
        cls,
        config_path: str | Path,
        search_data: dict[str, Any],
        config_data: dict[str, Any] | None = None,
        search_file: str | None = None,
    ) -> Path:
        target_path = cls.get_search_file_path(config_path, config_data, search_file)
        cls._write_json_file(target_path, search_data)
        return target_path

    @classmethod
    def ensure_search_file(cls, config_path: str | Path, config_data: dict[str, Any] | None = None) -> Path:
        search_data = config_data.get("search") if isinstance(config_data, dict) else None
        target_path = cls.get_search_file_path(config_path, config_data)

        if not target_path.exists():
            cls._copy_external_template(target_path, "search")
            if isinstance(search_data, dict):
                cls._write_json_file(target_path, search_data)

        return target_path

    @classmethod
    def write_media_file(
        cls,
        config_path: str | Path,
        media_data: dict[str, Any],
        config_data: dict[str, Any] | None = None,
        media_file: str | None = None,
    ) -> Path:
        target_path = cls.get_media_file_path(config_path, config_data, media_file)
        cls._write_json_file(target_path, media_data)
        return target_path

    @classmethod
    def ensure_media_file(cls, config_path: str | Path, config_data: dict[str, Any] | None = None) -> Path:
        vision_data = config_data.get("vision") if isinstance(config_data, dict) else None
        ocr_data = config_data.get("ocr") if isinstance(config_data, dict) else None
        speech_data = config_data.get("speech") if isinstance(config_data, dict) else None
        video_data = config_data.get("video") if isinstance(config_data, dict) else None
        target_path = cls.get_media_file_path(config_path, config_data)

        if not target_path.exists():
            cls._copy_external_template(target_path, "media")
            if any(isinstance(section, dict) for section in (vision_data, ocr_data, speech_data, video_data)):
                cls._write_json_file(
                    target_path,
                    {
                        "vision": vision_data if isinstance(vision_data, dict) else VisionConfig().model_dump(),
                        "ocr": ocr_data if isinstance(ocr_data, dict) else OcrConfig().model_dump(),
                        "speech": speech_data if isinstance(speech_data, dict) else SpeechConfig().model_dump(),
                        "video": video_data if isinstance(video_data, dict) else VideoConfig().model_dump(),
                    },
                )

        return target_path

    @classmethod
    def write_messages_file(
        cls,
        config_path: str | Path,
        messages_data: dict[str, Any],
        config_data: dict[str, Any] | None = None,
        messages_file: str | None = None,
    ) -> Path:
        target_path = cls.get_messages_file_path(config_path, config_data, messages_file)
        cls._write_json_file(target_path, messages_data)
        return target_path

    @classmethod
    def ensure_messages_file(cls, config_path: str | Path, config_data: dict[str, Any] | None = None) -> Path:
        messages_data = config_data.get("messages") if isinstance(config_data, dict) else None
        target_path = cls.get_messages_file_path(config_path, config_data)

        if not target_path.exists():
            cls._copy_external_template(target_path, "messages")
            if isinstance(messages_data, dict):
                cls._write_json_file(target_path, messages_data)

        return target_path

    @classmethod
    def write_llm_providers_file(
        cls,
        config_path: str | Path,
        providers_data: dict[str, Any],
        llm_config: LLMsConfig | dict[str, Any] | None = None,
        providers_file: str | None = None,
    ) -> Path:
        target_path = cls.get_llm_providers_file_path(config_path, llm_config, providers_file)
        cls._write_json_file(target_path, providers_data)
        return target_path

    @classmethod
    def ensure_llm_providers_file(cls, config_path: str | Path, config_data: dict[str, Any] | None = None) -> Path:
        llm_data = config_data.get("llm") if isinstance(config_data, dict) else None
        providers_data = llm_data.get("providers") if isinstance(llm_data, dict) else None
        target_path = cls.get_llm_providers_file_path(config_path, llm_data)

        if not target_path.exists():
            cls._copy_external_template(target_path, "llm.providers")
            if isinstance(providers_data, dict):
                cls._write_json_file(target_path, providers_data)

        return target_path

    @staticmethod
    def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = Config._deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"設定檔不存在：{path}")
        with open(path, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        if not data:
            raise ValueError(f"設定檔是空的：{path}")
        for section in ["llm", "storage"]:
            if section not in data:
                raise ValueError(f"設定檔缺少必要區塊：{section}")
        template_data = cls.load_template_data()
        llm_data = dict(template_data.get("llm", {}))
        llm_data.update(dict(data.get("llm", {})))
        inline_providers = llm_data.get("providers", {})
        llm_providers_path = cls._resolve_llm_providers_file(path, llm_data.get("providers_file"))
        external_providers = cls._load_llm_providers_data(llm_providers_path) if llm_providers_path is not None else {}
        merged_providers = dict(inline_providers) if isinstance(inline_providers, dict) else {}
        merged_providers.update(external_providers)
        if merged_providers or llm_providers_path is not None:
            llm_data["providers"] = merged_providers
        inline_channels = data.get("channels", {})
        channels_path = cls._resolve_channels_file(path, data.get("channels_file"))
        external_channels = cls._load_channels_data(channels_path) if channels_path is not None else {}
        merged_channels = dict(inline_channels) if isinstance(inline_channels, dict) else {}
        merged_channels.update(external_channels)
        if not merged_channels:
            raise ValueError("設定檔缺少必要區塊：channels 或 channels_file")
        inline_search = data.get("search", {})
        search_path = cls._resolve_search_file(path, data.get("search_file"))
        external_search = cls._load_search_data(search_path) if search_path is not None else {}
        merged_search = dict(inline_search) if isinstance(inline_search, dict) else {}
        merged_search.update(external_search)
        media_path = cls._resolve_media_file(path, data.get("media_file"))
        external_media = cls._load_media_data(media_path) if media_path is not None else {}
        merged_vision = dict(data.get("vision", {})) if isinstance(data.get("vision", {}), dict) else {}
        merged_ocr = dict(data.get("ocr", {})) if isinstance(data.get("ocr", {}), dict) else {}
        merged_speech = dict(data.get("speech", {})) if isinstance(data.get("speech", {}), dict) else {}
        merged_video = dict(data.get("video", {})) if isinstance(data.get("video", {}), dict) else {}
        if isinstance(external_media.get("vision"), dict):
            merged_vision.update(external_media["vision"])
        if isinstance(external_media.get("ocr"), dict):
            merged_ocr.update(external_media["ocr"])
        if isinstance(external_media.get("speech"), dict):
            merged_speech.update(external_media["speech"])
        if isinstance(external_media.get("video"), dict):
            merged_video.update(external_media["video"])
        tools_data = dict(data.get("tools", {})) if "tools" in data else {}
        inline_mcp_servers = tools_data.get("mcp_servers", {})
        mcp_servers_path = cls._resolve_mcp_servers_file(path, tools_data.get("mcp_servers_file"))
        external_mcp_servers = cls._load_mcp_servers_data(mcp_servers_path) if mcp_servers_path is not None else {}
        if inline_mcp_servers or mcp_servers_path is not None:
            tools_data["mcp_servers"] = cls._merge_mcp_servers(
                inline_mcp_servers,
                external_mcp_servers,
                config_path=path,
                external_path=mcp_servers_path,
            )
        inline_messages = dict(data.get("messages", {})) if isinstance(data.get("messages", {}), dict) else {}
        messages_path = cls._resolve_messages_file(path, data.get("messages_file"))
        external_messages = cls._load_messages_data(messages_path) if messages_path is not None else {}
        merged_messages = cls._deep_merge_dicts(inline_messages, external_messages)
        agent_data = cls._deep_merge_dicts(
            dict(template_data.get("agent", {})),
            dict(data.get("agent", {})) if isinstance(data.get("agent", {}), dict) else {},
        )
        return cls(
            llm=LLMsConfig(**llm_data),
            agent=AgentConfig(**agent_data),
            storage=StorageConfig(**data["storage"]),
            channels=ChannelsConfig(instances=coerce_channel_instances(merged_channels)),
            log=LogConfig(**data["log"]) if "log" in data else None,
            tools=ToolsConfig(**tools_data) if "tools" in data else None,
            memory=MemoryConfig(
                **cls._merge_document_section(dict(data.get("memory", {})), template_data.get("memory", {}))
            ),
            search=SearchConfig(**merged_search) if (merged_search or "search" in data or search_path is not None) else None,
            user_profile=UserProfileConfig(
                **cls._merge_document_section(dict(data.get("user_profile", {})), template_data.get("user_profile", {}))
            ),
            active_task=ActiveTaskConfig(
                **cls._merge_document_section(dict(data.get("active_task", {})), template_data.get("active_task", {}))
            ),
            recent_summary=RecentSummaryConfig(
                **cls._merge_document_section(
                    dict(data.get("recent_summary", {})), template_data.get("recent_summary", {})
                )
            ),
            messages=MessagesConfig(**merged_messages) if (merged_messages or "messages" in data or messages_path is not None) else None,
            vision=VisionConfig(**merged_vision) if (merged_vision or "vision" in data or media_path is not None) else None,
            ocr=OcrConfig(**merged_ocr) if (merged_ocr or "ocr" in data or media_path is not None) else None,
            speech=SpeechConfig(**merged_speech) if (merged_speech or "speech" in data or media_path is not None) else None,
            video=VideoConfig(**merged_video) if (merged_video or "video" in data or media_path is not None) else None,
            source_path=path,
            channels_file=data.get("channels_file") or "channels.json",
            search_file=data.get("search_file") or "search.json",
            media_file=data.get("media_file") or "media.json",
            messages_file=data.get("messages_file") or "messages.json",
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            workspace = Path.home() / ".opensprite"
            workspace.mkdir(parents=True, exist_ok=True)
            path = workspace / "opensprite.json"
            if not path.exists():
                cls.copy_template(path)
                from ..utils.log import logger
                logger.info(f"已建立設定檔：{path}")
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"設定檔不存在：{path}")
        if path.suffix != ".json":
            raise ValueError(f"不支援的格式：{path.suffix}")
        return cls.from_json(path)

    @property
    def is_llm_configured(self) -> bool:
        if self.llm.providers and self.llm.default and self.llm.default in self.llm.providers:
            provider = self.llm.providers[self.llm.default]
            if provider.auth_type == "openai_codex_oauth":
                if not provider.model:
                    return False
                try:
                    from ..auth.codex import get_codex_status

                    status = get_codex_status(self.source_path.parent if self.source_path is not None else None)
                except Exception:
                    return False
                return status.configured and status.expired is not True
            if provider.auth_type == "github_copilot_oauth":
                if not provider.model:
                    return False
                try:
                    from ..auth.copilot import get_copilot_status

                    status = get_copilot_status(self.source_path.parent if self.source_path is not None else None)
                except Exception:
                    return False
                return status.configured
            return bool(provider.api_key and provider.model)
        return bool(self.llm.api_key)

    @classmethod
    def template_path(cls) -> Path:
        """Return the packaged JSON config template path."""
        return Path(__file__).parent / "opensprite.json.template"

    @classmethod
    def external_template_path(cls, name: str) -> Path:
        """Return one packaged external JSON template path."""
        return Path(__file__).parent / f"{name}.json.template"

    @classmethod
    def load_template_data(cls) -> dict[str, Any]:
        """Load the packaged JSON config template."""
        template_path = cls.template_path()
        with open(template_path, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        return data

    @classmethod
    def load_agent_template_config(cls, **overrides: Any) -> AgentConfig:
        """Create agent config from the packaged JSON template plus explicit overrides."""
        data = dict(cls.load_template_data().get("agent", {}))
        data.update(overrides)
        return AgentConfig(**data)

    @staticmethod
    def _merge_document_section(user: dict[str, Any], template_section: dict[str, Any]) -> dict[str, Any]:
        """Shallow-merge section keys; nested ``llm`` is deep-merged so partial overrides keep template defaults."""
        merged = dict(template_section)
        for key, value in user.items():
            if key == "llm" and isinstance(value, dict):
                base_llm = dict(template_section.get("llm", {}))
                base_llm.update(value)
                merged["llm"] = base_llm
            else:
                merged[key] = value
        return merged

    @classmethod
    def packaged_llm_flat_dict(cls) -> dict[str, Any]:
        """Packaged ``opensprite.json.template`` top-level ``llm`` object (after JSON load)."""
        return dict(cls.load_template_data().get("llm", {}))

    @classmethod
    def packaged_agent_llm_chat_kwargs(cls) -> dict[str, Any]:
        """Map packaged ``llm`` to :class:`opensprite.agent.agent.AgentLoop` keyword arguments."""
        llm = cls.packaged_llm_flat_dict()
        return {
            "llm_chat_temperature": llm["temperature"],
            "llm_chat_max_tokens": llm["max_tokens"],
            "llm_chat_top_p": llm["top_p"],
            "llm_chat_frequency_penalty": llm["frequency_penalty"],
            "llm_chat_presence_penalty": llm["presence_penalty"],
            "llm_pass_decoding_params": llm["pass_decoding_params"],
        }

    @classmethod
    def packaged_execution_engine_chat_kwargs(cls) -> dict[str, Any]:
        """Map packaged ``llm`` to :class:`opensprite.agent.execution.ExecutionEngine` keyword arguments."""
        llm = cls.packaged_llm_flat_dict()
        return {
            "chat_temperature": llm["temperature"],
            "chat_max_tokens": llm["max_tokens"],
            "chat_top_p": llm["top_p"],
            "chat_frequency_penalty": llm["frequency_penalty"],
            "chat_presence_penalty": llm["presence_penalty"],
            "pass_decoding_params": llm["pass_decoding_params"],
        }

    @classmethod
    def load_external_template_data(cls, name: str) -> dict[str, Any]:
        """Load one packaged external JSON template."""
        template_path = cls.external_template_path(name)
        with open(template_path, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        return data

    @classmethod
    def _copy_external_template(cls, target_path: Path, template_name: str) -> None:
        """Copy a packaged external template to the target path."""
        import shutil

        template_path = cls.external_template_path(template_name)
        if not template_path.exists():
            raise FileNotFoundError(f"設定模板不存在：{template_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_path, target_path)

    @classmethod
    def copy_template(cls, path: str | Path) -> Path:
        """Copy template config file from package."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        template_path = cls.template_path()

        if template_path.exists():
            shutil.copy2(template_path, path)
            cls.ensure_channels_file(path, cls.load_template_data())
            cls.ensure_search_file(path, cls.load_template_data())
            cls.ensure_media_file(path, cls.load_template_data())
            cls.ensure_messages_file(path, cls.load_template_data())
            cls.ensure_llm_providers_file(path, cls.load_template_data())
            cls.ensure_mcp_servers_file(path, cls.load_template_data())

        return path

    def save(self, path: str | Path) -> None:
        """Save config to JSON file."""
        path = Path(path).expanduser().resolve()
        if path.suffix != ".json":
            raise ValueError(f"不支援的格式：{path.suffix}")
        self.write_channels_file(
            path,
            {"instances": {key: dict(value) for key, value in self.channels.instances.items()}},
            channels_file=self.channels_file,
        )
        self.write_search_file(
            path,
            self.search.model_dump(),
            search_file=self.search_file,
        )
        self.write_media_file(
            path,
            {
                "vision": self.vision.model_dump(),
                "ocr": self.ocr.model_dump(),
                "speech": self.speech.model_dump(),
                "video": self.video.model_dump(),
            },
            media_file=self.media_file,
        )
        self.write_messages_file(
            path,
            self.messages.model_dump(),
            messages_file=self.messages_file,
        )
        self.write_llm_providers_file(
            path,
            {name: provider.model_dump() for name, provider in self.llm.providers.items()},
            providers_file=self.llm.providers_file,
        )
        mcp_servers_path = self._resolve_mcp_servers_file(path, self.tools.mcp_servers_file)
        if mcp_servers_path is not None:
            self._write_json_file(
                mcp_servers_path,
                {name: server.model_dump() for name, server in self.tools.mcp_servers.items()},
            )
        data = {
            "llm": {
                "providers_file": self.llm.providers_file,
                "default": self.llm.default,
                "context_window_tokens": self.llm.context_window_tokens,
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
                "top_p": self.llm.top_p,
                "frequency_penalty": self.llm.frequency_penalty,
                "presence_penalty": self.llm.presence_penalty,
                "pass_decoding_params": self.llm.pass_decoding_params,
            },
            "storage": {"type": self.storage.type, "path": self.storage.path},
            "channels_file": self.channels_file,
            "search_file": self.search_file,
            "media_file": self.media_file,
            "messages_file": self.messages_file,
            "log": {
                "enabled": self.log.enabled,
                "retention_days": self.log.retention_days,
                "level": self.log.level,
                "log_system_prompt": self.log.log_system_prompt,
                "log_system_prompt_lines": self.log.log_system_prompt_lines,
                "log_reasoning_details": self.log.log_reasoning_details,
            },
            "tools": {
                "max_tool_iterations": self.tools.max_tool_iterations,
                "exec": self.tools.exec_tool.model_dump(by_alias=True),
                "web_search": self.tools.web_search.model_dump(by_alias=True),
                "web_fetch": self.tools.web_fetch.model_dump(by_alias=True),
                "cron": self.tools.cron.model_dump(by_alias=True),
                "mcp_servers_file": self.tools.mcp_servers_file,
            },
            "agent": {
                "max_history": self.agent.max_history,
                "history_token_budget": self.agent.history_token_budget,
                "context_compaction_enabled": self.agent.context_compaction_enabled,
                "context_compaction_threshold_ratio": self.agent.context_compaction_threshold_ratio,
                "context_compaction_min_messages": self.agent.context_compaction_min_messages,
                "context_compaction_strategy": self.agent.context_compaction_strategy,
                "context_compaction_llm": self.agent.context_compaction_llm.model_dump(),
                "skill_review_enabled": self.agent.skill_review_enabled,
                "skill_review_min_tool_calls": self.agent.skill_review_min_tool_calls,
                "skill_review_max_tool_iterations": self.agent.skill_review_max_tool_iterations,
                "skill_review_transcript_messages": self.agent.skill_review_transcript_messages,
                "worktree_sandbox_enabled": self.agent.worktree_sandbox_enabled,
            },
            "memory": {
                "threshold": self.memory.threshold,
                "token_threshold": self.memory.token_threshold,
                "llm": self.memory.llm.model_dump(),
            },
            "user_profile": {
                "enabled": self.user_profile.enabled,
                "threshold": self.user_profile.threshold,
                "lookback_messages": self.user_profile.lookback_messages,
                "llm": self.user_profile.llm.model_dump(),
            },
            "active_task": {
                "enabled": self.active_task.enabled,
                "threshold": self.active_task.threshold,
                "lookback_messages": self.active_task.lookback_messages,
                "llm": self.active_task.llm.model_dump(),
            },
            "recent_summary": {
                "enabled": self.recent_summary.enabled,
                "threshold": self.recent_summary.threshold,
                "token_threshold": self.recent_summary.token_threshold,
                "lookback_messages": self.recent_summary.lookback_messages,
                "keep_last_messages": self.recent_summary.keep_last_messages,
                "llm": self.recent_summary.llm.model_dump(),
            },
        }
        self._write_json_file(path, data)
