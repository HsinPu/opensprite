"""minibot/config/schema.py - 設定檔定義"""
import json
from pathlib import Path
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    api_key: str
    model: str
    base_url: str | None = None


class LLMsConfig(BaseModel):
    providers: dict[str, ProviderConfig] = {}
    default: str | None = None
    api_key: str = ""
    model: str = ""
    base_url: str | None = None
    temperature: float
    max_tokens: int

    def get_active(self) -> ProviderConfig:
        if self.providers and self.default and self.default in self.providers:
            return self.providers[self.default]
        return ProviderConfig(api_key=self.api_key, model=self.model, base_url=self.base_url)


class AgentConfig(BaseModel):
    system_prompt: str
    max_history: int
    memory_threshold: int = 30  # Trigger consolidation after this many messages
    brave_api_key: str = ""  # Brave Search API key for web search


class StorageConfig(BaseModel):
    type: str
    path: str


class ChannelsConfig(BaseModel):
    telegram: dict = Field(default_factory=lambda: {"enabled": False, "token": ""})
    console: dict = Field(default_factory=lambda: {"enabled": True})


class LogConfig(BaseModel):
    enabled: bool = True
    retention_days: int = 365
    level: str = "INFO"


class ToolsConfig(BaseModel):
    """Tool configurations."""
    brave_api_key: str = ""


class Config:
    def __init__(self, llm: LLMsConfig, agent: AgentConfig, storage: StorageConfig,
                 channels: ChannelsConfig, log: LogConfig | None = None, tools: ToolsConfig | None = None):
        self.llm = llm
        self.agent = agent
        self.storage = storage
        self.channels = channels
        self.log = log or LogConfig()
        self.tools = tools or ToolsConfig()

    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"設定檔不存在：{path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            raise ValueError(f"設定檔是空的：{path}")
        for section in ["llm", "agent", "storage", "channels"]:
            if section not in data:
                raise ValueError(f"設定檔缺少必要區塊：{section}")
        return cls(
            llm=LLMsConfig(**data["llm"]),
            agent=AgentConfig(**data["agent"]),
            storage=StorageConfig(**data["storage"]),
            channels=ChannelsConfig(**data["channels"]),
            log=LogConfig(**data["log"]) if "log" in data else None,
            tools=ToolsConfig(**data.get("tools", {})) if "tools" in data else None,
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            workspace = Path.home() / ".minibot" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            path = workspace / "minibot.json"
            if not path.exists():
                cls.generate_template(path)
                from minibot.utils.log import logger
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
    def generate_template(cls, path: str | Path | None = None) -> Path:
        if path is None:
            workspace = Path.home() / ".minibot" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            path = workspace / "minibot.json"
        else:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            from minibot.utils.log import logger
            logger.info(f"設定檔已存在：{path}")
            return path
        default_config = {
            "llm": {
                "providers": {
                    "openrouter": {"api_key": "", "model": "openai/gpt-4o-mini", "base_url": "https://openrouter.ai/api/v1"},
                    "openai": {"api_key": "", "model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"}
                },
                "default": "openrouter",
                "temperature": 0.7,
                "max_tokens": 8192
            },
            "agent": {"system_prompt": "你是個有用且簡潔的助理。", "max_history": 50, "brave_api_key": ""},
            "storage": {"type": "sqlite", "path": "~/.minibot/data/sessions.db"},
            "channels": {"telegram": {"enabled": False, "token": ""}, "console": {"enabled": True}},
            "log": {"enabled": True, "retention_days": 365, "level": "INFO"},
            "tools": {"brave_api_key": ""}
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
        return path

    def save(self, path: str | Path):
        path = Path(path)
        if path.suffix != ".json":
            raise ValueError(f"不支援的格式：{path.suffix}")
        data = {
            "llm": {
                "providers": dict(self.llm.providers) if self.llm.providers else {},
                "default": self.llm.default,
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
            },
            "agent": {"system_prompt": self.agent.system_prompt, "max_history": self.agent.max_history, "brave_api_key": self.agent.brave_api_key},
            "storage": {"type": self.storage.type, "path": self.storage.path},
            "channels": {
                "telegram": {"enabled": self.channels.telegram.get("enabled", False), "token": self.channels.telegram.get("token", "")},
                "console": {"enabled": self.channels.console.get("enabled", True)},
            },
            "log": {"enabled": self.log.enabled, "retention_days": self.log.retention_days, "level": self.log.level},
            "tools": {"brave_api_key": self.tools.brave_api_key},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
