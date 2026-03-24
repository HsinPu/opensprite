# OpenSprite

OpenSprite is a lightweight, self-hosted personal AI assistant for people who want a small codebase, local control, and a standard Python install flow.

## What It Does

- Runs as an installable Python CLI: `opensprite`
- Supports multiple LLM providers through one config format
- Receives messages from Telegram
- Stores conversation history in memory or SQLite
- Provides built-in tools for file edits, shell commands, web search, web fetch, and long-term memory
- Can optionally index history and tool results with LanceDB

## Current Status

This repository is now package-first.

- CLI entrypoint: `src/opensprite/cli/commands.py`
- Module entrypoint: `src/opensprite/__main__.py`
- Service runtime: `src/opensprite/runtime.py`
- Install command: `python -m pip install .`
- Default command: `opensprite` shows help
- Start command: `opensprite gateway`
- Legacy alias: `opensprite run`
- Runtime mode: foreground process; stop it with `Ctrl+C`

## Requirements

- Python 3.11+
- One configured LLM provider API key
- Telegram bot token if you want incoming messages through Telegram

## Install

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install .
```

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

### Development Install

```bash
python -m pip install -e ".[dev]"
```

## Start

After installation, show the CLI help with:

```bash
opensprite
```

Initialize the default config and app directories with:

```bash
opensprite onboard
```

Start the gateway with:

```bash
opensprite gateway
```

Or via the module entrypoint:

```bash
python -m opensprite gateway
```

`opensprite run` is still available as a compatibility alias.

The process stays attached to the current terminal and does not daemonize itself.

## First Run

Run `opensprite onboard` first to create or refresh the default config and app directories.

OpenSprite stores its default config at:

```text
~/.opensprite/opensprite.json
```

It also prepares these directories:

```text
~/.opensprite/
├── bootstrap/
├── memory/
├── skills/
└── workspace/
```

## Minimal Configuration

Edit `~/.opensprite/opensprite.json`, enable one provider, and set `llm.default` to the provider you want to use.

Example using OpenRouter:

```json
{
  "llm": {
    "providers": {
      "openrouter": {
        "api_key": "sk-or-...",
        "enabled": true,
        "model": "openai/gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1"
      },
      "openai": {
        "api_key": "",
        "enabled": false,
        "model": "",
        "base_url": "https://api.openai.com/v1"
      },
      "minimax": {
        "api_key": "",
        "enabled": false,
        "model": "MiniMax-M2.5",
        "base_url": "https://api.minimax.io/v1"
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
      "token": ""
    },
    "console": {
      "enabled": true
    }
  }
}
```

The template still contains a `console` section, but the current startup path actively launches the Telegram adapter.

## Telegram Setup

If you want to use Telegram, update the `channels.telegram` section:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  }
}
```

Then restart OpenSprite:

```bash
opensprite gateway
```

## Storage Options

OpenSprite currently supports these storage modes through `storage.type`.

- `memory`: in-process only
- `sqlite`: SQLite database at `storage.path`

Example:

```json
{
  "storage": {
    "type": "sqlite",
    "path": "~/.opensprite/data/sessions.db"
  }
}
```

## Search Index

Optional search uses LanceDB and is configured in `search`.

```json
{
  "search": {
    "enabled": true,
    "provider": "lancedb",
    "path": "~/.opensprite/data/lancedb",
    "history_top_k": 5,
    "knowledge_top_k": 5
  }
}
```

When enabled, OpenSprite can index:

- stored chat history
- web search results
- web fetch results

## Built-In Tools

The default agent registers tools for:

- reading files
- writing files
- editing files
- listing directories
- executing shell commands
- web search
- web fetch
- long-term memory save
- search over indexed history and knowledge when LanceDB is enabled

## Project Layout

```text
src/opensprite/
├── cli/            # Typer CLI entrypoints
├── agent/          # Agent loop and tool orchestration
├── bus/            # Message queue and message models
├── channels/       # External channel adapters
├── config/         # Config schema and default template
├── context/        # Bootstrap files, paths, workspace helpers
├── llms/           # LLM provider implementations
├── memory/         # Long-term memory support
├── search/         # Optional LanceDB index
├── storage/        # Memory and SQLite storage providers
├── tools/          # Built-in tool implementations
├── utils/          # Logging and shared helpers
├── __main__.py     # python -m opensprite entrypoint
└── runtime.py      # Service startup logic
```

## Useful Commands

```bash
# install package
python -m pip install .

# development install
python -m pip install -e ".[dev]"

# show help
opensprite

# initialize config and app directories
opensprite onboard

# start gateway
opensprite gateway

# inspect config/runtime status
opensprite status

# inspect status as JSON
opensprite status --json

# module entrypoint
python -m opensprite gateway

# compatibility alias
opensprite run

# alias for onboard
opensprite init

# uninstall from current environment
python -m pip uninstall opensprite
```

## Notes

- `opensprite gateway` runs in the foreground
- the package install flow does not create a virtual environment for you; activate one before installing if you want isolation
- the repository root no longer needs a separate launcher script

## License

MIT
