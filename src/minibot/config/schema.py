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

class LLMsConfig(BaseModel):
    """LLM 設定（從 JSON 讀取）"""
    
    api_key: str  # 必填
    model: str  # 必填
    base_url: str | None = None  # 可選
    temperature: float  # 必填
    max_tokens: int  # 必填


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
    ):
        self.llm = llm
        self.agent = agent
        self.storage = storage
        self.channels = channels
    
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
        
        return cls(llm=llm, agent=agent, storage=storage, channels=channels)
    
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
            
            # 如果 workspace 裡沒有 config，複製預設範本
            if not config_path.exists():
                default_config = Path("src/minibot/config/nanobot.json")
                if default_config.exists():
                    import shutil
                    shutil.copy(default_config, config_path)
                    print(f"已建立設定檔: {config_path}")
                else:
                    raise FileNotFoundError(f"找不到預設設定檔，也沒有現存的設定檔")
            
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
    
    def to_dict(self) -> dict:
        """轉成 dict"""
        return {
            "llm": {
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
