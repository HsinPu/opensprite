# mini-bot 完整流程圖

## 訊息處理流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        mini-bot 訊息處理流程                                 │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐
  │  使用者     │
  │ (Telegram/ │
  │  Console)  │
  └──────┬──────┘
         │
         ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                        1. Channel Adapter                              │
  │  ┌──────────────────────────────────────────────────────────────────┐ │
  │  │  TelegramAdapter / Console                                        │ │
  │  │  - 接收平台原始訊息                                                │ │
  │  │  - 轉成 InboundMessage                                            │ │
  │  │  - mq.enqueue_raw() 丟到 Queue                                   │ │
  │  └──────────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                        2. MessageQueue (Bus 版)                       │
  │                                                                         │
  │   ┌───────────────────────────────────────────────────────────────┐   │
  │   │                    inbound Queue                               │   │
  │   │  ┌─────────────────────────────────────────────────────────┐  │   │
  │   │  │ InboundMessage {                                      │  │   │
  │   │  │   channel: "telegram",                               │  │   │
  │   │  │   chat_id: "123",                                    │  │   │
  │   │  │   content: "你好",                                   │  │   │
  │   │  │   sender_id: "user456"                               │  │   │
  │   │  │ }                                                     │  │   │
  │   │  └─────────────────────────────────────────────────────────┘  │   │
  │   └───────────────────────────────────────────────────────────────┘   │
  │                                    │                                    │
  │                                    ▼ (spawn 成獨立 task)              │
  │   ┌───────────────────────────────────────────────────────────────┐   │
  │   │              _process_message()                                │   │
  │   │                                                            │   │
  │   │   ┌───────────────────────────────────────────────────────┐   │   │
  │   │   │           AgentLoop.process()                        │   │   │
  │   │   │                                                       │   │   │
  │   │   │  ┌─────────────────────────────────────────────┐    │   │   │
  │   │   │  │     ContextBuilder.build_messages()        │    │   │   │
  │   │   │  │                                             │    │   │   │
  │   │   │  │  system_prompt =                           │    │   │   │
  │   │   │  │    ┌─────────────────────────────────┐    │    │   │   │
  │   │   │  │    │ FileContextBuilder              │    │    │   │   │
  │   │   │  │    │ - IDENTITY.md                  │    │    │   │   │
  │   │   │  │    │ - AGENTS.md                    │    │    │   │   │
  │   │   │  │    │ - SOUL.md                      │    │    │   │   │
  │   │   │  │    │ - USER.md                      │    │    │   │   │
  │   │   │  │    │ - TOOLS.md                     │    │    │   │   │
  │   │   │  │    │ - memory/MEMORY.md             │    │    │   │   │
  │   │   │  │    └─────────────────────────────────┘    │    │   │   │
  │   │   │  │                                             │    │   │   │
  │   │   │  │  + history (對話歷史)                      │    │   │   │
  │   │   │  │  + runtime_context (現在時間、channel)    │    │   │   │
  │   │   │  └─────────────────────────────────────────────┘    │   │   │
  │   │   │                                                       │   │   │
  │   │   │  ┌─────────────────────────────────────────────┐    │   │   │
  │   │   │  │           LLMProvider.chat()               │    │   │   │
  │   │   │  │           (OpenAI / Anthropic / ... )      │    │   │   │
  │   │   │  └─────────────────────────────────────────────┘    │   │   │
  │   │   │                                                       │   │   │
  │   │   │  ┌─────────────────────────────────────────────┐    │   │   │
  │   │   │  │           StorageProvider                  │    │   │   │
  │   │   │  │           (存對話歷史)                      │    │   │   │
  │   │   │  └─────────────────────────────────────────────┘    │   │   │
  │   │   │                                                       │   │   │
  │   │   └───────────────────────────────────────────────────────┘   │   │
  │   │                                                            │   │
  │   └───────────────────────────────────────────────────────────────┘   │
  │                                    │                                    │
  │                                    ▼                                    │
  │   ┌───────────────────────────────────────────────────────────────┐   │
  │   │                    outbound Queue                             │   │
  │   │  ┌─────────────────────────────────────────────────────────┐  │   │
  │   │  │ OutboundMessage {                                     │  │   │
  │   │  │   channel: "telegram",                               │  │   │
  │   │  │   chat_id: "123",                                    │  │   │
  │   │  │   content: "嗨～"                                    │  │   │
  │   │  │ }                                                     │  │   │
  │   │  └─────────────────────────────────────────────────────────┘  │   │
  │   └───────────────────────────────────────────────────────────────┘   │
  │                                    │                                    │
  │                                    ▼ (獨立 task)                      │
  │   ┌───────────────────────────────────────────────────────────────┐   │
  │   │              _consume_outbound()                              │   │
  │   │                                                            │   │
  │   │   ┌───────────────────────────────────────────────────────┐   │   │
  │   │   │            on_response callback                       │   │   │
  │   │   │            (發送到對應頻道)                           │   │   │
  │   │   └───────────────────────────────────────────────────────┘   │   │
  │   └───────────────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                        3. Channel Adapter 發送                         │
  │                                                                         │
  │   TelegramAdapter.send() → Telegram API → 發送訊息                    │
  │   Console callback → print() → 終端機顯示                              │
  └─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌─────────────┐
  │  使用者收到 │
  │  回覆       │
  └─────────────┘
