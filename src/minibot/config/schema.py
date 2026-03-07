"""
minibot/config/schema.py - 設定檔定義

從 JSON 檔案讀取設定
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


# ============================================
# LLM 設定
# ============================================

class ProviderConfig(BaseModel):
    """單一 LLM Provider 設定"""
    api_key: str
    model: str
    base_url: str | None = None


class LLMsConfig(BaseModel):
    """LLM 設定 - 支持多 Provider"""
    
    # 多 provider 模式
    providers: dict[str, ProviderConfig] = {}
    default: str | None = None
    
    # 向後兼容：單一 provider
    api_key: str = ""
    model: str = ""
    base_url: str | None = None
    
    # 共用
    temperature: float = 0.7
    max_tokens: int = 2048
    
    def get_active(self) -> ProviderConfig:
        """取得當前使用的 Provider 設定"""
        if self.providers and self.default and self.default in self.providers:
            return self.providers[self.default]
        return ProviderConfig(api_key=self.api_key, model=self.model, base_url=self.base_url)


# ============================================
# Agent 設定
# ============================================

class AgentConfig(BaseModel):
    """Agent 設定（從 JSON 讀取）"""
    
    system_prompt: str  # 必填
    max_history: int  # 必填


# ============================================
# Storage 設定
# ============================================

class StorageConfig(BaseModel):
    """Storage 設定（從 JSON 讀取）"""
    
    type: str  # 必填：memory / file / sqlite
    path: str  # 必填


# ============================================
# Channels 設定
# ============================================

class TelegramConfig(BaseModel):
    """Telegram 設定（從 JSON 讀取）"""
    
    enabled: bool  # 必填
    token: str  # 必填（enabled=true 時）


class ConsoleConfig(BaseModel):
    """Console 設定（從 JSON 讀取）"""
    
    enabled: bool  # 必填


class ChannelsConfig(BaseModel):
    """Channels 設定（從 JSON 讀取）"""
    
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)


# ============================================
# Log 設定
# ============================================

class LogConfig(BaseModel):
    """Log 設定（從 JSON 讀取）"""
    
    enabled: bool = True  # 預設開啟
    retention_days: int = 365  # 預設保留 365 天
    level: str = "INFO"  # 預設 INFO


# ============================================
# 主設定
# ============================================

class Config:
    """主設定檔（從 JSON 讀取）"""
    
    def __init__(
        self,
        llm: LLMsConfig,
        agent: AgentConfig,
        storage: StorageConfig,
        channels: ChannelsConfig,
        log: LogConfig | None = None,
    ):
        self.llm = llm
        self.agent = agent
        self.storage = storage
        self.channels = channels
        self.log = log or LogConfig()  # 預設值
    
    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        """從 JSON 檔案讀取設定"""
        path = Path(path)
        
        if not path.exists():
            raise FileNotFoundError(f"設定檔不存在: {path}")
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return cls._parse_data(data, path)
    
    @classmethod
    def _parse_data(cls, data: dict, path: Path) -> "Config":
        """解析設定資料"""
        if not data:
            raise ValueError(f"設定檔是空的: {path}")
        
        # 檢查必要區塊
        required_sections = ["llm", "agent", "storage", "channels"]
        for section in required_sections:
            if section not in data:
                raise ValueError(f"設定檔缺少必要區塊: {section}")
        
        # 建立各部分設定（無預設值）
        llm = LLMsConfig(**data["llm"])
        agent = AgentConfig(**data["agent"])
        storage = StorageConfig(**data["storage"])
        channels = ChannelsConfig(**data["channels"])
        
        # Log 設定可選
        log = None
        if "log" in data:
            log = LogConfig(**data["log"])
        
        return cls(llm=llm, agent=agent, storage=storage, channels=channels, log=log)
    
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """
        載入設定
        
        參數：
            path: 設定檔路徑，預設從 workspace 讀取 ~/.minibot/workspace/nanobot.json
        """
        if path is None:
            # 預設從 workspace 讀取
            workspace = Path.home() / ".minibot" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            config_path = workspace / "nanobot.json"
            
            # 如果 workspace 裡沒有 config，產生預設範本
            if not config_path.exists():
                cls.generate_template(config_path)
                from minibot.utils.log import logger
                logger.info(f"已建立設定檔: {config_path}")
            
            path = config_path
        
        path = Path(path)
        
        if not path.exists():
            raise FileNotFoundError(f"設定檔不存在: {path}")
        
        # 根據副檔名判斷格式（目前只支援 JSON）
        if path.suffix == ".json":
            return cls.from_json(path)
        else:
            raise ValueError(f"不支援的格式: {path.suffix}，只支援 .json")
    
    @property
    def is_llm_configured(self) -> bool:
        """檢查 LLM 是否已設定"""
        return bool(self.llm.api_key)
    
    @classmethod
    def generate_template(cls, path: str | Path | None = None) -> Path:
        """
        產生預設設定檔
        
        參數：
            path: 輸出路徑，預設 ~/.minibot/workspace/nanobot.json
        
        回傳：
            設定檔路徑
        """
        import json
        
        if path is None:
            workspace = Path.home() / ".minibot" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            path = workspace / "nanobot.json"
        else:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        
        # 如果檔案已存在，就不覆蓋
        if path.exists():
            from minibot.utils.log import logger
            logger.info(f"設定檔已存在: {path}")
            return path
        
        # 產生預設設定 (多 provider 模式)
        default_config = {
            "llm": {
                # 多 provider 模式
                "providers": {
                    "openrouter": {
                        "api_key": "",
                        "model": "openai/gpt-4o-mini",
                        "base_url": "https://openrouter.ai/api/v1"
                    },
                    "openai": {
                        "api_key": "",
                        "model": "gpt-4o-mini",
                        "base_url": "https://api.openai.com/v1"
                    }
                },
                "default": "openrouter",
                
                # 共用參數
                "temperature": 0.7,
                "max_tokens": 2048
            },
            "agent": {
                "system_prompt": "你是個有用且簡潔的助理。",
                "max_history": 50
            },
            "storage": {
                "type": "memory",
                "path": "./data"
            },
            "channels": {
                "telegram": {
                    "enabled": False,
                    "token": ""
                },
                "console": {
                    "enabled": True
                }
            },
            "log": {
                "enabled": True,
                "retention_days": 365,
                "level": "INFO"
            }
        }
        
        # 寫入檔案
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
        
        return path
    
    def to_dict(self) -> dict:
        """轉成 dict"""
        return {
            "llm": {
                "providers": self.llm.providers,
                "default": self.llm.default,
                "api_key": self.llm.api_key,
                "model": self.llm.model,
                "base_url": self.llm.base_url,
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
            },
            "agent": {
                "system_prompt": self.agent.system_prompt,
                "max_history": self.agent.max_history,
            },
            "storage": {
                "type": self.storage.type,
                "path": self.storage.path,
            },
            "channels": {
                "telegram": {
                    "enabled": self.channels.telegram.enabled,
                    "token": self.channels.telegram.token,
                },
                "console": {
                    "enabled": self.channels.console.enabled,
                },
            },
            "log": {
                "enabled": self.log.enabled,
                "retention_days": self.log.retention_days,
                "level": self.log.level,
            }
        }
    
    def save(self, path: str | Path):
        """儲存到 JSON 檔案"""
        path = Path(path)
        
        if path.suffix == ".json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        else:
            raise ValueError(f"不支援的格式: {path.suffix}，只支援 .json")
