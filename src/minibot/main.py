"""
minibot/main.py - 入口點（Queue 版本）

支援多個對話同時進行：
- 每個聊天室（chat_id）有獨立的對話歷史
- 輸入格式：@chat_id 訊息
- /reset 清除歷史，/exit 離開

範例：
  @123 你好     → 發送到 chat_id=123
  你好          → 發送到預設對話（default）
  @123 /reset  → 只清除 chat_id=123 的歷史

"""

import asyncio
import os
from dotenv import load_dotenv

from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.queue import MessageQueue
from minibot.message import UserMessage


async def main():
    load_dotenv()
    
    # ============================================
    # 1. 讀取設定
    # ============================================
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("錯誤：請設定 OPENAI_API_KEY")
        return
    
    model = os.getenv("MODEL", "gpt-4o-mini")
    base_url = os.getenv("BASE_URL", None)
    system_prompt = os.getenv("SYSTEM_PROMPT", "你是個有用且簡潔的助理。")
    
    # ============================================
    # 2. 建立 LLM 和 Agent
    # ============================================
    llm = OpenAILLM(
        api_key=api_key,
        base_url=base_url,
        default_model=model
    )
    
    config = AgentConfig(
        system_prompt=system_prompt,
        model=model
    )
    agent = AgentLoop(config, llm)
    
    # ============================================
    # 3. 建立 Queue
    # ============================================
    mq = MessageQueue(agent)
    
    # 定義收到回覆時要做什麼
    async def on_response(response):
        print(f"\n🤖 [{response.chat_id}]: {response.text}")
    
    mq.on_response = on_response
    
    # ============================================
    # 4. 啟動處理迴圈（背景執行）
    # ============================================
    processor = asyncio.create_task(mq.process_queue())
    
    # ============================================
    # 5. 主迴圈：接收輸入
    # ============================================
    print("🤖 minibot 啟動了！")
    print("-" * 40)
    print("輸入格式：")
    print("  @<chat_id> <訊息>   → 發送到指定聊天室")
    print("  <訊息>              → 發送到預設對話（default）")
    print("  @<chat_id> /reset   → 清除該聊天室歷史")
    print("  /reset              → 清除所有歷史")
    print("  /exit               → 離開")
    print("-" * 40)
    
    while True:
        try:
            line = input("\n你: ").strip()
        except EOFError:
            break
        
        if not line:
            continue
        
        # 離開
        if line.lower() == "/exit":
            print("再見！")
            await mq.stop()
            await processor
            break
        
        # 重置所有歷史
        if line.lower() == "/reset":
            for conv in mq.conversations.values():
                conv.messages.clear()
            print("✅ 所有歷史已清除")
            continue
        
        # 解析 chat_id
        if line.startswith("@"):
            # 格式：@chat_id 訊息 或 @chat_id /reset
            parts = line[1:].split(" ", 1)
            chat_id = parts[0]
            
            # 檢查是否要重置特定對話
            if len(parts) > 1 and parts[1].lower() == "/reset":
                mq.reset_conversation(chat_id)
                print(f"✅ Chat {chat_id} 的歷史已清除")
                continue
            
            text = parts[1] if len(parts) > 1 else ""
        else:
            # 預設 chat_id
            chat_id = "default"
            text = line
        
        if not text:
            print("錯誤：訊息不能為空")
            continue
        
        # 加入佇列
        user_msg = UserMessage(text=text, chat_id=chat_id)
        await mq.enqueue(user_msg)


if __name__ == "__main__":
    asyncio.run(main())