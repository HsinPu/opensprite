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

Initialize and configure OpenSprite interactively with:

```bash
opensprite onboard
```

For automation or CI, skip prompts with:

```bash
opensprite onboard --no-input
```

Start the gateway with:

```bash
opensprite gateway
```

Or via the module entrypoint:

```bash
python -m opensprite gateway
```

The process stays attached to the current terminal and does not daemonize itself.

## First Run

Run `opensprite onboard` first. By default it creates the app directories and then opens menu-based prompts for provider, model, API key, and chat channel selection.

If you need a non-interactive setup flow, use `opensprite onboard --no-input` and edit the config manually afterward.

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

If you use `opensprite onboard --no-input`, edit `~/.opensprite/opensprite.json`, enable one provider, and set `llm.default` to the provider you want to use.

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

The template still contains a `console` section, but there is no `ConsoleAdapter` yet. The runtime now boots enabled channels through the channel registry, and currently only `telegram` is implemented.

## Architecture

OpenSprite uses an agent-centric architecture with ports-and-adapters boundaries.

- `AgentLoop` is the main orchestrator. It owns the end-to-end request flow: store the inbound message, build context, call the execution engine, trigger maintenance work, and return one unified assistant response.
- `ExecutionEngine` runs the LLM and tool-calling loop. It handles repeated LLM calls, tool execution, fallback handling, and iteration limits.
- `ToolResultPersistence` stores tool outputs back into conversation history and optionally indexes them into the search store.
- `tool_registration` builds the default tool set for the agent, including filesystem, shell, web, delegate, memory, and optional search tools.
- `consolidation` contains maintenance services for long-term memory updates and global `USER.md` profile refreshes.
- `channels`, `llms`, `storage`, and `search` are adapter layers around external systems. The agent core talks to these through shared interfaces rather than implementation-specific code.

At a high level, the runtime flow looks like this:

```text
Channel Adapter -> MessageQueue -> AgentLoop -> ExecutionEngine -> LLM / Tools
                                      |               |
                                      |               -> ToolResultPersistence -> Storage / Search
                                      -> Consolidation Services -> Memory / USER.md
```

This keeps the agent decision flow stable while letting channels, providers, storage backends, and search infrastructure evolve independently.

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
├── agent/          # Agent orchestration, execution, and maintenance services
├── bus/            # Message queue and message models
├── channels/       # External channel adapters
├── config/         # Config schema and default template
├── context/        # Bootstrap files, paths, workspace helpers
├── documents/      # Managed markdown stores and consolidators
├── llms/           # LLM provider implementations
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

# initialize without prompts
opensprite onboard --no-input

# start gateway
opensprite gateway

# inspect config/runtime status
opensprite status

# inspect status as JSON
opensprite status --json

# module entrypoint
python -m opensprite gateway

# uninstall from current environment
python -m pip uninstall opensprite
```

## Notes

- `opensprite gateway` runs in the foreground
- the package install flow does not create a virtual environment for you; activate one before installing if you want isolation
- the repository root no longer needs a separate launcher script

## License

MIT
