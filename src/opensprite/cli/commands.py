"""CLI entrypoints for OpenSprite."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import statistics
import time

import typer

from .. import __version__
from ..context.paths import get_session_workspace, get_tool_workspace
from ..cron import CronSchedule, CronService
from ..cron.presentation import format_cron_timestamp, format_cron_timing, render_cron_jobs
from ..runtime import gateway as run_gateway
from ..search.base import SearchHit
from ..storage.base import StoredMessage
from . import service_linux
from .onboard import run_onboard

app = typer.Typer(
    name="opensprite",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="OpenSprite CLI.",
)

service_app = typer.Typer(help="Manage the Linux systemd user service.")
app.add_typer(service_app, name="service")
cron_app = typer.Typer(help="Manage per-session scheduled jobs.")
app.add_typer(cron_app, name="cron")
search_app = typer.Typer(help="Manage the SQLite search index.")
app.add_typer(search_app, name="search")
config_app = typer.Typer(help="Inspect and validate configuration files.")
app.add_typer(config_app, name="config")


def version_callback(value: bool) -> None:
    """Print the package version and exit."""
    if value:
        typer.echo(f"opensprite {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show the OpenSprite version and exit.",
    ),
) -> None:
    """OpenSprite CLI."""
    return


def _start_gateway(config: str | None = None) -> None:
    """Start the OpenSprite gateway with optional config override."""
    try:
        run_gateway(config_path=config)
    except (FileNotFoundError, ValueError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _resolve_config_path(config: str | None = None) -> Path:
    """Resolve the OpenSprite config path without creating files."""
    if config:
        return Path(config).expanduser().resolve()
    return (Path.home() / ".opensprite" / "opensprite.json").resolve()


def _format_presence(value: bool) -> str:
    """Return a simple status label."""
    return "yes" if value else "no"


def _iter_channel_status(config_obj) -> list[tuple[str, bool]]:
    """Collect enabled/disabled channel flags from the loaded config."""
    channels = config_obj.channels.model_dump()
    instances = channels.get("instances") if isinstance(channels, dict) else None
    if isinstance(instances, dict):
        channels = instances
    results: list[tuple[str, bool]] = []
    for name, section in channels.items():
        enabled = False
        if isinstance(section, dict):
            enabled = bool(section.get("enabled", False))
        results.append((name, enabled))
    return results


def _emit_status(payload: dict[str, object], json_output: bool) -> None:
    """Render status output in text or JSON format."""
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.echo("OpenSprite Status")
    typer.echo(f"Version: {payload['version']}")
    typer.echo(
        f"Config: {payload['config_path']} [{_format_presence(bool(payload['config_exists']))}]"
    )
    typer.echo(
        f"App Home: {payload['app_home']} [{_format_presence(bool(payload['app_home_exists']))}]"
    )
    typer.echo(
        "Workspace Root: "
        f"{payload['workspace_root']} [{_format_presence(bool(payload['workspace_root_exists']))}]"
    )

    if not bool(payload["config_loaded"]):
        typer.echo(f"LLM Configured: {_format_presence(bool(payload['llm_configured']))}")
        typer.echo("Channels: unavailable (config file missing)")
        hint = payload.get("hint")
        if isinstance(hint, str) and hint:
            typer.echo(f"Hint: {hint}")
        return

    provider = payload["provider"]
    storage = payload["storage"]
    search = payload["search"]
    channels = payload["channels"]
    enabled_channels = [name for name, enabled in channels.items() if enabled]

    typer.echo(f"LLM Configured: {_format_presence(bool(payload['llm_configured']))}")
    typer.echo(
        "Provider: "
        f"{provider['name'] or '<unset>'} "
        f"(enabled={_format_presence(bool(provider['enabled']))}, "
        f"api_key={_format_presence(bool(provider['api_key_configured']))})"
    )
    typer.echo(f"Model: {provider['model']}")
    typer.echo(f"Storage: {storage['type']} -> {storage['path']}")
    typer.echo(
        "Search: "
        f"enabled={_format_presence(bool(search['enabled']))} "
        f"backend={search['backend']} "
        f"(history_top_k={search['history_top_k']}, knowledge_top_k={search['knowledge_top_k']})"
    )
    typer.echo(
        "Channels: " + (", ".join(enabled_channels) if enabled_channels else "none enabled")
    )


def _build_config_validate_payload(config: str | None = None) -> dict[str, object]:
    """Validate the main config and external split config files."""
    from ..config import Config

    config_path = _resolve_config_path(config)
    payload: dict[str, object] = {
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "valid": False,
        "files": [],
    }

    if not config_path.exists():
        payload["error"] = f"Config file does not exist: {config_path}"
        return payload

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        payload["error"] = str(exc)
        return payload

    if not isinstance(raw_data, dict):
        payload["error"] = f"Config file must contain a JSON object: {config_path}"
        return payload

    files = [
        ("main", config_path),
        ("llm.providers", Config.get_llm_providers_file_path(config_path, raw_data.get("llm", {}))),
        ("channels", Config.get_channels_file_path(config_path, raw_data)),
        ("search", Config.get_search_file_path(config_path, raw_data)),
        ("media", Config.get_media_file_path(config_path, raw_data)),
        ("messages", Config.get_messages_file_path(config_path, raw_data)),
        ("mcp_servers", Config.get_mcp_servers_file_path(config_path, raw_data.get("tools", {}))),
    ]

    file_payloads: list[dict[str, object]] = []
    missing_files: list[str] = []
    parse_errors: list[str] = []
    for label, file_path in files:
        exists = file_path.exists()
        entry: dict[str, object] = {
            "name": label,
            "path": str(file_path),
            "exists": exists,
            "valid_json": False,
        }
        if exists:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                entry["valid_json"] = isinstance(loaded, dict)
                if not isinstance(loaded, dict):
                    entry["error"] = "JSON root must be an object"
                    parse_errors.append(f"{label}: JSON root must be an object")
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                entry["error"] = str(exc)
                parse_errors.append(f"{label}: {exc}")
        else:
            missing_files.append(label)
        file_payloads.append(entry)

    payload["files"] = file_payloads

    if missing_files:
        payload["error"] = "Missing config files: " + ", ".join(missing_files)
        return payload
    if parse_errors:
        payload["error"] = "; ".join(parse_errors)
        return payload

    try:
        loaded = Config.load(config_path)
    except (FileNotFoundError, ValueError) as exc:
        payload["error"] = str(exc)
        return payload

    payload.update(
        {
            "valid": True,
            "llm_default": loaded.llm.default,
            "enabled_channels": [name for name, enabled in _iter_channel_status(loaded) if enabled],
            "search_enabled": loaded.search.enabled,
            "mcp_servers": sorted(loaded.tools.mcp_servers),
        }
    )
    return payload


def _emit_config_validate(payload: dict[str, object], json_output: bool) -> None:
    """Render config validation output."""
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.echo("OpenSprite Config Validation")
    typer.echo(f"Config: {payload['config_path']}")
    typer.echo(f"Exists: {_format_presence(bool(payload.get('config_exists')))}")
    typer.echo(f"Valid: {_format_presence(bool(payload.get('valid')))}")
    for entry in payload.get("files", []):
        if not isinstance(entry, dict):
            continue
        status = _format_presence(bool(entry.get("exists")))
        valid_json = _format_presence(bool(entry.get("valid_json")))
        typer.echo(f"- {entry.get('name')}: {entry.get('path')} [exists={status}, json={valid_json}]")
        error = entry.get("error")
        if isinstance(error, str) and error:
            typer.echo(f"  error: {error}")

    if payload.get("valid"):
        typer.echo(f"LLM default: {payload.get('llm_default') or '<unset>'}")
        enabled_channels = payload.get("enabled_channels") or []
        typer.echo("Enabled channels: " + (", ".join(enabled_channels) if enabled_channels else "none"))
        typer.echo(f"Search enabled: {_format_presence(bool(payload.get('search_enabled')))}")
        typer.echo("MCP servers: " + (", ".join(payload.get("mcp_servers") or []) or "none"))
        return

    error = payload.get("error")
    if isinstance(error, str) and error:
        typer.echo(f"Error: {error}")


def _run_onboard(
    config: str | None = None,
    *,
    force: bool = False,
    no_input: bool = False,
) -> None:
    """Run the OpenSprite onboarding workflow and print next steps."""
    try:
        result = run_onboard(config_path=config, force=force, interactive=not no_input)
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("OpenSprite onboarding complete.")
    typer.echo(f"Config: {result.config_path}")
    typer.echo(f"App Home: {result.app_home}")

    if result.created_config:
        typer.echo("Config status: created")
    elif result.reset_config:
        typer.echo("Config status: reset to defaults")
    elif result.refreshed_config:
        typer.echo("Config status: refreshed with missing defaults")
    else:
        typer.echo("Config status: unchanged")

    if result.created_dirs:
        typer.echo("Created directories:")
        for path in result.created_dirs:
            typer.echo(f"- {path}")

    if result.template_files:
        typer.echo("Added template files:")
        for relative_path in result.template_files:
            typer.echo(f"- {relative_path}")

    if result.interactive:
        typer.echo("Interactive setup:")
        typer.echo(f"- LLM provider: {result.llm_provider or '<unset>'}")
        typer.echo(f"- Model: {result.llm_model or '<unset>'}")
        typer.echo(f"- Channel: {result.channel_name or '<unset>'}")

    typer.echo("Next steps:")
    if not result.llm_api_key_configured:
        typer.echo(f"1. Edit {result.config_path} and add your API key")
        typer.echo("2. Run `opensprite status` to verify the setup")
        typer.echo("3. Start the service with `opensprite gateway`")
    else:
        typer.echo("1. Run `opensprite status` to verify the setup")
        typer.echo("2. Start the service with `opensprite gateway`")


@app.command()
def onboard(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Reset the config to the packaged defaults instead of refreshing missing fields.",
    ),
    no_input: bool = typer.Option(
        False,
        "--no-input",
        help="Initialize files only and skip interactive prompts.",
    ),
) -> None:
    """Initialize or refresh OpenSprite and prompt for key settings by default."""
    _run_onboard(config=config, force=force, no_input=no_input)


@app.command()
def status(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output status as JSON.",
    ),
) -> None:
    """Show OpenSprite configuration and runtime status."""
    from ..config import Config

    config_path = _resolve_config_path(config)
    app_home = (Path.home() / ".opensprite").resolve()
    workspace_root = (app_home / "workspace").resolve()

    payload: dict[str, object] = {
        "version": __version__,
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "config_loaded": False,
        "app_home": str(app_home),
        "app_home_exists": app_home.exists(),
        "workspace_root": str(workspace_root),
        "workspace_root_exists": workspace_root.exists(),
        "llm_configured": False,
    }

    if not config_path.exists():
        payload["hint"] = "run `opensprite onboard` to create the default config and app directories."
        _emit_status(payload, json_output)
        return

    try:
        loaded = Config.load(config_path)
    except (FileNotFoundError, ValueError) as exc:
        if json_output:
            payload["error"] = str(exc)
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    selected_provider = loaded.llm.default if loaded.llm.default in loaded.llm.providers else None
    if selected_provider is not None:
        active_provider = loaded.llm.providers[selected_provider]
        provider_enabled = bool(getattr(active_provider, "enabled", False))
        provider_has_key = bool(active_provider.api_key)
        model_name = active_provider.model or "<unset>"
    else:
        provider_enabled = False
        provider_has_key = False
        model_name = "<unset>"
    storage_path = Path(loaded.storage.path).expanduser()
    channels = {name: enabled for name, enabled in _iter_channel_status(loaded)}

    payload.update(
        {
            "config_loaded": True,
            "llm_configured": loaded.is_llm_configured,
            "provider": {
                "name": selected_provider,
                "enabled": provider_enabled,
                "api_key_configured": provider_has_key,
                "model": model_name,
            },
            "storage": {
                "type": loaded.storage.type,
                "path": str(storage_path),
            },
            "search": {
                "enabled": loaded.search.enabled,
                "backend": loaded.search.backend,
                "history_top_k": loaded.search.history_top_k,
                "knowledge_top_k": loaded.search.knowledge_top_k,
            },
            "channels": channels,
        }
    )
    _emit_status(payload, json_output)


@app.command()
def gateway(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Start the OpenSprite gateway."""
    _start_gateway(config=config)


