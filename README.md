# OpenSprite

**English:** A lightweight, self-hosted personal AI assistant with a small codebase, local control, and a standard Python packaging and install flow.

**中文：** 輕量、可自架設的個人 AI 助理：程式碼精簡、資料留在本機、以標準 Python 套件方式安裝與執行。

> This README is **bilingual (English / 繁體中文)**. Code blocks and JSON examples are language-neutral.

---

## Overview · 功能概覽

**English**

- Installable Python CLI: `opensprite`
- Multiple LLM providers in one config format (OpenRouter, OpenAI, MiniMax, …)
- Incoming messages from **Telegram** (channel registry is extensible; only Telegram is implemented today)
- Conversation storage: in-memory or **SQLite**
- Built-in tools: filesystem, shell, web search and fetch, long-term memory, scheduling (`cron`), subagent delegation, skills, **MCP-hosted tools**
- Optional: index history and web tool payloads in SQLite **FTS5**, with background embeddings for hybrid reranking

**中文**

- 以 Python CLI 執行：`opensprite`
- 透過統一設定格式支援多種 LLM 供應商（OpenRouter、OpenAI、MiniMax 等）
- 從 **Telegram** 接收訊息（頻道採登錄表擴充；目前實作僅 Telegram）
- 對話紀錄可使用記憶體或 **SQLite**
- 內建工具：讀寫／編輯檔案、目錄列表、Shell、網路搜尋與擷取、長期記憶、排程（cron）、子代理委派、Skills、**MCP 外部工具**
- 可選：將歷史與網路工具結果索引至 SQLite **FTS5**，並可搭配背景 embedding 做混合重排序

---

## Current layout · 目前狀態

**English**

- CLI entrypoint: `opensprite` (`opensprite.cli.commands`)
- Module entrypoint: `python -m opensprite`
- Service runtime: `gateway` in `src/opensprite/runtime.py`
- Install: `python -m pip install .`
- Default Typer behavior shows help; `opensprite gateway` runs the gateway as a foreground process (stop with `Ctrl+C`)

**中文**

- 套件入口：`opensprite`（`opensprite.cli.commands`）
- 模組入口：`python -m opensprite`
- 服務執行：`src/opensprite/runtime.py` 的 `gateway`
- 安裝：`python -m pip install .`
- 預設無子命令會顯示說明；`opensprite gateway` 啟動閘道（前景程序，以 `Ctrl+C` 結束）

---

## Requirements · 系統需求

**English:** Python 3.11+. A Telegram bot token is required if you use Telegram. LLM API keys are required when the agent handles user prompts.

**中文：** Python 3.11+。若使用 Telegram 則需要 Bot Token。LLM API 金鑰可稍後設定，但代理開始處理對話前需要完成設定。

---

## Install · 安裝

### Windows (recommended) · Windows（建議）

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

### Linux one-command setup · Linux 一鍵安裝

**English:** On a fresh Linux machine, install directly from GitHub:

**中文：** 全新的 Linux 主機可直接從 GitHub 安裝：

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.sh | bash
```

Install and start the background gateway immediately:

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.sh | bash -s -- --start
```

The installer keeps code and runtime data separate:

```text
~/.local/share/opensprite/opensprite   # cloned repository / 程式碼
~/.opensprite                          # config, logs, memory / 設定與資料
~/.local/bin/opensprite                # command symlink / 指令連結
```

Start the background gateway after setup:

```bash
opensprite service start
opensprite service status
```

### Development install · 開發模式安裝

```powershell
python -m pip install -e ".[dev]"
```

**English:** Optional **sqlite-vec** extra for vector candidate backends:

**中文：** 可選安裝 **sqlite-vec**（向量候選後端）：

```powershell
python -m pip install -e ".[dev,vector]"
```

---

## Quick start · 快速開始

```powershell
opensprite
opensprite gateway
```

**English:** On first gateway start, OpenSprite creates the default app config under `~/.opensprite`. Configure providers, models, and channels from the Web UI Settings page.

**中文：** 第一次啟動 gateway 時，OpenSprite 會在 `~/.opensprite` 建立預設設定。Provider、模型與頻道請從 Web UI 的 Settings 頁設定。

**English:** Module entrypoint:

**中文：** 模組方式啟動：

```powershell
python -m opensprite gateway
```

---

