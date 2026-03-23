# OpenSprite

超輕量個人 AI 助理框架

## 簡介

OpenSprite 是一個模組化的個人 AI 助理框架，提供統一的 LLM 介面、多頻道支援、工具呼叫與長期記憶功能。

## 核心概念

### 設計原則

1. **依賴注入**：所有元件（LLM、Storage、ContextBuilder、Tools）皆可替換
2. **統一訊息格式**：`UserMessage` 和 `AssistantMessage` 抽象化頻道差異
3. **非同步優先**：基於 asyncio 的非阻塞操作
4. **多對話支援**：每個聊天室擁有獨立的對話歷史
5. **訊息佇列**：非同步處理，支援背景執行

### 訊息流程

```
使用者 → Channel → MessageQueue → AgentLoop → LLM → Storage
                                    ↓
                                 Tools
                                    ↓
使用者 ← Channel ← MessageQueue ← Response
```

## 安裝

### Linux 安裝

```bash
cd opensprite
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

安裝完成後可直接使用 CLI：

```bash
opensprite
```

### 會安裝到哪裡

- 使用虛擬環境安裝時，套件會安裝到 `.venv/lib/python3.x/site-packages/`
- CLI 指令會安裝到 `.venv/bin/opensprite`
- 啟用虛擬環境後，直接執行 `opensprite` 就會使用這個版本
- 若使用 `python3 -m pip install --user .`，通常會安裝到 `~/.local/lib/python3.x/site-packages/`，CLI 在 `~/.local/bin/opensprite`
- 若直接安裝到系統 Python，通常會在系統的 `site-packages`，CLI 常見位置是 `/usr/local/bin/opensprite`

可用以下指令確認實際安裝位置：

```bash
which opensprite
python -c "import opensprite; print(opensprite.__file__)"
```

### Linux 可編輯模式安裝（開發用）

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 解除安裝

```bash
# 從目前啟用的環境移除套件
python -m pip uninstall opensprite

# 清除配置檔與資料（可選）
rm -rf ~/.opensprite
```

- 若是使用虛擬環境安裝，也可以直接刪除整個 `.venv/`
- 若是用 `--user` 安裝，執行 `python3 -m pip uninstall opensprite`
- 若是安裝到系統 Python，可能需要使用 `sudo python3 -m pip uninstall opensprite`

### 啟動

```bash
# 安裝成 CLI 後直接執行
opensprite

# 或用 module 方式執行
python -m opensprite.main
```

## 快速開始

### 1. 配置

首次執行會自動建立 `~/.opensprite/opensprite.json`：

```json
{
  "llm": {
    "providers": {
      "openrouter": {
        "api_key": "your-api-key",
        "enabled": true,
        "model": "openai/gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1"
      }
    },
    "default": "openrouter",
    "temperature": 0.7,
    "max_tokens": 8192
  },
  "storage": {
    "type": "sqlite",
    "path": "~/.opensprite/data/sessions.db"
  },
  "channels": {
    "telegram": {
      "enabled": false,
      "token": "",
      "connect_timeout": 10,
      "read_timeout": 30,
      "write_timeout": 30,
      "pool_timeout": 30,
      "get_updates_connect_timeout": 10,
      "get_updates_read_timeout": 30,
      "get_updates_write_timeout": 30,
      "get_updates_pool_timeout": 30,
      "poll_timeout": 10,
      "bootstrap_retries": 3,
      "drop_pending_updates": false
    },
    "console": {
      "enabled": true
    }
  },
  "tools": {
    "max_tool_iterations": 100,
    "web_search": {
      "provider": "brave",
      "brave_api_key": "",
      "tavily_api_key": "",
      "jina_api_key": "",
      "searxng_url": "https://searx.be",
      "max_results": 10,
      "proxy": ""
    },
    "web_fetch": {
      "max_chars": 50000,
      "timeout": 30,
      "prefer_trafilatura": true,
      "firecrawl_api_key": ""
    }
  },
  "memory": {
    "max_history": 50,
    "threshold": 30
  },
  "search": {
    "enabled": false,
    "provider": "lancedb",
    "path": "~/.opensprite/data/lancedb",
    "history_top_k": 5,
    "knowledge_top_k": 5
  }
}
```

### 2. 執行

```bash
# CLI 模式
opensprite

# module 模式
python -m opensprite.main
```

### 3. Console 指令

| 指令 | 說明 |
|------|------|
| `你好` | 發送到 default 對話 |
| `@123 你好` | 發送到 chat_id=123 |
| `@123 /reset` | 清除 chat_id=123 的歷史 |
| `/reset` | 清除所有歷史 |
| `/exit` | 離開 |

## 使用方式

### Python API

```python
from opensprite.agent import AgentLoop, AgentConfig
from opensprite.llms import OpenAILLM
from opensprite.storage import MemoryStorage
from opensprite.context import FileContextBuilder
from opensprite.message import UserMessage

# 初始化
llm = OpenAILLM(api_key="sk-xxx", default_model="gpt-4o-mini")
storage = MemoryStorage()
context_builder = FileContextBuilder()
agent = AgentLoop(AgentConfig(), llm, storage, context_builder)

# 處理訊息
user_msg = UserMessage(text="你好", chat_id="123")
response = await agent.process(user_msg)
print(response.text)
```

### Telegram

```python
from opensprite.channels.telegram import TelegramAdapter

