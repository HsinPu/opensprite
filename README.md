# OpenSprite

OpenSprite is a lightweight, self-hosted personal AI assistant for people who want a small codebase, local control, and a standard Python install flow.

## What It Does

- Runs as an installable Python CLI: `opensprite`
- Supports multiple LLM providers through one config format
- Receives messages from Telegram
- Stores conversation history in memory or SQLite
- Provides built-in tools for file edits, shell commands, web search, web fetch, and long-term memory
- Can optionally index history and web tool results in SQLite FTS5, with background embeddings for hybrid reranking

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

## Linux Service

On Linux, you can install OpenSprite as a `systemd --user` service after you have confirmed that `opensprite gateway` starts correctly in the foreground.

Install and start the service:

```bash
opensprite service install
```

Install against a specific config file:

```bash
opensprite service install --config ~/.opensprite/opensprite.json
```

Common service commands:

```bash
opensprite service status
opensprite service restart
opensprite service stop
opensprite service start
opensprite service uninstall
```

The service file is written to:

```text
~/.config/systemd/user/opensprite-gateway.service
```

To keep the user service running after logout, enable lingering once:

```bash
loginctl enable-linger "$USER"
```

To inspect runtime logs from systemd:

```bash
journalctl --user -u opensprite-gateway.service -n 100 --no-pager
```

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

Optional search uses the same SQLite database as `storage.path` and is configured in `search`.

Search requires `storage.type="sqlite"`.

```json
{
  "search": {
    "enabled": true,
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
      "candidate_strategy": "fts",
      "vector_candidate_count": 50,
      "retry_failed_on_startup": false
    }
  }
}
```

When enabled, OpenSprite can index:

- stored chat history
- web search results
- web fetch results

Search data now lives in the same SQLite file as normal chat storage:

- `messages` for normalized chat history
- `knowledge_sources` for stored `web_search` and `web_fetch` payloads
- `search_chunks` and `search_chunks_fts` for FTS5 search
- `chunk_embeddings` for optional hybrid reranking

When `search.embedding.enabled=true`:

- chunk embeddings are queued in the background instead of blocking normal replies
- search still works immediately through FTS5
- hybrid reranking improves result quality as pending embedding jobs complete

If `search.embedding.api_key` or `search.embedding.base_url` is empty, OpenSprite falls back to the active LLM provider settings.

`search.embedding` options:

- `candidate_count`: FTS candidate pool size before hybrid reranking
- `candidate_strategy`: candidate selection mode, either `fts` or `vector`
- `vector_candidate_count`: vector candidate pool size when `candidate_strategy="vector"`
- `retry_failed_on_startup`: whether failed embeddings should be re-queued automatically on startup

Candidate strategies:

- `fts` keeps the current default path: FTS5 selects candidates first, then embeddings rerank them when available
- `vector` uses stored embeddings to pull candidates first, then applies the same score fusion and deduplication logic afterward

Current note on `vector` mode:

- it uses exact vector scanning over `chunk_embeddings`
- it is not ANN and does not require `sqlite-vec`
- if vector candidates are unavailable, search falls back to the existing FTS path automatically

### Automatic Maintenance

Under normal service operation, search maintenance is mostly automatic:

- new chat messages are indexed automatically
- new `web_search` and `web_fetch` results are persisted and indexed automatically
- if search is enabled after older SQLite history already exists, startup backfills the missing index rows
- if the search index signature changes, startup rebuilds stale index structures automatically
- if embeddings are enabled, new chunk embeddings are queued in the background instead of blocking normal replies
- the gateway now starts a search queue worker automatically and keeps draining pending embedding jobs while the service is online
- stale `processing` embedding rows are re-queued automatically after restart
- missing or stale embeddings are refreshed automatically on startup
- failed embeddings can also be retried automatically on startup when `search.embedding.retry_failed_on_startup=true`

### Manual Maintenance

Manual commands are mainly for recovery, forced refresh, or running maintenance when the normal service is not active:

Search maintenance commands:

