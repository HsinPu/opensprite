# OpenSprite

輕量、可自架設的個人 AI assistant。OpenSprite 以 Python CLI 執行，支援多 LLM provider、Telegram channel、SQLite storage/search、內建 tools、cron、skills、subagents 與 MCP tools。

## Requirements

- Python 3.11+
- Git
- Telegram Bot Token（使用 Telegram 時需要）
- LLM provider API key（代理處理訊息前需要）

## Linux Install

一鍵安裝會把程式碼與資料分開，Python dependencies 會裝在專屬 venv，不污染 system Python。安裝完成後預設會啟動或重啟背景 gateway。

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.sh | bash
```

如果只想安裝、不啟動 gateway：

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.sh | bash -s -- --no-start
```

預設路徑：

```text
~/.local/share/opensprite/opensprite   # code checkout + .venv
~/.local/bin/opensprite                # command symlink
~/.opensprite                          # config, data, logs, memory
```

如果不想讓 installer 安裝 apt packages：

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.sh | bash -s -- --no-system
```

Web UI 需要 Node.js 20.19+ 或 22.12+。Installer 會在 apt-based Linux 上自動安裝或升級到可用的 Node.js 22。

## Manual Install

Windows / macOS / development 可以手動建立 venv：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell activation：

```powershell
.\.venv\Scripts\Activate.ps1
```

## Common Commands

```bash
opensprite --version
opensprite status
opensprite gateway              # foreground gateway
opensprite service start        # background gateway
opensprite service status
opensprite service stop
opensprite update
opensprite update --check
opensprite update --restart
opensprite config validate
```

`opensprite gateway` 是前景程序；背景執行請用 `opensprite service start`。

## Update

```bash
opensprite update
```

Update 會：

- `git fetch origin`
- fast-forward 到 `origin/main`
- 重新安裝 package/dependencies 到既有 venv
- 保留 `~/.opensprite`

如果 checkout 有本地修改，update 會停止，不會自動 stash 或 reset。

更新後要重啟 gateway：

```bash
opensprite update --restart
```

## Uninstall

移除 command 與 code，但保留 `~/.opensprite`：

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/uninstall.sh | bash
```

完整移除 code、config、data、logs、memory：

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/uninstall.sh | bash -s -- --full
```

非互動環境可加 `--yes` 跳過確認：

```bash
curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/uninstall.sh | bash -s -- --full --yes
```

`--full` 會刪除 `~/.opensprite`，不可復原。

## Configuration

第一次啟動 gateway 會建立預設設定到 `~/.opensprite`。

主要設定檔：

```text
~/.opensprite/opensprite.json
~/.opensprite/llm.providers.json
~/.opensprite/channels.json
~/.opensprite/search.json
~/.opensprite/media.json
~/.opensprite/messages.json
~/.opensprite/mcp_servers.json
```

目前使用者可連接的 channel 主要是 `telegram`。`web` 是內建 Web UI channel。

如果 gateway 需要透過 proxy 連外，例如 GitHub Copilot API，可在 `~/.opensprite/opensprite.json` 加上：

```json
{
  "network": {
    "http_proxy": "http://proxy-host:port",
    "https_proxy": "http://proxy-host:port",
    "no_proxy": "127.0.0.1,localhost"
  }
}
```

修改後重啟 gateway：`opensprite service restart`。

設定檢查：

```bash
opensprite config validate
opensprite config validate --json
```

## Credential Vault

OpenSprite 會把 LLM provider API key 存在本機 credential vault，而不是寫回 `llm.providers.json`。預設位置是 `~/.opensprite/auth.json`；不要把這個檔案 commit 到 repository。

可用 Web UI、CLI 或明確的 chat 指令管理 credentials：

- Web UI：開啟 Settings，連接 provider 或切換 provider credential。
- CLI：使用 `opensprite auth credentials ...` 管理本機 credentials。
- Chat：只有在你明確要求儲存、列出、刪除或設定預設 credential 時，agent 才能使用 `credential_store` tool。

CLI 範例：

```bash
opensprite auth credentials add openrouter --secret sk-or-...
opensprite auth credentials list openrouter
opensprite auth credentials default <credential_id> --provider openrouter
opensprite auth credentials default <credential_id> --capability llm.chat
opensprite auth credentials remove openrouter <credential_id>
```

Chat 範例：

```text
幫我把這個 OpenRouter API key 存起來：sk-or-...
列出目前 openrouter credentials
把 <credential_id> 設成 llm.chat 預設 credential
```

安全行為：

- Agent 不會只因為訊息中出現 API key 就自動保存；你必須明確要求或確認保存。
- Tool result、run trace、persisted tool args 和後續 LLM context 只會顯示 redacted preview，不會回顯完整 secret。
- Runtime 會從 vault 解析 `credential_id`、provider default，或 `llm.chat` capability default。

## Search And Cron

搜尋維護：

```bash
opensprite search status
opensprite search rebuild
opensprite search retry-embeddings
opensprite search run-queue
```

Cron jobs 需要 gateway 正在執行：

```bash
opensprite cron list --session telegram:<chat_id>
opensprite cron add every 300 "message" --session telegram:<chat_id>
```

## Project Layout

```text
src/opensprite/
├── cli/              # Typer CLI
├── agent/            # Agent loop and execution
├── channels/         # Telegram and Web channel wiring
├── config/           # Config schema and templates
├── llms/             # LLM providers
├── search/           # SQLite search and embeddings
├── storage/          # Storage backends
├── tools/            # Built-in tools and MCP
└── runtime.py        # Gateway wiring
```

## Development

```bash
python -m pip install -e ".[dev,vector]"
python -m pytest
```

Web app checks live in `apps/web`:

```bash
npm ci
npm run test:smoke
npm run build
```

## License

MIT