telegram = TelegramAdapter(bot_token="your-token")
asyncio.run(telegram.run(handle))
```

## 架構

```
src/opensprite/
├── agent/           # AgentLoop 核心
├── llms/            # LLM Providers
│   ├── base.py     # LLMProvider 介面
│   ├── openai.py
│   ├── minimax.py
│   └── openrouter.py
├── bus/             # Message Bus
│   ├── message.py
│   ├── dispatcher.py
│   ├── message_bus.py
│   └── events.py
├── storage/         # Storage Providers
│   ├── base.py
│   ├── memory.py
│   └── sqlite.py
├── context/         # Context Builders
│   ├── builder.py
│   ├── file_builder.py
│   └── paths.py
├── tools/           # Tool Implementations
│   ├── filesystem.py
│   ├── shell.py
│   ├── web_search.py
│   └── web_fetch.py
├── memory/          # Long-term Memory
├── skills/          # Skill Definitions
├── config/          # Configuration
├── channels/        # Channel Adapters
├── templates/       # System Prompt Templates
└── main.py         # Entry Point
```

## 元件說明

### LLM Providers

| Provider | 說明 |
|----------|------|
| OpenAI | OpenAI API |
| MiniMax | MiniMax AI |
| OpenRouter | 多模型聚合平台 |

### Storage

| Type | 說明 |
|------|------|
| Memory | 記憶體儲存 |
| File | 檔案儲存 |
| SQLite | SQLite 資料庫（預設） |

### Channels

| Channel | 說明 |
|---------|------|
| Console | 終端機介面 |
| Telegram | Telegram Bot |

### Tools

| Tool | 說明 |
|------|------|
| ReadFile | 讀取檔案 |
| WriteFile | 寫入檔案 |
| EditFile | 編輯檔案 |
| ListDir | 列出目錄 |
| Exec | 執行 Shell 命令 |
| WebSearch | 網頁搜尋（Brave API） |
| WebFetch | 抓取網頁內容 |
| SaveMemory | 儲存長期記憶 |

## 配置選項

### LLM 設定

```json
{
  "llm": {
    "providers": {
      "openrouter": {
        "api_key": "",
        "model": "",
        "base_url": "https://openrouter.ai/api/v1",
        "enabled": false
      }
    },
    "default": "openrouter",
    "temperature": 0.7,
    "max_tokens": 8192
  }
}
```

### 記憶設定

```json
{
  "memory": {
    "max_history": 50,
    "threshold": 30
  }
}
```

- `max_history`: 對話歷史最大訊息數
- `threshold`: 觸發記憶 consolidation 的訊息數

### 日誌設定

```json
{
  "log": {
    "enabled": true,
    "retention_days": 365,
    "level": "INFO",
    "log_system_prompt": true,
    "log_system_prompt_lines": 0
  }
}
```

### Tools 設定

```json
{
  "tools": {
    "max_tool_iterations": 100,
    "web_search": {
      "provider": "brave",
      "brave_api_key": "",
      "tavily_api_key": "",
      "jina_api_key": "",
      "searxng_url": "https://searx.be",
      "max_results": 10,
      "proxy": ""
    },
    "web_fetch": {
      "max_chars": 50000,
      "timeout": 30,
      "prefer_trafilatura": true,
      "firecrawl_api_key": ""
    }
  }
}
```

- `web_search.provider`: `brave` / `duckduckgo` / `tavily` / `searxng` / `jina`
- `web_search.brave_api_key`: Brave Search API key
- `web_search.tavily_api_key`: Tavily API key
- `web_search.jina_api_key`: Jina API key
- `web_search.searxng_url`: SearXNG instance URL
- `web_search.max_results`: 預設搜尋筆數
- `web_search.proxy`: 對 web search 使用的 proxy URL
- `web_fetch.max_chars`: 抓取結果最大字數
- `web_fetch.timeout`: 抓取 timeout 秒數
- `web_fetch.prefer_trafilatura`: 優先使用 trafilatura
- `web_fetch.firecrawl_api_key`: Firecrawl API key

### Search 設定

```json
{
  "search": {
    "enabled": false,
    "provider": "lancedb",
    "path": "~/.opensprite/data/lancedb",
    "history_top_k": 5,
    "knowledge_top_k": 5
  }
}
```

- `search.enabled`: 是否啟用 per-chat 搜尋索引
- `search.provider`: 目前支援 `lancedb`
- `search.path`: LanceDB 索引存放路徑
- `search.history_top_k`: `search_history` 預設回傳筆數
- `search.knowledge_top_k`: `search_knowledge` 預設回傳筆數
- 啟用後會保留 `MEMORY.md` 作為每次對話都帶入的長期記憶，另外把對話歷史與 `web_search` / `web_fetch` 結果寫進 LanceDB 供需要時搜尋

## 依賴

```txt
aiohttp>=3.0.0
beautifulsoup4>=4.0
html2text>=2020.1.16
lancedb>=0.29.0
loguru>=0.7.0
markdown>=3.0
openai>=1.0.0
python-daemon>=3.0.0
python-dotenv>=1.0.0
python-telegram-bot>=20.0
trafilatura>=2.0.0
```

## 開發

```bash
# 安裝開發依賴
python -m pip install -e ".[dev]"

# 執行測試
pytest
```

## 文件

- [FLOW.md](FLOW.md) - 完整架構與流程圖
- [AGENTS.md](src/opensprite/templates/AGENTS.md) - Agent 使用指南
- [SOUL.md](src/opensprite/templates/SOUL.md) - Agent 核心設定

## License

MIT
