"""
minibot/main.py - 入口點

兩種模式：
1. foreground（前台互動模式）
2. background（背景服務模式）

"""

import asyncio
import os
import sys
import signal

from minibot.agent import AgentLoop, AgentConfig as BotAgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage, StorageProvider
from minibot.bus.message_queue import MessageQueue
from minibot.bus.message import UserMessage
from minibot.config import Config


# ============================================
# 共用設定
# ============================================

def create_storage(config: Config) -> StorageProvider:
    """根據設定建立 Storage"""
    storage_type = config.storage.type
    
    if storage_type == "memory":
        return MemoryStorage()
    elif storage_type == "file":
        from minibot.storage import FileStorage
        return FileStorage(base_path=config.storage.path)
    elif storage_type == "sqlite":
        from minibot.storage import SQLiteStorage
        return SQLiteStorage(path=config.storage.path)
    else:
        return MemoryStorage()


def create_agent(config: Config):
    """建立 Agent 和 Queue"""
    # 根據 base_url 決定用哪個 LLM
    if config.llm.base_url and "openrouter" in config.llm.base_url:
        from minibot.llms import OpenRouterLLM
        llm = OpenRouterLLM(
            api_key=config.llm.api_key,
            default_model=config.llm.model
        )
    else:
        from minibot.llms import OpenAILLM
        llm = OpenAILLM(
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            default_model=config.llm.model
        )
    
    # 建立 Agent 設定
    agent_config = BotAgentConfig(
        system_prompt=config.agent.system_prompt,
        model=config.llm.model,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
    )
    
    # 建立 Storage
    storage = create_storage(config)
    
    # 建立 Agent
    agent = AgentLoop(agent_config, llm, storage)
    mq = MessageQueue(agent)
    
    return agent, mq


# ============================================
# 前台互動模式
# ============================================

async def run_foreground(config: Config):
    """前台互動模式"""
    agent, mq = create_agent(config)
    
    # 收到回覆時印出
    async def on_response(response):
        print(f"\n🤖 [{response.chat_id}]: {response.text}")
    
    mq.on_response = on_response
    
    # 啟動處理迴圈
    processor = asyncio.create_task(mq.process_queue())
    
    print("🤖 mini-bot 啟動了！（前台模式）")
    print("-" * 40)
    print("輸入格式：")
    print("  @<chat_id> <訊息>   → 發送到指定聊天室")
    print("  <訊息>              → 發送到預設對話（default）")
    print("  /exit               → 離開")
    print("-" * 40)
    
    while True:
        try:
            line = input("\n你: ").strip()
        except EOFError:
            break
        
        if not line:
            continue
        
        if line.lower() == "/exit":
            print("再見！")
            await mq.stop()
            await processor
            break
        
        # 解析 chat_id
        chat_id = "default"
        text = line
        
        if line.startswith("@"):
            parts = line[1:].split(" ", 1)
            chat_id = parts[0]
            text = parts[1] if len(parts) > 1 else ""
        
        if not text:
            continue
        
        user_msg = UserMessage(text=text, chat_id=chat_id)
        await mq.enqueue(user_msg)


# ============================================
# 背景服務模式
# ============================================

class BackgroundRunner:
    """背景執行器"""
    
    def __init__(self, config: Config, log_file=None):
        self.config = config
        self.log_file = log_file or "/tmp/minibot.log"
        self.agent = None
        self.mq = None
        self.loop = None
    
    async def start(self):
        """啟動服務"""
        self.agent, self.mq = create_agent(self.config)
        
        # 收到回覆時寫入日誌
        async def on_response(response):
            log_msg = f"[{response.chat_id}] {response.text}\n"
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_msg)
        
        self.mq.on_response = on_response
        
        # 啟動處理迴圈
        self.loop = asyncio.create_task(self.mq.process_queue())
        
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"=== mini-bot 啟動於 {asyncio.get_event_loop().time()} ===\n")
        
        print(f"🤖 mini-bot 背景啟動中...")
        print(f"   PID: {os.getpid()}")
        print(f"   Log: {self.log_file}")
        print(f"   使用 @<chat_id> <訊息> 發送訊息")
        
        # 等待直到被停止
        try:
            await self.mq.queue.get()
        except:
            pass
    
    async def stop(self):
        """停止服務"""
        if self.mq:
            await self.mq.stop()
        if self.loop:
            await self.loop
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("=== mini-bot 已停止 ===\n")