@config_app.command("validate")
def config_validate(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output validation details as JSON.",
    ),
) -> None:
    """Validate the main config and all split external config files."""
    payload = _build_config_validate_payload(config)
    _emit_config_validate(payload, json_output)
    if not bool(payload.get("valid")):
        raise typer.Exit(code=1)


@search_app.command("rebuild")
def search_rebuild(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Optional session id to rebuild instead of rebuilding the full index.",
    ),
) -> None:
    """Rebuild the SQLite search index from stored messages."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        result = asyncio.run(search_store.rebuild_index(session_id=session_id))
        status = asyncio.run(search_store.wait_for_embedding_idle())
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = session_id or "all sessions"
    typer.echo(f"Rebuilt search index for {scope}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(f"Sessions: {result['session_count']}")
    typer.echo(f"Messages: {result['message_count']}")
    typer.echo(f"Knowledge sources: {result['knowledge_count']}")
    typer.echo(f"Chunks: {result['chunk_count']}")
    typer.echo(
        "Embeddings: "
        f"queued={status['queued']} pending={status['pending']} processing={status['processing']} completed={status['completed']} failed={status['failed']} missing={status['missing']} stale={status['stale']}"
    )


@search_app.command("status")
def search_status(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Optional session id to inspect instead of the full search index.",
    ),
) -> None:
    """Show SQLite search index and embedding status."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        status = asyncio.run(search_store.get_status(session_id=session_id))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = session_id or "all sessions"
    embedding = loaded.search.embedding
    typer.echo(f"Search status for {scope}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(f"Sessions: {status['session_count']}")
    typer.echo(f"Messages: {status['message_count']}")
    typer.echo(f"Knowledge sources: {status['knowledge_count']}")
    typer.echo(f"Chunks: {status['chunk_count']}")
    typer.echo(
        "Embedding: "
        f"enabled={_format_presence(bool(embedding.enabled))} "
        f"provider={embedding.provider} model={embedding.model or '<unset>'} "
        f"candidate_strategy={embedding.candidate_strategy} "
        f"vector_backend={embedding.vector_backend} "
        f"retry_failed_on_startup={_format_presence(bool(embedding.retry_failed_on_startup))}"
    )
    typer.echo(
        "Embedding jobs: "
        f"total={status['embedding_total']} queued={status['queued']} pending={status['pending']} processing={status['processing']} completed={status['completed']} failed={status['failed']} missing={status['missing']} stale={status['stale']}"
    )
    typer.echo(
        "Vector backend: "
        f"requested={status['vector_backend_requested']} effective={status['vector_backend_effective']}"
    )
    typer.echo(
        "Queue worker: "
        f"running={_format_presence(bool(status['worker_running']))} "
        f"owner={status['worker_owner'] or '<none>'} "
        f"expires={_format_search_timestamp(status['worker_expires_at'])}"
    )
    typer.echo(
        "Last queue run: "
        f"mode={status['last_run_mode'] or '<none>'} "
        f"started={_format_search_timestamp(status['last_run_started_at'])} "
        f"finished={_format_search_timestamp(status['last_run_finished_at'])} "
        f"refreshed={status['last_run_refreshed']} processed={status['last_run_processed']} failed={status['last_run_failed']}"
    )


@search_app.command("refresh-embeddings")
def search_refresh_embeddings(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Optional session id to refresh instead of refreshing all embeddings.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Recompute all embeddings in scope, not just missing or stale ones.",
    ),
) -> None:
    """Queue embedding refresh work and wait for it to complete."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        if not loaded.search.embedding.enabled:
            raise ValueError("search.embedding.enabled=false; enable embeddings first")
        status = asyncio.run(search_store.refresh_embeddings(session_id=session_id, force=force, wait=True))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = session_id or "all sessions"
    embedding = loaded.search.embedding
    typer.echo(f"Refreshed embeddings for {scope}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(
        "Embedding: "
        f"enabled={_format_presence(bool(embedding.enabled))} "
        f"provider={embedding.provider} model={embedding.model or '<unset>'} force={_format_presence(bool(force))}"
    )
    typer.echo(f"Refreshed: {status['refreshed']}")
    typer.echo(
        "Embedding jobs: "
        f"total={status['embedding_total']} queued={status['queued']} pending={status['pending']} processing={status['processing']} completed={status['completed']} failed={status['failed']} missing={status['missing']} stale={status['stale']}"
    )


@search_app.command("run-queue")
def search_run_queue(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    watch: bool = typer.Option(
        False,
        "--watch",
        help="Keep polling the embedding queue instead of draining it once.",
    ),
    poll_interval: float = typer.Option(
        5.0,
        "--poll-interval",
        help="Polling interval in seconds when --watch is enabled.",
    ),
    idle_exit_seconds: float | None = typer.Option(
        None,
        "--idle-exit-seconds",
        help="Exit watch mode after this many idle seconds.",
    ),
    force_refresh: bool = typer.Option(
        False,
        "--force-refresh",
        help="Refresh all embeddings before draining the queue.",
    ),
) -> None:
    """Run the embedding queue worker once or in watch mode."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        if not loaded.search.embedding.enabled:
            raise ValueError("search.embedding.enabled=false; enable embeddings first")
        status = asyncio.run(
            search_store.run_queue(
                once=not watch,
                poll_interval=poll_interval,
                idle_exit_seconds=idle_exit_seconds,
                force_refresh=force_refresh,
            )
        )
    except KeyboardInterrupt:
        typer.secho("Search queue stopped.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=130)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = "watch" if watch else "once"
    typer.echo(f"Ran search queue in {scope} mode.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(
        "Embedding jobs: "
        f"total={status['embedding_total']} queued={status['queued']} pending={status['pending']} processing={status['processing']} completed={status['completed']} failed={status['failed']} missing={status['missing']} stale={status['stale']}"
    )
    typer.echo(
        f"Queue run: refreshed={status['refreshed']} processed={status['processed_chunks']} failed={status['failed_chunks_run']}"
    )


@search_app.command("benchmark")
def search_benchmark(
    query: str = typer.Option(..., "--query", help="Search query to benchmark."),
    session_id: str = typer.Option(..., "--session-id", help="Session id to benchmark against."),
    kind: str = typer.Option("knowledge", "--kind", help="Benchmark `history` or `knowledge` search."),
    strategy: str = typer.Option("both", "--strategy", help="Benchmark `fts`, `vector`, or `both`."),
    vector_backend: str | None = typer.Option(None, "--vector-backend", help="Override vector backend for this benchmark: `exact`, `sqlite_vec`, `auto`, or `both` (compare exact vs sqlite_vec)."),
    limit: int = typer.Option(5, "--limit", help="Maximum hits to show per strategy."),
    repeat: int = typer.Option(3, "--repeat", help="How many runs to execute per strategy."),
    json_output: bool = typer.Option(False, "--json", help="Emit benchmark output as JSON."),
    demo_embeddings: bool = typer.Option(False, "--demo-embeddings", help="Use a deterministic local embedding provider so vector benchmarks can run without a remote API key."),
    source_type: str | None = typer.Option(None, "--source-type", help="Knowledge source filter for knowledge benchmarks."),
    provider: str | None = typer.Option(None, "--provider", help="Knowledge provider filter for knowledge benchmarks."),
    extractor: str | None = typer.Option(None, "--extractor", help="Knowledge extractor filter for knowledge benchmarks."),
    status: int | None = typer.Option(None, "--status", help="Knowledge status filter for knowledge benchmarks."),
    content_type: str | None = typer.Option(None, "--content-type", help="Knowledge content type filter for knowledge benchmarks."),
    truncated: bool | None = typer.Option(None, "--truncated", help="Knowledge truncation filter for knowledge benchmarks."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
) -> None:
    """Benchmark FTS and vector candidate strategies for one session query."""
    if kind not in {"history", "knowledge"}:
        _handle_search_error("--kind must be 'history' or 'knowledge'")
    if strategy not in {"fts", "vector", "both"}:
        _handle_search_error("--strategy must be 'fts', 'vector', or 'both'")
    if vector_backend is not None and vector_backend not in {"exact", "sqlite_vec", "auto", "both"}:
        _handle_search_error("--vector-backend must be 'exact', 'sqlite_vec', 'auto', or 'both'")
    if repeat < 1:
        _handle_search_error("--repeat must be greater than 0")

    try:
        from ..config import Config
        from ..search.embeddings import LocalHashEmbeddingProvider

        loaded = Config.load(_resolve_config_path(config))
        if not loaded.search.enabled:
            raise ValueError("search.enabled=false; enable search first")
        strategies = ["fts", "vector"] if strategy == "both" else [strategy]
        benchmark_embedding_provider = LocalHashEmbeddingProvider() if demo_embeddings else None
        vector_available = loaded.search.embedding.enabled or benchmark_embedding_provider is not None
        if strategy == "vector" and not vector_available:
            raise ValueError("search.embedding.enabled=false; vector benchmarks require embeddings")
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    backend_overrides = [vector_backend] if vector_backend not in {None, "both"} else (["exact", "sqlite_vec"] if vector_backend == "both" else [None])

    filters = []
    if kind == "knowledge":
        if source_type:
            filters.append(f"source_type={source_type}")
        if provider:
            filters.append(f"provider={provider}")
        if extractor:
            filters.append(f"extractor={extractor}")
        if status is not None:
            filters.append(f"status={status}")
        if content_type:
            filters.append(f"content_type={content_type}")
        if truncated is not None:
            filters.append(f"truncated={'yes' if truncated else 'no'}")

    if "vector" in strategies and not vector_available:
        if not json_output:
            typer.echo("Vector benchmark skipped because embeddings are disabled.")
        strategies = [item for item in strategies if item != "vector"]

    benchmark_payload: dict[str, object] = {
        "session_id": session_id,
        "kind": kind,
        "query": query,
        "repeat": repeat,
        "demo_embeddings": demo_embeddings,
        "vector_backend": vector_backend or getattr(loaded.search.embedding, "vector_backend", "exact"),
        "filters": filters,
        "strategies": [],
        "comparison": {},
    }

    if not json_output:
        typer.echo(f"Search benchmark for {session_id} ({kind}).")
        typer.echo(f"Query: {query}")
        if vector_backend:
            typer.echo(f"Vector backend override: {vector_backend}")
        if kind == "knowledge":
            typer.echo(f"Filters: {', '.join(filters) if filters else '<none>'}")

    for candidate_strategy in strategies:
        strategy_backends = backend_overrides if candidate_strategy == "vector" else [None]
        for backend_override in strategy_backends:
            try:
                embedding_override = benchmark_embedding_provider if candidate_strategy == "vector" and benchmark_embedding_provider is not None else None
                search_store = _build_sqlite_search_store(
                    loaded,
                    candidate_strategy=candidate_strategy,
                    vector_backend=backend_override,
                    embedding_provider_override=embedding_override,
                )
                if embedding_override is not None:
                    asyncio.run(search_store.refresh_embeddings(force=False, wait=True))
                elapsed_runs: list[float] = []
                hits = []
                for _ in range(repeat):
                    elapsed_ms, hits = _benchmark_one_strategy(
                        search_store,
                        kind=kind,
                        session_id=session_id,
                        query=query,
                        limit=limit,
                        source_type=source_type,
                        provider=provider,
                        extractor=extractor,
                        status=status,
                        content_type=content_type,
                        truncated=truncated,
                    )
                    elapsed_runs.append(elapsed_ms)
            except (ValueError, RuntimeError) as exc:
                _handle_search_error(exc)

            summary = _summarize_benchmark_runs(elapsed_runs)
            label = candidate_strategy
            if candidate_strategy == "vector" and backend_override is not None:
                label = f"vector:{backend_override}"
            benchmark_payload["strategies"].append(
                {
                    "strategy": label,
                    "candidate_strategy": candidate_strategy,
                    "vector_backend_requested": getattr(search_store, "vector_backend_requested", None),
                    "vector_backend_effective": getattr(search_store, "vector_backend_effective", None),
                    "summary": summary,
                    "hit_count": len(hits),
                    "hits": _serialize_benchmark_hits(hits, limit=limit),
                    "_raw_hits": hits,
                }
            )

            if json_output:
                continue

            typer.echo("")
            typer.echo(f"Strategy: {label}")
            if candidate_strategy == "vector":
                typer.echo(
                    "Vector backend: "
                    f"requested={getattr(search_store, 'vector_backend_requested', '<unknown>')} "
                    f"effective={getattr(search_store, 'vector_backend_effective', '<unknown>')}"
                )
            typer.echo(
                "Elapsed: "
                f"avg={summary['avg_ms']:.2f} ms min={summary['min_ms']:.2f} ms "
                f"max={summary['max_ms']:.2f} ms median={summary['median_ms']:.2f} ms "
                f"runs={int(summary['runs'])}"
            )
            typer.echo(f"Hits: {len(hits)}")
            preview_lines = _render_benchmark_hits(hits, limit=limit)
            if preview_lines:
                for line in preview_lines:
                    typer.echo(line)
            else:
                typer.echo("<no hits>")

    comparison = _compare_benchmark_results(list(benchmark_payload["strategies"]))
    if comparison:
        benchmark_payload["comparison"] = comparison
        if not json_output:
            for pair in comparison.get("pairs", []):
                typer.echo("")
                typer.echo(
                    "Comparison: "
                    f"{pair['strategies'][0]} vs {pair['strategies'][1]} | "
                    f"overlap={pair['overlap_count']} ({pair['overlap_ratio']:.2%}) "
                    f"top_hit_same={_format_presence(bool(pair['top_hit_same']))} "
                    f"{pair['strategies'][0]}_only={pair['first_only_count']} "
                    f"{pair['strategies'][1]}_only={pair['second_only_count']}"
                )
                if pair["first_top"] or pair["second_top"]:
                    typer.echo(
                        "Top hits: "
                        f"{pair['strategies'][0]}={pair['first_top'] or '<none>'} | "
                        f"{pair['strategies'][1]}={pair['second_top'] or '<none>'}"
                    )

    if json_output:
        strategies_payload = []
        for item in benchmark_payload["strategies"]:
            serialized = dict(item)
            serialized.pop("_raw_hits", None)
            strategies_payload.append(serialized)
        benchmark_payload["strategies"] = strategies_payload
        typer.echo(json.dumps(benchmark_payload, ensure_ascii=False, indent=2))


@search_app.command("seed-demo")
def search_seed_demo(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
    session_id: str = typer.Option("demo:search-benchmark", "--session-id", help="Session id that will receive the synthetic benchmark dataset."),
    reset: bool = typer.Option(True, "--reset/--append", help="Replace any existing demo chat data before seeding."),
) -> None:
    """Seed synthetic session history and web knowledge so benchmark commands can run without real user data."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        result = asyncio.run(_seed_demo_search_data(loaded, search_store, session_id=session_id, reset=reset))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    typer.echo(f"Seeded demo search data for {session_id}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(f"Messages: {result['messages']}")
    typer.echo(f"Knowledge sources: {result['knowledge_sources']}")
    typer.echo(f"Chunks: {result['chunks']}")
    typer.echo(f"Completed embeddings: {result['completed']}")
    typer.echo("Try:")
    typer.echo(f"  opensprite search benchmark --session-id {session_id} --query \"orchard irrigation\" --strategy both --repeat 5")


@search_app.command("retry-embeddings")
def search_retry_embeddings(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Optional session id to retry instead of retrying all failed embeddings.",
    ),
) -> None:
    """Retry failed embedding jobs and wait for the queue to go idle."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        if not loaded.search.embedding.enabled:
            raise ValueError("search.embedding.enabled=false; enable embeddings first")
        status = asyncio.run(search_store.retry_failed_embeddings(session_id=session_id, wait=True))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = session_id or "all sessions"
    embedding = loaded.search.embedding
    typer.echo(f"Retried failed embeddings for {scope}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(
        "Embedding: "
        f"enabled={_format_presence(bool(embedding.enabled))} "
        f"provider={embedding.provider} model={embedding.model or '<unset>'}"
    )
    typer.echo(f"Retried: {status['retried']}")
    typer.echo(
        "Embedding jobs: "
        f"total={status['embedding_total']} queued={status['queued']} pending={status['pending']} processing={status['processing']} completed={status['completed']} failed={status['failed']} missing={status['missing']} stale={status['stale']}"
    )


def _handle_service_error(exc: Exception) -> None:
    """Render a service-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1) from exc


def _handle_cron_error(exc: Exception | str) -> None:
    """Render a cron-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _handle_search_error(exc: Exception | str) -> None:
    """Render a search-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _format_search_timestamp(value: float | None) -> str:
    """Render an optional unix timestamp for search status output."""
    if not value:
        return "never"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _render_benchmark_hits(hits, *, limit: int) -> list[str]:
    """Render a compact benchmark preview for the top hits."""
    lines: list[str] = []
    for index, hit in enumerate(hits[:limit], start=1):
        title = hit.title or hit.source_type
        score = f" score={hit.score:.4f}" if hit.score is not None else ""
        lines.append(f"{index}. [{hit.source_type}] {title}{score}")
        if hit.url:
            lines.append(f"   {hit.url}")
    return lines


def _serialize_benchmark_hits(hits, *, limit: int) -> list[dict[str, object]]:
    """Convert benchmark hits into a compact JSON-friendly structure."""
    payload: list[dict[str, object]] = []
    for hit in hits[:limit]:
        payload.append(
            {
                "id": hit.id,
                "source_type": hit.source_type,
                "title": hit.title,
                "url": hit.url,
                "score": hit.score,
                "content": hit.content,
            }
        )
    return payload


def _summarize_benchmark_runs(elapsed_runs: list[float]) -> dict[str, float]:
    """Summarize repeated benchmark timings."""
    if not elapsed_runs:
        return {"runs": 0.0, "avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0, "median_ms": 0.0}
    return {
        "runs": float(len(elapsed_runs)),
        "avg_ms": float(statistics.mean(elapsed_runs)),
        "min_ms": float(min(elapsed_runs)),
        "max_ms": float(max(elapsed_runs)),
        "median_ms": float(statistics.median(elapsed_runs)),
    }


def _benchmark_hit_identity(hit: SearchHit) -> str:
    """Build a stable identity for comparing benchmark result overlap."""
    if hit.url:
        return f"url:{hit.url}"
    title = hit.title or hit.source_type or hit.id
    return f"title:{title}|source:{hit.source_type}"


def _compare_benchmark_results(results: list[dict[str, object]]) -> dict[str, object]:
    """Compare benchmark outputs pairwise by overlap and top-hit agreement."""
    if len(results) < 2:
        return {}

    comparisons = []
    for index in range(len(results) - 1):
        first = results[index]
        second = results[index + 1]
        first_hits = first.get("_raw_hits", [])
        second_hits = second.get("_raw_hits", [])
        first_keys = {_benchmark_hit_identity(hit) for hit in first_hits}
        second_keys = {_benchmark_hit_identity(hit) for hit in second_hits}
        overlap = first_keys & second_keys
        union = first_keys | second_keys
        first_top = _benchmark_hit_identity(first_hits[0]) if first_hits else None
        second_top = _benchmark_hit_identity(second_hits[0]) if second_hits else None

        comparisons.append(
            {
                "strategies": [first.get("strategy"), second.get("strategy")],
                "overlap_count": len(overlap),
                "overlap_ratio": (len(overlap) / len(union)) if union else 1.0,
                "top_hit_same": bool(first_top and second_top and first_top == second_top),
                "first_only_count": len(first_keys - second_keys),
                "second_only_count": len(second_keys - first_keys),
                "first_top": first_top,
                "second_top": second_top,
            }
        )

    return {"pairs": comparisons}


def _resolve_workspace_root() -> Path:
    """Resolve the default workspace root used by cron CLI commands."""
    return get_tool_workspace()


def _load_sqlite_search_store(config: str | None = None):
    """Load the configured SQLite search store or fail with a clear message."""
    from ..config import Config
    from ..runtime import create_search_store
    from ..search.sqlite_store import SQLiteSearchStore

    loaded = Config.load(_resolve_config_path(config))
    search_store = create_search_store(loaded)
    if search_store is None:
        raise ValueError("search.enabled=false; enable search first")
    if not isinstance(search_store, SQLiteSearchStore):
        raise ValueError("configured search backend does not support rebuild")
    return loaded, search_store


def _build_sqlite_search_store(
    loaded,
    *,
    candidate_strategy: str | None = None,
    vector_backend: str | None = None,
    embedding_provider_override=None,
):
    """Build a SQLite search store from an already loaded config."""
    from ..runtime import create_search_embedding_provider
    from ..search.sqlite_store import SQLiteSearchStore

    embedding_provider = embedding_provider_override or create_search_embedding_provider(loaded)
    strategy = candidate_strategy or loaded.search.embedding.candidate_strategy
    backend = vector_backend or loaded.search.embedding.vector_backend
    return SQLiteSearchStore(
        path=loaded.storage.path,
        history_top_k=loaded.search.history_top_k,
        knowledge_top_k=loaded.search.knowledge_top_k,
        embedding_provider=embedding_provider,
        hybrid_candidate_count=loaded.search.embedding.candidate_count,
        embedding_candidate_strategy=strategy,
        vector_backend=backend,
        vector_candidate_count=loaded.search.embedding.vector_candidate_count,
        retry_failed_on_startup=loaded.search.embedding.retry_failed_on_startup,
    )


def _benchmark_one_strategy(
    search_store,
    *,
    kind: str,
    session_id: str,
    query: str,
    limit: int,
    source_type: str | None = None,
    provider: str | None = None,
    extractor: str | None = None,
    status: int | None = None,
    content_type: str | None = None,
    truncated: bool | None = None,
):
    """Run one benchmark query and return elapsed time with hits."""
    started = time.perf_counter()
    if kind == "history":
        hits = asyncio.run(search_store.search_history(session_id, query, limit=limit))
    else:
        hits = asyncio.run(
            search_store.search_knowledge(
                session_id,
                query,
                limit=limit,
                source_type=source_type,
                provider=provider,
                extractor=extractor,
                status=status,
                content_type=content_type,
                truncated=truncated,
            )
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, hits


def _demo_search_payload(query: str, title: str, url: str, content: str, *, provider: str = "duckduckgo") -> str:
    """Build a demo web_search payload."""
    return json.dumps(
        {
            "type": "web_search",
            "query": query,
            "url": "",
            "final_url": "",
            "title": "",
            "content": "",
            "summary": f"Search results for: {query}",
            "provider": provider,
            "extractor": "search",
            "status": None,
            "truncated": False,
            "content_type": "application/json",
            "items": [
                {
                    "title": title,
                    "url": url,
                    "content": content,
                }
            ],
        },
        ensure_ascii=False,
    )


def _demo_fetch_payload(url: str, title: str, content: str, *, extractor: str = "trafilatura") -> str:
    """Build a demo web_fetch payload."""
    return json.dumps(
        {
            "type": "web_fetch",
            "query": url,
            "url": url,
            "final_url": url,
            "title": title,
            "content": content,
            "summary": title,
            "provider": "web_fetch",
            "extractor": extractor,
            "status": 200,
            "content_type": "text/html",
            "truncated": False,
            "items": [],
        },
        ensure_ascii=False,
    )


async def _seed_demo_search_data(loaded, search_store, *, session_id: str, reset: bool) -> dict[str, int]:
    """Seed one synthetic session with history and stored web knowledge."""
    from ..storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(loaded.storage.path)
    if reset:
        await storage.clear_messages(session_id)
        await search_store.clear_session(session_id)

    seeded_messages = [
        StoredMessage(role="user", content="Compare SQLite FTS and vector search strategies.", timestamp=10.0),
        StoredMessage(role="assistant", content="I can compare retrieval speed and relevance once benchmark data is ready.", timestamp=11.0),
        StoredMessage(role="user", content="Focus on orchard planning, irrigation, and soil notes.", timestamp=12.0),
    ]

    for message in seeded_messages:
        await storage.add_message(session_id, message)
        await search_store.index_message(
            session_id,
            role=message.role,
            content=message.content,
            tool_name=message.tool_name,
            created_at=message.timestamp,
        )

    demo_knowledge = [
        (
            "web_search",
            {"query": "orchard irrigation guide", "provider": "duckduckgo"},
            _demo_search_payload(
                "orchard irrigation guide",
                "Orchard Irrigation Guide",
                "https://example.com/orchard-irrigation",
                "Irrigation scheduling, soil moisture, and orchard planning checklist.",
            ),
            20.0,
        ),
        (
            "web_fetch",
            {"url": "https://example.com/orchard-irrigation"},
            _demo_fetch_payload(
                "https://example.com/orchard-irrigation",
                "Orchard Irrigation Guide",
                "This guide covers orchard layout, irrigation timing, soil moisture targets, and benchmark-friendly notes about planning healthy trees.",
            ),
            21.0,
        ),
        (
            "web_search",
            {"query": "soil amendment notes", "provider": "duckduckgo"},
            _demo_search_payload(
                "soil amendment notes",
                "Soil Amendment Notes",
                "https://example.com/soil-notes",
                "Soil notes on compost, drainage, and amendment timing for orchards.",
            ),
            22.0,
        ),
        (
            "web_fetch",
            {"url": "https://example.com/soil-notes"},
            _demo_fetch_payload(
                "https://example.com/soil-notes",
                "Soil Amendment Notes",
                "Detailed soil notes covering compost rates, drainage fixes, and orchard soil preparation steps.",
            ),
            23.0,
        ),
    ]

    for tool_name, tool_args, payload, created_at in demo_knowledge:
        tool_message = StoredMessage(role="tool", content=payload, timestamp=created_at, tool_name=tool_name)
        await storage.add_message(session_id, tool_message)
        await search_store.index_message(
            session_id,
            role=tool_message.role,
            content=tool_message.content,
            tool_name=tool_message.tool_name,
            created_at=tool_message.timestamp,
        )
        await search_store.index_tool_result(
            session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            result=payload,
            created_at=created_at,
        )

    status = await search_store.wait_for_embedding_idle()
    return {
        "messages": len(seeded_messages) + len(demo_knowledge),
        "knowledge_sources": 4,
        "chunks": status["chunk_count"],
        "completed": status["completed"],
    }


def _get_cron_service(session: str) -> CronService:
    """Open the cron service store for a session without starting a timer loop."""
    workspace = get_session_workspace(session, workspace_root=_resolve_workspace_root())
    return CronService(workspace / "cron" / "jobs.json", session_id=session)


def _build_cli_schedule(
    *,
    every_seconds: int | None,
    cron_expr: str | None,
    tz: str | None,
    at: str | None,
    default_timezone: str = "UTC",
) -> tuple[CronSchedule, bool]:
    """Build a CronSchedule from CLI arguments."""
    provided = [every_seconds is not None, bool(cron_expr), bool(at)]
    if sum(provided) != 1:
        raise ValueError("provide exactly one of --every-seconds, --cron-expr, or --at")
    if tz and not cron_expr:
        raise ValueError("--tz can only be used with --cron-expr")

    if every_seconds is not None:
        if every_seconds <= 0:
            raise ValueError("--every-seconds must be greater than 0")
        return CronSchedule(kind="every", every_ms=every_seconds * 1000), False

    if cron_expr:
        return CronSchedule(kind="cron", expr=cron_expr, tz=tz or default_timezone), False

    try:
        dt = datetime.fromisoformat(at or "")
    except ValueError as exc:
        raise ValueError("--at must use ISO format like 2026-04-10T09:00:00") from exc

    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo

        dt = dt.replace(tzinfo=ZoneInfo(default_timezone))
    return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000)), True


def _format_cron_timestamp(ms: int, tz_name: str) -> str:
    """Format a scheduled timestamp for CLI output."""
    return format_cron_timestamp(ms, tz_name)


def _format_cron_timing(schedule: CronSchedule, default_timezone: str = "UTC") -> str:
    """Format a cron schedule in the same style as the runtime tool."""
    return format_cron_timing(schedule, default_timezone)


def _load_cli_cron_messages(config: str | None = None):
    from ..config import Config, CronMessagesConfig

    config_path = _resolve_config_path(config)
    if not config_path.exists():
        return CronMessagesConfig()
    try:
        return Config.from_json(config_path).messages.cron
    except Exception:
        return CronMessagesConfig()


def _render_cron_jobs(service: CronService, default_timezone: str = "UTC", *, messages=None) -> str:
    """Render the stored jobs for CLI list output."""
    messages = messages or _load_cli_cron_messages()
    return render_cron_jobs(service.list_jobs(include_disabled=True), messages, default_timezone=default_timezone)


@service_app.command("install")
def service_install(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    start: bool = typer.Option(
        True,
        "--start/--no-start",
        help="Start the service immediately after installation.",
    ),
) -> None:
    """Install OpenSprite as a Linux systemd user service."""
    try:
        config_path = _resolve_config_path(config)
        service_file = service_linux.install_service(config_path, start=start)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _handle_service_error(exc)

    typer.echo(f"Installed service: {service_file}")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Started: {'yes' if start else 'no'}")
    typer.echo("Tip: run `loginctl enable-linger $USER` if you want the user service to stay up after logout.")


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Uninstall the OpenSprite Linux systemd user service."""
    try:
        removed = service_linux.uninstall_service()
    except RuntimeError as exc:
        _handle_service_error(exc)

    if removed:
        typer.echo("Removed OpenSprite service.")
    else:
        typer.echo("OpenSprite service is not installed.")


@service_app.command("start")
def service_start() -> None:
    """Start the installed OpenSprite Linux systemd user service."""
    try:
        service_linux.start_service()
    except (FileNotFoundError, RuntimeError) as exc:
        _handle_service_error(exc)
    typer.echo("Started OpenSprite service.")


@service_app.command("stop")
def service_stop() -> None:
    """Stop the installed OpenSprite Linux systemd user service."""
    try:
        service_linux.stop_service()
    except (FileNotFoundError, RuntimeError) as exc:
        _handle_service_error(exc)
    typer.echo("Stopped OpenSprite service.")


@service_app.command("restart")
def service_restart() -> None:
    """Restart the installed OpenSprite Linux systemd user service."""
    try:
        service_linux.restart_service()
    except (FileNotFoundError, RuntimeError) as exc:
        _handle_service_error(exc)
    typer.echo("Restarted OpenSprite service.")


@service_app.command("status")
def service_status() -> None:
    """Show OpenSprite Linux systemd user service status."""
    try:
        status = service_linux.get_service_status()
    except RuntimeError as exc:
        _handle_service_error(exc)

    typer.echo("OpenSprite Service")
    typer.echo(f"Service File: {status.service_file}")
    typer.echo(f"Installed: {_format_presence(status.installed)}")
    typer.echo(f"Enabled: {_format_presence(status.enabled)}")
    typer.echo(f"Active: {_format_presence(status.active)}")


@cron_app.command("list")
def cron_list(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """List scheduled jobs for one session."""
    service = _get_cron_service(session)
    messages = _load_cli_cron_messages(config)
    typer.echo(_render_cron_jobs(service, messages=messages))


@cron_app.command("add")
def cron_add(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    message: str = typer.Option(
        ...,
        "--message",
        help="Instruction to execute when the job triggers.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Optional short label for the job.",
    ),
    every_seconds: int | None = typer.Option(
        None,
        "--every-seconds",
        help="Fixed recurring interval in seconds.",
    ),
    cron_expr: str | None = typer.Option(
        None,
        "--cron-expr",
        help="Cron expression like '0 9 * * *'.",
    ),
    tz: str | None = typer.Option(
        None,
        "--tz",
        help="Optional IANA timezone for cron expressions.",
    ),
    at: str | None = typer.Option(
        None,
        "--at",
        help="ISO datetime for one-time execution, e.g. 2026-04-10T09:00:00.",
    ),
    deliver: bool = typer.Option(
        True,
        "--deliver/--no-deliver",
        help="Whether the job should send its result back to the original chat.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Add a scheduled job to one session."""
    messages = _load_cli_cron_messages(config)
    try:
        schedule, delete_after = _build_cli_schedule(
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            tz=tz,
            at=at,
        )
        service = _get_cron_service(session)
        if ":" in session:
            channel, chat_id = session.split(":", 1)
        else:
            channel, chat_id = "default", session
        job = service.add_job(
            name=name or message[:30],
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            chat_id=chat_id,
            delete_after_run=delete_after,
        )
    except ValueError as exc:
        _handle_cron_error(exc)

    typer.echo(messages.created_job.format(name=job.name, job_id=job.id))


@cron_app.command("remove")
def cron_remove(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to remove.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Remove one scheduled job from a session."""
    messages = _load_cli_cron_messages(config)
    service = _get_cron_service(session)
    if not service.remove_job(job_id):
        _handle_cron_error(messages.job_not_found.format(job_id=job_id))
    typer.echo(messages.removed_job.format(job_id=job_id))


@cron_app.command("pause")
def cron_pause(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to pause.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Pause one scheduled job in a session without deleting it."""
    messages = _load_cli_cron_messages(config)
    service = _get_cron_service(session)
    if not service.pause_job(job_id):
        _handle_cron_error(messages.job_not_found_or_paused.format(job_id=job_id))
    typer.echo(messages.paused_job.format(job_id=job_id))


@cron_app.command("enable")
def cron_enable(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to re-enable.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Re-enable a paused scheduled job in a session."""
    messages = _load_cli_cron_messages(config)
    service = _get_cron_service(session)
    if not service.enable_job(job_id):
        _handle_cron_error(messages.job_not_found_or_enabled.format(job_id=job_id))
    typer.echo(messages.enabled_job.format(job_id=job_id))


@cron_app.command("run")
def cron_run(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to execute immediately.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Run one scheduled job immediately in a session."""
    messages = _load_cli_cron_messages(config)
    service = _get_cron_service(session)
    if not asyncio.run(service.run_job(job_id)):
        _handle_cron_error(messages.job_not_found.format(job_id=job_id))
    typer.echo(messages.ran_job.format(job_id=job_id))


if __name__ == "__main__":
    app()