```

---

## 檔案對應關係

```
workspace/                      src/minibot/
┌─────────────────┐             ┌──────────────────────────┐
│ AGENTS.md       │ ◀── load ──│ workspace.py             │
│ SOUL.md         │             │ - load_bootstrap_files() │
│ USER.md         │             │ - load_memory()         │
│ IDENTITY.md     │             │ - sync_templates()      │
│ TOOLS.md        │             └──────────────────────────┘
│ memory/         │
│   MEMORY.md     │
└─────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│ context/file_builder.py                   │
│ - FileContextBuilder.build_system_prompt()│
│   組合成完整的 system prompt              │
└──────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│ agent.py                                 │
│ - AgentLoop.process()                    │
│   用 ContextBuilder 組的 prompt 叫 LLM  │
└──────────────────────────────────────────┘
```

---

## 核心要點總結

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Channel    │────▶│   Queue      │────▶│    Agent     │
│  (Adapter)   │     │  (MessageBus)│     │   (Loop)     │
│              │     │              │     │              │
│ - Telegram   │     │ - inbound   │     │ - process()  │
│ - Discord    │     │ - outbound  │     │ - call_llm() │
│ - Console    │     │              │     │ - storage    │
└──────────────┘     └──────────────┘     └──────────────┘
                                              │
                                              ▼
                         ┌──────────────────────────────────────────┐
                         │              LLMProvider                  │
                         │  - OpenAI / Anthropic / LiteLLM / ...  │
                         └──────────────────────────────────────────┘
```

---

## 可替換的元件（依賴注入）

### StorageProvider

| 實作 | 說明 |
|------|------|
| MemoryStorage | 記憶體儲存（目前使用） |
| FileStorage | 檔案儲存（未來） |
| SQLiteStorage | SQLite 儲存（未來） |

### ContextBuilder

| 實作 | 說明 |
|------|------|
| FileContextBuilder | 從 workspace 檔案讀取（目前使用） |
| （其他） | 未來可以新增其他實作 |

### LLMProvider

| 實作 | 說明 |
|------|------|
| OpenAILLM | OpenAI API（目前使用） |
| AnthropicLLM | Anthropic API（未來） |
| LiteLLM | LiteLLM 統一介面（未來） |

---

## 資料夾結構

```
mini-bot/
├── src/minibot/
│   ├── __init__.py           # 匯出主要類別
│   ├── agent.py              # Agent Loop 核心
│   ├── main.py               # 進入點（foreground/background）
│   ├── message.py            # 統一訊息格式
│   ├── queue.py              # 訊息佇列（Bus 版）
│   ├── workspace.py           # 工作區輔助函式
│   │
│   ├── bus/                  # 訊息匯流排
│   │   ├── events.py         # 訊息結構（Inbound/OutboundMessage）
│   │   └── queue.py          # MessageBus 類別
│   │
│   ├── channels/              # 頻道適配器
│   │   └── telegram.py       # Telegram Adapter
│   │
│   ├── context/              # Prompt 上下文建構
│   │   ├── builder.py        # ContextBuilder 介面
│   │   └── file_builder.py   # 檔案式實作
│   │
│   ├── llms/                 # LLM 提供者
│   │   ├── base.py          # LLMProvider 介面
│   │   └── openai.py        # OpenAI 實作
│   │
│   ├── storage/              # 儲存提供者
│   │   ├── base.py          # StorageProvider 介面
│   │   └── memory.py        # 記憶體實作
│   │
│   ├── config/               # 設定
│   │   └── schema.py        # 設定 schema
│   │
│   └── templates/            # 範本（會同步到 workspace）
│       ├── AGENTS.md
│       ├── SOUL.md
│       ├── USER.md
│       ├── IDENTITY.md
│       ├── TOOLS.md
│       └── memory/
│           └── MEMORY.md
│
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 使用方式

### 基本使用

```python
from pathlib import Path
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM
from minibot.storage import MemoryStorage
from minibot.context import FileContextBuilder
from minibot.workspace import get_workspace_path

# 1. 建立 ContextBuilder（從 workspace 讀檔案）
workspace = get_workspace_path()
context_builder = FileContextBuilder(workspace)

# 2. 建立 Storage
storage = MemoryStorage()

# 3. 建立 LLM
llm = OpenAILLM(api_key="your-key")

# 4. 建立 Agent
config = AgentConfig()
agent = AgentLoop(config, llm, storage, context_builder)

# 5. 使用
from minibot.message import UserMessage
user_msg = UserMessage(text="你好", chat_id="123", channel="telegram")
response = await agent.process(user_msg)
```

### Console 模式

```bash
python -m minibot.main foreground
```

### 背景模式

```bash
python -m minibot.main start
```