## Linux systemd user service · Linux systemd 使用者服務

**English:** After `opensprite gateway` works in the foreground, you can install a `systemd --user` unit. Service file path: `~/.config/systemd/user/opensprite-gateway.service`. To keep the user service after logout, run once: `loginctl enable-linger "$USER"`.

**中文：** 在確認 `opensprite gateway` 可正常前景執行後，可安裝為 `systemd --user` 服務。服務單元檔：`~/.config/systemd/user/opensprite-gateway.service`。登出後仍要保持使用者服務：執行一次 `loginctl enable-linger "$USER"`。

```bash
opensprite service install
opensprite service install --config ~/.opensprite/opensprite.json
opensprite service status
opensprite service restart
```

---

## Configuration layout · 設定檔架構（重要）

**English:** `opensprite gateway` creates the app home directory and default config if they do not exist. The main config defaults to `~/.opensprite/opensprite.json`. **Split config files** live next to the main file; paths are relative to the directory that contains `opensprite.json` (keys are customizable).

**中文：** `opensprite gateway` 會在缺少設定時建立應用程式家目錄與預設設定，主設定預設為 `~/.opensprite/opensprite.json`。主檔採 **分割設定檔**：路徑為相對於主設定檔所在目錄的檔名（可自訂鍵名）。

| Main file key · 主檔欄位 | Default file · 預設檔名 | Purpose · 用途 |
| --- | --- | --- |
| `llm.providers_file` | `llm.providers.json` | LLM provider API keys, models, enabled flags / 各 LLM 供應商的 API／模型／啟用狀態 |
| `channels_file` | `channels.json` | Channel sections (Telegram, console, …) / Telegram、console 等頻道區塊 |
| `search_file` | `search.json` | Search and embedding settings / 搜尋與 embedding 相關設定 |
| `media_file` | `media.json` | Vision, speech, video / 影像、語音、影片 |
| `messages_file` | `messages.json` | User-facing reply strings / 給使用者看的回覆文案 |
| `tools.mcp_servers_file` | `mcp_servers.json` | MCP server definitions / MCP 伺服器連線定義 |

**English:** Example layout:

**中文：** 目錄結構範例：

```text
~/.opensprite/
├── opensprite.json      # main config / 主設定
├── llm.providers.json
├── channels.json
├── search.json
├── media.json
├── messages.json
├── mcp_servers.json
├── bootstrap/
├── memory/
├── skills/
├── workspace/
└── subagent_prompts/    # subagent prompts (tools may manage) / 子代理提示詞（可由工具維護）
```

**English:** Validate the main file and all split JSON files:

**中文：** 驗證主檔與所有外掛 JSON：

```powershell
opensprite config validate
opensprite config validate --json
```

### Main file `opensprite.json` · 主設定節錄

**English:** The authoritative template is `opensprite.json.template` in the package. It includes `llm`, `storage`, `channels_file`, `search_file`, `media_file`, `log`, `tools`, `agent`, `memory`, `user_profile`, `recent_summary`, and more.

**中文：** 實際模板以套件內 `opensprite.json.template` 為準；結構包含 `llm`、`storage`、`channels_file`、`search_file`、`media_file`、`log`、`tools`、`agent`、`memory`、`user_profile`、`recent_summary` 等。

### Channels `channels.json` · 頻道

**English:** Only **`telegram`** has a registered channel adapter today. The template may set `console.enabled` to `true`, but without an adapter the runtime logs a warning and skips that channel.

**中文：** 目前 **已註冊的頻道適配器僅有 `telegram`**。模板中的 `console.enabled` 可為 `true`，但若沒有對應適配器，啟動時會記錄警告並略過。

Minimal Telegram example:

```json
{
  "telegram": {
    "enabled": true,
    "token": "YOUR_TELEGRAM_BOT_TOKEN"
  },
  "console": {
    "enabled": true
  }
}
```

### LLM `llm.providers.json`

**English:** In the main file’s `llm` section, set `default` to a provider key and ensure that provider is `enabled` with `api_key` and `model` set.

**中文：** 在主檔的 `llm` 區塊設定 `default` 指向其中一個鍵，並在對應供應商啟用且填好 `api_key`／`model`。

OpenRouter example:

