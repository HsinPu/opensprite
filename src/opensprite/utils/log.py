"""
opensprite/utils/log.py - 日誌模組

功能：
- 每日輪轉日誌檔案
- 自動清理過期日誌（可設定保留天數）
- 同時輸出到檔案和螢幕

日誌位置：~/.opensprite/logs/opensprite-YYYY-MM-DD.log
"""

from pathlib import Path
from loguru import logger
import sys

# 日誌目錄
LOG_DIR = Path.home() / ".opensprite" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 移除預設的 stderr 輸出（我們會自己加）
logger.remove()

# 追蹤是否已初始化
_initialized = False


def setup_log(config=None, console: bool = True):
    global _initialized
    
    # 防止重複初始化
    if _initialized:
        return logger
    
    """
    初始化日誌
    
    參數：
        config: LogConfig 物件，若為 None 则使用預設值
        console: 是否輸出到螢幕，預設 True
    """
    # 取得設定值
    if config:
        retention = f"{config.retention_days} days"
        level = config.level
    else:
        retention = "365 days"
        level = "INFO"
    
    # 設定輸出到檔案
    logger.add(
        LOG_DIR / "opensprite-{time:YYYY-MM-DD}.log",
        rotation="1 day",           # 每日新檔案
        retention=retention,        # 保留天數
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level=level,
    )
    
    # 設定輸出到螢幕（可選，簡化格式）
    if console:
        logger.add(
            sys.stderr,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            level=level,
            colorize=True,
        )
    
    _initialized = True
    
    return logger


# 預設設定（未傳入 config 時使用）
setup_log()

__all__ = ["logger", "setup_log"]