async def run_background(config: Config):
    """背景服務模式"""
    runner = BackgroundRunner(config)
    
    # 設定訊號處理
    def signal_handler(sig, frame):
        print("\n收到停止訊號，正在關閉...")
        asyncio.create_task(runner.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    await runner.start()


# ============================================
# Daemon 模式（真正背景執行）
# ============================================

def run_daemon(config: Config):
    """以 daemon 方式執行（Unix only）"""
    # daemon 只在 Unix 上可用
    try:
        import daemon
    except ImportError:
        print("錯誤：daemon 模式只能在 Unix 系統上使用")
        print("請使用 foreground 模式：python -m minibot.main foreground")
        sys.exit(1)
    
    log_file = "/tmp/minibot.log"
    
    # 建立 PID 檔
    pid_file = "/tmp/minibot.pid"
    
    if os.path.exists(pid_file):
        with open(pid_file, "r") as f:
            old_pid = f.read().strip()
        print(f"mini-bot 似乎正在執行中（PID: {old_pid}）")
        print("如果要重新啟動，請先執行：minibot stop")
        sys.exit(1)
    
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    
    # 進入 daemon 模式
    with daemon.DaemonContext():
        # 建立新的 event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 執行
        runner = BackgroundRunner(config, log_file)
        
        def signal_handler(sig, frame):
            loop.create_task(runner.stop())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        try:
            loop.run_until_complete(runner.start())
        finally:
            loop.close()
            if os.path.exists(pid_file):
                os.remove(pid_file)


# ============================================
# 停止服務
# ============================================

def stop_daemon():
    """停止背景服務（Unix only）"""
    # daemon 只在 Unix 上可用
    try:
        import daemon
    except ImportError:
        print("錯誤：daemon 模式只能在 Unix 系統上使用")
        sys.exit(1)
    
    pid_file = "/tmp/minibot.pid"
    
    if not os.path.exists(pid_file):
        print("mini-bot 沒有在執行")
        return
    
    with open(pid_file, "r") as f:
        pid = int(f.read().strip())
    
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"已發送停止訊號到 PID {pid}")
        
        # 等待一下，然後刪除 PID 檔
        import time
        time.sleep(1)
        if os.path.exists(pid_file):
            os.remove(pid_file)
        
        print("mini-bot 已停止")
    except ProcessLookupError:
        print(f"找不到 PID {pid}，可能是已經結束")
        if os.path.exists(pid_file):
            os.remove(pid_file)
    except PermissionError:
        print(f"沒有權限停止 PID {pid}")


# ============================================
# 主程式
# ============================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="mini-bot CLI")
    parser.add_argument(
        "command", 
        choices=["start", "stop", "foreground"],
        nargs="?",
        default="foreground",
        help="命令：start=背景啟動, stop=停止, foreground=前台"
    )
    parser.add_argument(
        "-c", "--config",
        help="設定檔路徑（預設 ~/.minibot/workspace/nanobot.json）"
    )
    parser.add_argument("--log", "-l", help="日誌檔案")
    
    args = parser.parse_args()
    
    # 從 JSON 檔案讀取設定
    config = Config.load(args.config)
    
    # 檢查必要設定（如果沒填 API Key，給警告但允許執行）
    if not config.is_llm_configured:
        print("⚠️  警告：請在 nanobot.json 設定 LLM API Key")
        print("    位置：~/.minibot/workspace/nanobot.json")
    
    if args.command == "stop":
        stop_daemon()
    elif args.command == "start":
        run_daemon(config)
    elif args.command == "foreground":
        asyncio.run(run_foreground(config))


if __name__ == "__main__":
    main()