```json
{
  "openrouter": {
    "api_key": "sk-or-...",
    "enabled": true,
    "model": "openai/gpt-4o-mini",
    "base_url": "https://openrouter.ai/api/v1"
  }
}
```

Main file snippet:

```json
{
  "llm": {
    "providers_file": "llm.providers.json",
    "default": "openrouter",
    "temperature": 0.7,
    "max_tokens": 8192
  },
  "storage": {
    "type": "sqlite",
    "path": "~/.opensprite/data/sessions.db"
  }
}
```

### Search `search.json` · 搜尋

**English:** Enabled by default with `search.backend="sqlite"`, which requires `storage.type="sqlite"`. When enabled, indexes chats and `web_search` / `web_fetch` payloads; embeddings are optional and processed in a background queue. If `search.embedding.api_key` or `base_url` is empty, values fall back to the active LLM provider.

**中文：** 預設啟用且 `search.backend="sqlite"`，需搭配 `storage.type` 為 `sqlite`。啟用後會索引對話、`web_search`／`web_fetch` 結果等；embedding 可選、於背景佇列處理。若 embedding 的 `api_key` 或 `base_url` 留空，會改採目前作用中的 LLM 供應商設定。

```json
{
  "enabled": true,
  "backend": "sqlite",
  "history_top_k": 5,
  "knowledge_top_k": 5,
  "embedding": {
    "enabled": false,
    "provider": "openai",
    "api_key": "",
    "model": "",
    "base_url": null,
    "batch_size": 16,
    "candidate_count": 20,
    "candidate_strategy": "vector",
    "vector_backend": "auto",
    "vector_candidate_count": 50,
    "retry_failed_on_startup": false
  }
}
```

### Media `media.json` · 多媒體

**English:** Vision, speech-to-text, and video analysis are configured here. Values merge with any inline `vision` / `speech` / `video` blocks in the main file; the external file overrides on conflict.

**中文：** 影像分析、語音轉文字、影片分析皆由此檔設定（亦可與主檔內嵌的 `vision`／`speech`／`video` 合併，以外部檔為準覆寫）。

```json
{
  "vision": {
    "enabled": true,
    "provider": "minimax",
    "api_key": "YOUR_VISION_API_KEY",
    "model": "YOUR_MINIMAX_VISION_MODEL",
    "base_url": "YOUR_MINIMAX_BASE_URL"
  },
  "speech": {
    "enabled": true,
    "provider": "minimax",
    "api_key": "YOUR_SPEECH_API_KEY",
    "model": "YOUR_MINIMAX_SPEECH_MODEL",
    "base_url": "YOUR_MINIMAX_BASE_URL"
  },
  "video": {
    "enabled": true,
    "provider": "minimax",
    "api_key": "YOUR_VIDEO_API_KEY",
    "model": "YOUR_MINIMAX_VIDEO_MODEL",
    "base_url": "YOUR_MINIMAX_BASE_URL"
  }
}
```

---

## MCP servers · MCP 伺服器

**English:** Define servers in `mcp_servers.json` (transport types such as `stdio`, `sse`, `streamableHttp`; see `MCPServerConfig` in the codebase). The gateway connects to MCP on startup and exposes remote tools to the agent. The `configure_mcp` tool can update config through a guarded workflow and trigger reload (see `ConfigureMCPTool`).

**中文：** 在 `mcp_servers.json` 定義伺服器（支援 `stdio`、`sse`、`streamableHttp` 等型別，詳見 `MCPServerConfig`）。閘道啟動時會先連線 MCP，將遠端工具掛入代理。代理可使用 `configure_mcp` 在安全流程下更新設定並觸發重新載入（實際行為見 `ConfigureMCPTool`）。

---

## Subagents and skills · 子代理與 Skills

**English**

- **`delegate`**: run a subtask with a built-in or custom subagent type; available types come from `subagent_prompts` (bundled + user home).
- **`configure_subagent`**: add or update `~/.opensprite/subagent_prompts/<id>.md` (subject to safety and confirmation rules inside the tool).
- **`read_skill` / `configure_skill`**: read or adjust skill markdown under workspace `skills` when the skills loader is enabled.
- Main `agent` section controls **skill review** (optional extra LLM pass after the main reply to maintain skills; costs extra API usage).

**中文**