```bash
# rebuild all indexed history and knowledge
opensprite search rebuild

# rebuild one chat only
opensprite search rebuild --chat-id telegram:user-a

# inspect index and embedding job status
opensprite search status
opensprite search status --chat-id telegram:user-a

# retry failed embedding jobs
opensprite search retry-embeddings
opensprite search retry-embeddings --chat-id telegram:user-a

# refresh missing or stale embeddings
opensprite search refresh-embeddings
opensprite search refresh-embeddings --chat-id telegram:user-a
opensprite search refresh-embeddings --force

# run the embedding queue worker manually
opensprite search run-queue
opensprite search run-queue --watch
opensprite search run-queue --watch --idle-exit-seconds 30
opensprite search run-queue --force-refresh
```

In practice, you should usually only need these commands when:

- the service is offline and you still want to drain the embedding queue
- you want to force a full embedding refresh after changing models or ranking behavior
- you want to inspect or recover failed jobs immediately instead of waiting for the next startup cycle

## Web Search Pipeline

`web_search` and `web_fetch` now use one shared JSON payload shape.

- `web_search` is the discovery step: it returns result items with titles, URLs, and snippets
- `web_fetch` is the content step: it returns one page payload with final URL, title, extracted content, and fetch metadata

When search is enabled, both tools are persisted into `knowledge_sources` with metadata such as:

- `provider`
- `extractor`
- `status`
- `content_type`
- `truncated`

That metadata is exposed back through knowledge search results and can also be used as filters by the agent-facing `search_knowledge` tool.

## Built-In Tools

The default agent registers tools for:

- reading files
- writing files
- editing files
- listing directories
- executing shell commands
- web search
- web fetch
- analyzing images from the current user turn
- extracting visible text from images in the current user turn
- scheduling per-session cron jobs
- long-term memory save
- search over indexed history and knowledge when SQLite search is enabled, including metadata filters for stored web knowledge

## Vision

OpenSprite now includes a minimal image-analysis path built around the `analyze_image` tool.

Images are still downloaded by the Telegram adapter, but they are no longer sent directly into the normal text-model chat call. Instead, the agent sees that the current turn contains images and decides whether to call `analyze_image`.

This keeps image handling agent-centric:

- the agent decides whether visual analysis is actually needed
- skills can influence the instruction before the tool is called
- the image provider can be swapped independently from the normal text model

Minimal vision config:

```json
{
  "vision": {
    "enabled": true,
    "provider": "minimax",
    "api_key": "YOUR_VISION_API_KEY",
    "model": "YOUR_MINIMAX_VISION_MODEL",
    "base_url": "YOUR_MINIMAX_BASE_URL"
  }
}
```

If `vision.enabled` is false, the `analyze_image` tool still exists, but it returns a clear error explaining that no vision provider is configured.

Typical uses for `analyze_image`:

- describe what is shown in a screenshot
- inspect a UI issue from an image
- explain a chart, diagram, or photographed content
- analyze the first or second image in a multi-image turn with `image_index`

For image turns where the main goal is text extraction, OpenSprite also exposes `ocr_image`.

Typical uses for `ocr_image`:

- read text from screenshots and photographed error messages
- extract visible text from receipts, forms, or labels
- capture document text before summarizing or reasoning about it

The current minimal image toolset is intentionally narrow:

- it currently covers general image analysis and OCR-style text extraction
- it does not yet cover audio or video-specific media tools
- it is designed so future media tools can follow the same tool + provider-adapter pattern

## Audio

OpenSprite now includes a minimal audio path built around the `transcribe_audio` tool.

Telegram voice messages and audio attachments are downloaded into the current turn, but they are not forced into the normal text-model chat call. Instead, the agent sees that the turn contains audio and decides whether to call `transcribe_audio`.

Minimal speech config:

```json
{
  "speech": {
    "enabled": true,
    "provider": "minimax",
    "api_key": "YOUR_SPEECH_API_KEY",
    "model": "YOUR_MINIMAX_SPEECH_MODEL",
    "base_url": "YOUR_MINIMAX_BASE_URL"
  }
}
```

If `speech.enabled` is false, the `transcribe_audio` tool still exists, but it returns a clear error explaining that no speech provider is configured.

Typical uses for `transcribe_audio`:

- transcribe a Telegram voice note into plain text
- extract spoken content before summarizing or planning next steps
- capture audio notes before feeding them into normal text reasoning

The current minimal audio tool is intentionally narrow:

- it focuses on speech-to-text only
- it does not yet add audio understanding beyond transcription
- it follows the same tool + provider-adapter pattern as image analysis

