"""opensprite/config/schema.py - 設定檔定義"""
import json
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    
    api_key: str
    model: str = ""
    base_url: str | None = None
    enabled: bool = False


class LLMsConfig(BaseModel):
    """LLM configuration with support for multiple providers."""

    providers: dict[str, ProviderConfig] = {}
    providers_file: str = "llm.providers.json"
    default: str | None = None
    api_key: str = ""
    model: str = ""
    base_url: str | None = None
    temperature: float
    max_tokens: int

    def get_active(self) -> ProviderConfig:
        """Get the active provider configuration."""
        if self.providers and self.default and self.default in self.providers:
            return self.providers[self.default]
        return ProviderConfig(api_key=self.api_key, model=self.model, base_url=self.base_url, enabled=True)


class AgentConfig(BaseModel):
    """Agent configuration."""
    
    max_history: int = 300
    history_token_budget: int = 140000


class StorageConfig(BaseModel):
    """Storage configuration."""

    type: Literal["memory", "sqlite"]
    path: str


class ChannelsConfig(BaseModel):
    telegram: dict[str, Any] = Field(default_factory=lambda: {
        "enabled": False,
        "token": "",
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
        "pool_timeout": 30,
        "get_updates_connect_timeout": 10,
        "get_updates_read_timeout": 30,
        "get_updates_write_timeout": 30,
        "get_updates_pool_timeout": 30,
        "poll_timeout": 10,
        "bootstrap_retries": 3,
        "drop_pending_updates": False,
    })
    console: dict[str, Any] = Field(default_factory=lambda: {"enabled": True})


class LogConfig(BaseModel):
    enabled: bool = False
    retention_days: int = 365
    level: str = "INFO"
    log_system_prompt: bool = True  # 是否印出 system prompt
    log_system_prompt_lines: int = 0  # 印出多少行，0 = 全部


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


class WebSearchToolConfig(BaseModel):
    """Web search tool configuration."""

    provider: Literal["brave", "duckduckgo", "tavily", "searxng", "jina"] = "brave"
    brave_api_key: str = ""
    tavily_api_key: str = ""
    jina_api_key: str = ""
    searxng_url: str = "https://searx.be"
    max_results: int = Field(default=10, ge=1, le=10)
    proxy: str | None = None


class WebFetchToolConfig(BaseModel):
    """Web fetch tool configuration."""

    max_chars: int = Field(default=50000, ge=1)
    timeout: int = Field(default=30, ge=1)
    prefer_trafilatura: bool = True
    firecrawl_api_key: str = ""


class CronToolConfig(BaseModel):
    """Cron tool configuration."""

    default_timezone: str = "UTC"


class ToolsConfig(BaseModel):
    """Tool configurations."""

    model_config = ConfigDict(populate_by_name=True)

    max_tool_iterations: int = 100
    exec_tool: ExecToolConfig = Field(default_factory=ExecToolConfig, alias="exec")
    web_search: WebSearchToolConfig = Field(default_factory=WebSearchToolConfig)
    web_fetch: WebFetchToolConfig = Field(default_factory=WebFetchToolConfig)
    cron: CronToolConfig = Field(default_factory=CronToolConfig)
    mcp_servers_file: str = "mcp_servers.json"
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class MemoryConfig(BaseModel):
    """Memory configurations."""
    threshold: int = 50  # Trigger consolidation after this many messages
    token_threshold: int = 120000


class UserProfileConfig(BaseModel):
    """Global USER.md profile update configuration."""

    enabled: bool = True
    threshold: int = 40
    lookback_messages: int = 80


class RecentSummaryConfig(BaseModel):
    """Per-chat RECENT_SUMMARY.md update configuration."""

    enabled: bool = True
    threshold: int = 20
    token_threshold: int = 30000
    lookback_messages: int = 120
    keep_last_messages: int = 40


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

    enabled: bool = False
    history_top_k: int = Field(default=5, ge=1)
    knowledge_top_k: int = Field(default=5, ge=1)
    embedding: SearchEmbeddingConfig = Field(default_factory=SearchEmbeddingConfig)