- **`delegate`**：將子任務交給內建或自訂子代理類型執行；可用類型來自 `subagent_prompts` 與套件內建範本。
- **`configure_subagent`**：新增或更新 `~/.opensprite/subagent_prompts/<id>.md`（需遵守工具內的安全與確認規則）。
- **`read_skill`／`configure_skill`**：讀取或調整工作區 `skills` 下的技能說明（有啟用 Skills 載入器時）。
- 主設定中 `agent` 區塊可控制 **skill review**（在主回覆後可選的額外 LLM 通過以維護 skills，會增加 API 成本）。

---

## Architecture · 架構摘要

**English:** Agent-centric design with ports-and-adapters boundaries: **AgentLoop** orchestrates storage, context, execution, maintenance, and replies; **ExecutionEngine** runs the LLM/tool loop; **ToolResultPersistence** writes tool output back and optionally indexes; **tool_registration** wires default tools; **consolidation** maintains long-term memory under `~/.opensprite/memory/` and per-session `USER.md` at `~/.opensprite/workspace/sessions/<channel>/<external_chat_id>/USER.md`; **channels / llms / storage / search / media** isolate external systems.

**中文：** 採用代理為核心、連接埠與適配器分層：**AgentLoop** 負責訊息儲存、組上下文、執行引擎、維護與回覆；**ExecutionEngine** 負責 LLM 與工具迴圈；**ToolResultPersistence** 將工具輸出寫回並可選索引；**tool_registration** 註冊預設工具；**consolidation** 維護 `~/.opensprite/memory/` 下的長期記憶，以及各 session workspace 的 `~/.opensprite/workspace/sessions/<channel>/<external_chat_id>/USER.md`；**channels／llms／storage／search／media** 為對外適配層。

```text
Channel Adapter -> MessageQueue -> AgentLoop -> ExecutionEngine -> LLM / Tools
                                      |               |
                                      |               -> ToolResultPersistence -> Storage / Search
                                      -> Consolidation -> Memory / USER.md
```

---

## Storage · 儲存模式

**English:** `storage.type` is either `memory` (in-process only) or `sqlite` (database at `storage.path`).

**中文：** `storage.type`：`memory` 僅行程內；`sqlite` 資料庫路徑見 `storage.path`。

---

## Web search and fetch · 網路搜尋與擷取

**English:** `tools.web_search.provider` defaults to `duckduckgo` and also supports `brave`, `tavily`, `searxng`, and `jina` (providers other than DuckDuckGo need the right API key or URL). `web_search` and `web_fetch` share a consistent JSON payload shape; when search indexing is on, payloads are stored in `knowledge_sources` for `search_knowledge`.

**中文：** `tools.web_search.provider` 預設為 `duckduckgo`，也可設定 `brave`、`tavily`、`searxng`、`jina`（DuckDuckGo 以外的供應商需對應 API 金鑰或 URL）。`web_search` 與 `web_fetch` 共用一致的 JSON 結果形狀；啟用搜尋索引時會寫入 `knowledge_sources` 供 `search_knowledge` 使用。

**English:** DuckDuckGo search follows result pages up to `tools.web_search.duckduckgo_max_pages` (default `10`) and returns up to `tools.web_search.max_results` results (default `25`).

**中文：** DuckDuckGo 搜尋會依 `tools.web_search.duckduckgo_max_pages` 追分頁（預設 `10` 頁），最多回傳 `tools.web_search.max_results` 筆結果（預設 `25` 筆）。

**English:** `web_fetch` limits extracted text with `tools.web_fetch.max_chars` (default `50000`) and raw HTTP response size with `tools.web_fetch.max_response_size` in bytes (default `5242880`).

**中文：** `web_fetch` 會用 `tools.web_fetch.max_chars` 限制擷取文字長度（預設 `50000`），並用 `tools.web_fetch.max_response_size` 限制原始 HTTP 回應大小，單位 bytes（預設 `5242880`）。

---

## Built-in tools · 內建工具（預設註冊）

**English:** Filesystem read/write/edit/list, shell, `web_search`, `web_fetch`, `analyze_image`, `ocr_image`, `transcribe_audio`, `analyze_video`, `send_media`, `cron`, `save_memory`, history/knowledge search when SQLite search is enabled, **dynamic MCP tools**, **`delegate`**, **`configure_mcp` / `configure_subagent`**, and skill-related tools.

