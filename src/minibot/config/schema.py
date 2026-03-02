"""
minibot/config/schema.py - 設定檔定義

從 YAML 檔案讀取設定（無預設值）
"""

import yaml
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


# ============================================
# LLM 設定
# ============================================

class LLMsConfig(BaseModel):
    """LLM 設定（從 YAML 讀取）"""
    
    api_key: str  # 必填
    model: str  # 必填
    base_url: str | None = None  # 可選
    temperature: float  # 必填
    max_tokens: int  # 必填


# ============================================
# Agent 設定
# ============================================

class AgentConfig(BaseModel):
    """Agent 設定（從 YAML 讀取）"""
    
    system_prompt: str  # 必填
    max_history: int  # 必填


# ============================================
# Storage 設定
# ============================================

class StorageConfig(BaseModel):
    """Storage 設定（從 YAML 讀取）"""
    
    type: str  # 必填：memory / file / sqlite
    path: str  # 必填


# ============================================
# Channels 設定
# ============================================

class TelegramConfig(BaseModel):
    """Telegram 設定（從 YAML 讀取）"""
    
    enabled: bool  # 必填
    token: str  # 必填（enabled=true 時）


class ConsoleConfig(BaseModel):
    """Console 設定（從 YAML 讀取）"""
    
    enabled: bool  # 必填


class ChannelsConfig(BaseModel):
    """Channels 設定（從 YAML 讀取）"""
    
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)


# ============================================
# 主設定
# ============================================

class Config:
    """主設定檔（從 YAML 讀取）"""
    
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
    def from_yaml(cls, path: str | Path) -> "Config":
        """從 YAML 檔案讀取設定"""
        path = Path(path)
        
        if not path.exists():
            raise FileNotFoundError(f"設定檔不存在: {path}")
        
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
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
            path: 設定檔路徑，預設搜尋 src/minibot/config/config.yaml
        """
        if path is None:
            # 預設路徑搜尋順序
            possible_paths = [
                Path("src/minibot/config/config.yaml"),
                Path.home() / ".config" / "minibot" / "config.yaml",
            ]
            
            for p in possible_paths:
                if p.exists():
                    path = p
                    break
            else:
                raise FileNotFoundError(
                    f"找不到設定檔，搜尋過的路徑: {[str(p) for p in possible_paths]}"
                )
        
        return cls.from_yaml(path)
    
    @property
    def is_llm_configured(self) -> bool:
        """檢查 LLM 是否已設定"""
        return bool(self.llm.api_key)
    
    def to_dict(self) -> dict:
        """轉成 dict（可用來寫回 YAML）"""
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
        """儲存到 YAML 檔案"""
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, allow_unicode=True, default_flow_style=False)
