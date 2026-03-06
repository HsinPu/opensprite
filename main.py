#!/usr/bin/env python3
"""mini-bot 啟動入口"""

import os
import sys
import subprocess
import venv
from pathlib import Path

BOT_DIR = Path(__file__).parent
VENV_DIR = BOT_DIR / ".venv"
MAIN_SCRIPT = BOT_DIR / "src" / "minibot" / "main.py"


def setup_venv():
    """建立虛擬環境（如果不存在）"""
    if not VENV_DIR.exists():
        print("🔧 建立虛擬環境...")
        venv.create(VENV_DIR, with_pip=True)
        print("✅ 虛擬環境已建立")


def get_venv_python():
    """取得虛擬環境的 Python 路徑"""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def install_deps():
    """安裝依賴"""
    pip = VENV_DIR / "bin" / "pip"
    req_file = BOT_DIR / "requirements.txt"
    
    # 檢查依賴是否已安裝
    result = subprocess.run([str(pip), "show", "openai"], capture_output=True)
    if result.returncode == 0:
        return  # 已安裝
    
    if req_file.exists():
        print("📦 安裝依賴...")
        subprocess.run([str(pip), "install", "-r", str(req_file)], check=True)
        print("✅ 依賴已安裝")


def run_bot():
    """啟動機器人"""
    print("🚀 啟動 mini-bot...", flush=True)
    
    # 把 src 目錄加入 Python 路徑
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BOT_DIR / "src")
    env["PYTHONUNBUFFERED"] = "1"
    
    # 使用虛擬環境的 Python 執行（維持在前景）
    python = get_venv_python()
    subprocess.run([str(python), str(MAIN_SCRIPT)], cwd=BOT_DIR, env=env)


def main():
    """主程式"""
    # 建立並進入虛擬環境
    setup_venv()
    install_deps()
    run_bot()


if __name__ == "__main__":
    main()
