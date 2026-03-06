"""
minibot/utils/log.py - 日誌模組

功能：
- 每日輪轉日誌檔案
- 自動清理過期日誌（保留 7 天）

日誌位置：~/.minibot/logs/minibot-YYYY-MM-DD.log
"""

from pathlib import Path
from loguru import logger

# 日誌目錄
LOG_DIR = Path.home() / ".minibot" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 設定日誌
logger.add(
    LOG_DIR / "minibot-{time:YYYY-MM-DD}.log",
    rotation="1 day",           # 每日新檔案
    retention="7 days",        # 保留 7 天
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="INFO",
)

__all__ = ["logger"]