**中文：** 讀寫／編輯檔案、列目錄、Shell、`web_search`、`web_fetch`、影像分析（`analyze_image`）、OCR（`ocr_image`）、語音轉文字（`transcribe_audio`）、影片分析（`analyze_video`）、傳送媒體（`send_media`）、排程（`cron`）、長期記憶（`save_memory`）、索引歷史／知識搜尋（SQLite 搜尋啟用時）、**MCP 動態工具**、**`delegate`**、**`configure_mcp`／`configure_subagent`**、Skills 相關工具等。

---

## Scheduling (cron) · 排程（Cron）

**English:** Per-session jobs live under the session workspace, e.g. `~/.opensprite/workspace/sessions/<channel>/<external_chat_id>/cron/jobs.json`. Supported kinds include `at` (one-shot ISO time), `every` (fixed interval in seconds), and `cron` (cron expression with optional timezone). When a job fires, the agent runs again with the stored message; if the job was created with `deliver=true`, the reply is sent back to the original channel. The **gateway must be running** (or a Linux user service installed) for jobs to execute. CLI: `opensprite cron list|add|remove|pause|enable --session telegram:<external_chat_id> ...`. In Telegram you can use `/cron`, `/cron help`, `/cron add every 300 "message"`, etc. (handled immediately by the queue layer, like `/stop`).

**中文：** 排程檔放在該工作階段的 workspace 內，例如 `~/.opensprite/workspace/sessions/<channel>/<external_chat_id>/cron/jobs.json`。支援 `at`（單次 ISO 時間）、`every`（固定秒數間隔）、`cron`（cron 表達式，可帶時區）。觸發時以儲存訊息再跑一輪代理；若建立工作時 `deliver=true`，結果會送回原頻道。**閘道必須在執行中**（或 Linux 上已安裝 user service）排程才會跑。CLI：`opensprite cron list|add|remove|pause|enable --session telegram:<external_chat_id> ...`。Telegram 內可使用 `/cron`、`/cron help`、`/cron add every 300 "訊息"` 等（與 `/stop` 類似，由佇列層立即處理）。

---

## Search maintenance CLI · 搜尋維護 CLI（精簡）

```powershell
opensprite search status
opensprite search rebuild
opensprite search rebuild --session-id telegram:user-a
opensprite search retry-embeddings
opensprite search refresh-embeddings
opensprite search run-queue
opensprite search benchmark --session-id telegram:user-a --query "keyword" --strategy both --repeat 5
opensprite search seed-demo
```

**English:** Optional `sqlite-vec`: `python -m pip install -e ".[vector]"`. Candidate strategies `fts` / `vector` and `vector_backend` (`exact`, `sqlite_vec`, `auto`) behave as in code and `search.json` templates.

**中文：** 向量後端可選 `sqlite-vec`：`python -m pip install -e ".[vector]"`。候選策略 `fts`／`vector` 與 `vector_backend`（`exact`、`sqlite_vec`、`auto`）行為以程式與 `search.json` 模板為準。

---

## Project layout · 專案目錄

```text
src/opensprite/
├── cli/              # Typer CLI
├── agent/            # Agent loop, execution, maintenance
├── bus/              # Message queue
├── channels/         # Channel adapters (Telegram)
├── config/           # Config schema and JSON templates
├── context/          # Paths, workspace, bootstrap
├── documents/        # Documents and consolidators
├── llms/             # LLM implementations
├── media/            # Vision / speech / video routing
├── search/           # SQLite search and embeddings
├── storage/          # Storage backends
├── tools/            # Built-in tools and MCP
├── skills/           # Bundled skill examples
├── subagent_prompts/ # Bundled subagent prompts
├── utils/
├── __main__.py
└── runtime.py
```

---

## Useful commands · 常用指令

```powershell
python -m pip install .
python -m pip install -e ".[dev,vector]"
opensprite
opensprite config validate
opensprite gateway
opensprite status
opensprite status --json
python -m pip uninstall opensprite
```

---

## Notes · 備註

**English:** `opensprite gateway` does not daemonize. Create and activate a venv yourself if you want isolation. The repository root does not require a separate launcher script.

**中文：** `opensprite gateway` 為前景程序，不自行 daemonize。虛擬環境需自行建立；套件不會代為建立 venv。儲存庫根目錄不需要額外啟動腳本。

---

## License · 授權

MIT