## Video

OpenSprite now includes a minimal video path built around the `analyze_video` tool.

Telegram video messages, video notes, and animations are downloaded into the current turn, but they are not forced into the normal text-model chat call. Instead, the agent sees that the turn contains video and decides whether to call `analyze_video`.

Minimal video config:

```json
{
  "video": {
    "enabled": true,
    "provider": "minimax",
    "api_key": "YOUR_VIDEO_API_KEY",
    "model": "YOUR_MINIMAX_VIDEO_MODEL",
    "base_url": "YOUR_MINIMAX_BASE_URL"
  }
}
```

If `video.enabled` is false, the `analyze_video` tool still exists, but it returns a clear error explaining that no video provider is configured.

Typical uses for `analyze_video`:

- inspect what happens in a short clip or screen recording
- analyze a motion sequence or a visual step-by-step process
- understand a video turn before deciding whether more detailed follow-up is needed

The current minimal video tool is intentionally narrow:

- it defines the video-analysis tool path and provider boundary
- it does not yet add richer video-specific parsing or timeline segmentation
- it follows the same tool + provider-adapter pattern as image and audio

## Scheduling

OpenSprite includes a per-session `cron` tool for scheduling future agent work.

The first version supports three schedule types:

- `at`: run once at a specific ISO datetime
- `every`: run repeatedly at a fixed interval in seconds
- `cron`: run on a cron expression, optionally with a timezone

Scheduled jobs are stored inside the current session workspace:

```text
~/.opensprite/workspace/chats/<channel>/<chat_id>/cron/jobs.json
```

This means each session keeps its own schedule file and scheduled jobs do not mix across sessions.

When a job triggers, OpenSprite runs a new agent turn using the stored job message. If the job was created with `deliver=true`, the result is sent back to the original chat channel.

The gateway must be running for schedules to execute:

```bash
opensprite gateway
```

Or, on Linux, install the gateway as a user service:

```bash
opensprite service install
```

Typical `cron` tool usage patterns:

- One-time reminder: `at="2026-04-10T09:00:00"`
- Recurring interval: `every_seconds=1800`
- Cron schedule: `cron_expr="0 9 * * 1-5", tz="Asia/Taipei"`

Current actions exposed by the tool:

- `add`
- `list`
- `remove`

You can also manage schedules directly from the CLI:

```bash
# list one session's jobs
opensprite cron list --session telegram:user-a

# add a recurring interval job
opensprite cron add \
  --session telegram:user-a \
  --message "Check weather and report back" \
  --every-seconds 300

# add a calendar-based cron job
opensprite cron add \
  --session telegram:user-a \
  --message "Send a weekday reminder" \
  --cron-expr "0 9 * * 1-5" \
  --tz Asia/Taipei

# add a one-time job
opensprite cron add \
  --session telegram:user-a \
  --message "Remind me later" \
  --at 2026-04-10T09:00:00

# pause and re-enable a job
opensprite cron pause --session telegram:user-a --job-id abc12345
opensprite cron enable --session telegram:user-a --job-id abc12345

# remove a job
opensprite cron remove --session telegram:user-a --job-id abc12345
```

Inside chat sessions, these immediate cron commands are also available:

```text
/cron
/cron help
/cron add every <seconds> <message>
/cron add at <iso-datetime> <message>
/cron add cron "<expr>" [--tz <timezone>] <message>
/cron list
/cron pause <job_id>
/cron enable <job_id>
/cron remove <job_id>
```

These commands are handled immediately by the queue layer, similar to `/stop` and `/reset`, so they do not wait behind the current session task queue.

Examples:

```text
/cron add every 300 "Check weather and report back"
/cron add at 2026-04-10T09:00:00 "Remind me later"
/cron add cron "0 9 * * 1-5" --tz Asia/Taipei "Send weekday reminder"
/cron pause abc12345
/cron enable abc12345
/cron remove abc12345
```

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
├── search/         # SQLite-backed search, indexing, and embeddings
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

# inspect search index and embedding job state
opensprite search status

# rebuild indexed history and knowledge
opensprite search rebuild

# retry failed or refresh stale embeddings
opensprite search retry-embeddings
opensprite search refresh-embeddings

# run the embedding queue worker manually
opensprite search run-queue

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