class Config:
    def __init__(self, llm: LLMsConfig, agent: AgentConfig, storage: StorageConfig,
                 channels: ChannelsConfig, log: LogConfig | None = None, tools: ToolsConfig | None = None,
                 memory: MemoryConfig | None = None, search: SearchConfig | None = None,
                 user_profile: UserProfileConfig | None = None, vision: VisionConfig | None = None,
                 speech: SpeechConfig | None = None, video: VideoConfig | None = None,
                 recent_summary: RecentSummaryConfig | None = None, source_path: str | Path | None = None,
                 channels_file: str = "channels.json", search_file: str = "search.json", media_file: str = "media.json"):
        self.llm = llm
        self.agent = agent
        self.storage = storage
        self.channels = channels
        self.log = log or LogConfig()
        self.tools = tools or ToolsConfig()
        self.memory = memory or MemoryConfig()
        self.search = search or SearchConfig()
        self.user_profile = user_profile or UserProfileConfig()
        self.recent_summary = recent_summary or RecentSummaryConfig()
        self.vision = vision or VisionConfig()
        self.speech = speech or SpeechConfig()
        self.video = video or VideoConfig()
        self.source_path = Path(source_path).expanduser().resolve() if source_path is not None else None
        self.channels_file = channels_file
        self.search_file = search_file
        self.media_file = media_file

        if self.agent is None:
            self.agent = AgentConfig()

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
        speech_data = config_data.get("speech") if isinstance(config_data, dict) else None
        video_data = config_data.get("video") if isinstance(config_data, dict) else None
        target_path = cls.get_media_file_path(config_path, config_data)

        if not target_path.exists():
            cls._copy_external_template(target_path, "media")
            if any(isinstance(section, dict) for section in (vision_data, speech_data, video_data)):
                cls._write_json_file(
                    target_path,
                    {
                        "vision": vision_data if isinstance(vision_data, dict) else VisionConfig().model_dump(),
                        "speech": speech_data if isinstance(speech_data, dict) else SpeechConfig().model_dump(),
                        "video": video_data if isinstance(video_data, dict) else VideoConfig().model_dump(),
                    },
                )

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
        llm_data = dict(data.get("llm", {}))
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
        merged_speech = dict(data.get("speech", {})) if isinstance(data.get("speech", {}), dict) else {}
        merged_video = dict(data.get("video", {})) if isinstance(data.get("video", {}), dict) else {}
        if isinstance(external_media.get("vision"), dict):
            merged_vision.update(external_media["vision"])
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
        return cls(
            llm=LLMsConfig(**llm_data),
            agent=AgentConfig(**data["agent"]) if "agent" in data else None,
            storage=StorageConfig(**data["storage"]),
            channels=ChannelsConfig(**merged_channels),
            log=LogConfig(**data["log"]) if "log" in data else None,
            tools=ToolsConfig(**tools_data) if "tools" in data else None,
            memory=MemoryConfig(**data.get("memory", {})) if "memory" in data else None,
            search=SearchConfig(**merged_search) if (merged_search or "search" in data or search_path is not None) else None,
            user_profile=UserProfileConfig(**data.get("user_profile", {})) if "user_profile" in data else None,
            recent_summary=RecentSummaryConfig(**data.get("recent_summary", {})) if "recent_summary" in data else None,
            vision=VisionConfig(**merged_vision) if (merged_vision or "vision" in data or media_path is not None) else None,
            speech=SpeechConfig(**merged_speech) if (merged_speech or "speech" in data or media_path is not None) else None,
            video=VideoConfig(**merged_video) if (merged_video or "video" in data or media_path is not None) else None,
            source_path=path,
            channels_file=data.get("channels_file") or "channels.json",
            search_file=data.get("search_file") or "search.json",
            media_file=data.get("media_file") or "media.json",
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
            return bool(self.llm.providers[self.llm.default].api_key)
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
            {
                "telegram": dict(self.channels.telegram),
                "console": dict(self.channels.console),
            },
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
                "speech": self.speech.model_dump(),
                "video": self.video.model_dump(),
            },
            media_file=self.media_file,
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
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
            },
            "storage": {"type": self.storage.type, "path": self.storage.path},
            "channels_file": self.channels_file,
            "search_file": self.search_file,
            "media_file": self.media_file,
            "log": {"enabled": self.log.enabled, "retention_days": self.log.retention_days, "level": self.log.level, "log_system_prompt": self.log.log_system_prompt, "log_system_prompt_lines": self.log.log_system_prompt_lines},
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
            },
            "memory": {
                "threshold": self.memory.threshold,
                "token_threshold": self.memory.token_threshold,
            },
            "user_profile": {
                "enabled": self.user_profile.enabled,
                "threshold": self.user_profile.threshold,
                "lookback_messages": self.user_profile.lookback_messages,
            },
            "recent_summary": {
                "enabled": self.recent_summary.enabled,
                "threshold": self.recent_summary.threshold,
                "token_threshold": self.recent_summary.token_threshold,
                "lookback_messages": self.recent_summary.lookback_messages,
                "keep_last_messages": self.recent_summary.keep_last_messages,
            },
        }
        self._write_json_file(path, data)
