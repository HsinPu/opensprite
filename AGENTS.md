# AGENTS.md

## Verification

- Dependencies live in `pyproject.toml`; root `requirements.txt` is narrower and misses active package deps such as `croniter`, `httpx`, `mcp`, and `typer`.
- Python uses a setuptools `src` layout and requires 3.11+. Dev install from repo root: `python -m pip install -e ".[dev]"`. Add `,vector` only when exercising the optional `sqlite-vec` backend.
- `tests/conftest.py` prepends `src` to `sys.path`, so focused tests can run without installing the package. Example: `python -m pytest tests/channels/test_web.py::test_web_adapter_roundtrip`.
- There are no repo-configured ruff/black/mypy/pyright/pre-commit checks and no GitHub Actions workflows. The real baseline is `python -m pytest`.
- The web app lives in `apps/web` with its own `package-lock.json`. Run web checks there: `npm ci`, `npm run build`, `npm run dev`, `npm run preview`. On Windows PowerShell, `npm.cmd run build` avoids script execution-policy issues.

## Runtime Shape

- CLI entrypoint is `opensprite.cli.commands:app`; `python -m opensprite` imports the same Typer app from `src/opensprite/__main__.py`.
- `opensprite gateway` runs `src/opensprite/runtime.py` in the foreground and creates default config on first start; stop it with `Ctrl+C`.
- Gateway wiring is `Config -> storage/search/media -> AgentLoop -> MessageQueue -> channel adapters` in `src/opensprite/runtime.py`.
- `opensprite onboard` was removed. Do not reintroduce CLI provider/model/channel prompts; configure them through the Web UI Settings and the settings services.
- If README prose conflicts with runtime behavior, trust `src/opensprite/runtime.py`, `src/opensprite/channels/__init__.py`, and `src/opensprite/channels/registry.py`.

## Config And Channels

- Runtime config lives under `~/.opensprite`, not this repo. Do not commit app-home files, API keys, or SQLite runtime databases.
- `opensprite.json` fans out into sibling split files resolved relative to the main config path: `llm.providers.json`, `channels.json`, `search.json`, `media.json`, `messages.json`, and `mcp_servers.json`. Validate the whole set with `opensprite config validate --config <path>` or `--json`.
- Channel config source of truth is `channels.json` `instances`. `coerce_channel_instances()` still accepts legacy top-level `telegram` / `web` sections and auto-adds the `web` instance if it is missing.
- Only `telegram` is user-connectable through the channel registry. `web` is a fixed built-in instance. `console` still appears as a fixed instance in settings/runtime code, but there is no adapter factory for it.
- `search.backend="sqlite"` requires `storage.type="sqlite"`. If `search.embedding.enabled=true`, `search.embedding.model` is mandatory; embedding auth can fall back to the active LLM provider.

## Web App

- `apps/web/src/App.vue` is mostly shell wiring. Most browser chat/session/run behavior lives in `apps/web/src/composables/useChatClient.js`.
- Vite binds `127.0.0.1` and proxies `/ws`, `/healthz`, and `/api` to the gateway at `127.0.0.1:8765`.
- Web gateway routes are unauthenticated; keep the default loopback bind unless adding auth, firewall, or tunnel protections.
- `WebAdapter` will attempt `npm ci`/`npm install` when dependencies are missing and will build the frontend before gateway startup when `frontend_auto_install` / `frontend_auto_build` are enabled.
- Do not edit generated `apps/web/dist` or `apps/web/node_modules`.

## Prompt Assets

- This repo has two `AGENTS.md` files: root `AGENTS.md` is for repo work; `src/opensprite/templates/AGENTS.md` is a packaged runtime prompt asset.
- Bundled bootstrap prompts live in `src/opensprite/templates/{IDENTITY,SOUL,AGENTS,TOOLS,USER}.md`. They sync into `~/.opensprite/bootstrap` only when missing, so changing package templates does not refresh an existing app-home copy.
- If you add new bundled prompt, skill, or subagent asset patterns, update `[tool.setuptools.package-data]` in `pyproject.toml`.
