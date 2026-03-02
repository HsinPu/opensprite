# mini-bot 🤖

超輕量個人 AI 助理框架

## 特色

- **極簡核心**：只有 3 個檔案（agent.py, message.py, llms/）
- **模組化設計**：訊息來源、LLM 廠商都可以替換
- **多對話支援**：每個聊天室有獨立的對話歷史
- **非同步處理**：訊息進入佇列，背景處理

## 架構

```
src/minibot/
├── llms/              # LLM 實作（可替換）
│   ├── base.py       # LLMProvider 介面
│   └── openai.py     # OpenAI 實作
├── channels/         # 訊息來源（可擴充）
│   └── telegram.py  # Telegram Adapter
├── message.py        # 統一訊息格式
├── agent.py          # Agent 核心
├── queue.py          # 訊息佇列
└── main.py           # 入口點
```

### 設計原則

| 層 | 說明 |
|---|---|
| **channels** | 訊息來源（Telegram、Discord、Console）→ 轉成 `UserMessage` |
| **agent** | 核心邏輯：接收 `UserMessage` → 處理 → 回傳 `AssistantMessage` |
| **llms** | LLM 實作（OpenAI、Anthropic、DeepSeek）→ 統一介面 |
| **queue** | 支援多對話、非同步處理 |

## 安裝

```bash
# clone 後安裝
cd mini-bot
pip install -e .

# 或只安裝依賴
pip install -r requirements.txt
```

## 快速開始

### 1. 設定環境變數

```bash
# 建立 .env 檔
cp .env.example .env

# 編輯 .env，填入你的 API Key
OPENAI_API_KEY=sk-xxxxx
MODEL=gpt-4o-mini
```

### 2. 執行（Console 模式）

```bash
python -m minibot.main
```

### 3. 輸入格式

```
你好              → 發送到 default 對話
@123 你好        → 發送到 chat_id=123
@123 /reset      → 清除 chat_id=123 的歷史
/reset           → 清除所有歷史
/exit            → 離開
```

## 使用 LLM

### OpenAI（預設）

```python
from minibot.llms import OpenAILLM
from minibot.agent import AgentLoop, AgentConfig
from minibot.message import UserMessage

llm = OpenAILLM(
    api_key="sk-xxx",
    base_url=None,           # 或用 OpenRouter: "https://openrouter.ai/v1"
    default_model="gpt-4o-mini"
)

config = AgentConfig(system_prompt="你是個簡潔的助理。")
agent = AgentLoop(config, llm)

# 使用
user_msg = UserMessage(text="你好", chat_id="123")
response = await agent.process(user_msg)
print(response.text)
```

### 之後可擴充

- `llms/anthropic.py` → Claude
- `llms/deepseek.py` → DeepSeek
- `llms/openrouter.py` → OpenRouter

## 使用 Telegram

```python
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.channels.telegram import TelegramAdapter
from minibot.message import UserMessage

# 建立 Agent
llm = OpenAILLM(api_key="your-key")
agent = AgentLoop(AgentConfig(system_prompt="你是個助理。"), llm)

# 建立 Telegram Adapter
telegram = TelegramAdapter(bot_token="your-telegram-token")

# 處理訊息
async def handle(adapter, raw_update, user_msg):
    response = await agent.process(user_msg)
    await adapter.send(response)

# 啟動
asyncio.run(telegram.run(handle))
```

## 資料夾說明

| 檔案 | 用途 |
|------|------|
| `message.py` | 統一訊息格式（`UserMessage`, `AssistantMessage`, `MessageAdapter`） |
| `agent.py` | Agent 核心（維護對話歷史、呼叫 LLM） |
| `llms/base.py` | LLM 介面（`LLMProvider`, `ChatMessage`, `LLMResponse`） |
| `queue.py` | 訊息佇列（支援多對話、非同步） |
| `main.py` | Console 入口點 |

## License

MIT
