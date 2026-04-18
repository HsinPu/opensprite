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
from ..context.paths import get_chat_workspace, get_tool_workspace
from ..cron import CronSchedule, CronService
from ..runtime import gateway as run_gateway
from ..search.base import SearchHit
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
        f"(history_top_k={search['history_top_k']}, knowledge_top_k={search['knowledge_top_k']})"
    )
    typer.echo(
        "Channels: " + (", ".join(enabled_channels) if enabled_channels else "none enabled")
    )


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


@search_app.command("rebuild")
def search_rebuild(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    chat_id: str | None = typer.Option(
        None,
        "--chat-id",
        help="Optional chat id to rebuild instead of rebuilding the full index.",
    ),
) -> None:
    """Rebuild the SQLite search index from stored messages."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        result = asyncio.run(search_store.rebuild_index(chat_id=chat_id))
        status = asyncio.run(search_store.wait_for_embedding_idle())
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = chat_id or "all chats"
    typer.echo(f"Rebuilt search index for {scope}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(f"Chats: {result['chat_count']}")
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
    chat_id: str | None = typer.Option(
        None,
        "--chat-id",
        help="Optional chat id to inspect instead of the full search index.",
    ),
) -> None:
    """Show SQLite search index and embedding status."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        status = asyncio.run(search_store.get_status(chat_id=chat_id))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = chat_id or "all chats"
    embedding = loaded.search.embedding
    typer.echo(f"Search status for {scope}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(f"Chats: {status['chat_count']}")
    typer.echo(f"Messages: {status['message_count']}")
    typer.echo(f"Knowledge sources: {status['knowledge_count']}")
    typer.echo(f"Chunks: {status['chunk_count']}")
    typer.echo(
        "Embedding: "
        f"enabled={_format_presence(bool(embedding.enabled))} "
        f"provider={embedding.provider} model={embedding.model or '<unset>'} "
        f"retry_failed_on_startup={_format_presence(bool(embedding.retry_failed_on_startup))}"
    )
    typer.echo(
        "Embedding jobs: "
        f"total={status['embedding_total']} queued={status['queued']} pending={status['pending']} processing={status['processing']} completed={status['completed']} failed={status['failed']} missing={status['missing']} stale={status['stale']}"
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
    chat_id: str | None = typer.Option(
        None,
        "--chat-id",
        help="Optional chat id to refresh instead of refreshing all embeddings.",
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
        status = asyncio.run(search_store.refresh_embeddings(chat_id=chat_id, force=force, wait=True))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = chat_id or "all chats"
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
    chat_id: str = typer.Option(..., "--chat-id", help="Chat id to benchmark against."),
    kind: str = typer.Option("knowledge", "--kind", help="Benchmark `history` or `knowledge` search."),
    strategy: str = typer.Option("both", "--strategy", help="Benchmark `fts`, `vector`, or `both`."),
    limit: int = typer.Option(5, "--limit", help="Maximum hits to show per strategy."),
    repeat: int = typer.Option(3, "--repeat", help="How many runs to execute per strategy."),
    json_output: bool = typer.Option(False, "--json", help="Emit benchmark output as JSON."),
    source_type: str | None = typer.Option(None, "--source-type", help="Knowledge source filter for knowledge benchmarks."),
    provider: str | None = typer.Option(None, "--provider", help="Knowledge provider filter for knowledge benchmarks."),
    extractor: str | None = typer.Option(None, "--extractor", help="Knowledge extractor filter for knowledge benchmarks."),
    status: int | None = typer.Option(None, "--status", help="Knowledge status filter for knowledge benchmarks."),
    content_type: str | None = typer.Option(None, "--content-type", help="Knowledge content type filter for knowledge benchmarks."),
    truncated: bool | None = typer.Option(None, "--truncated", help="Knowledge truncation filter for knowledge benchmarks."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
) -> None:
    """Benchmark FTS and vector candidate strategies for one chat query."""
    if kind not in {"history", "knowledge"}:
        _handle_search_error("--kind must be 'history' or 'knowledge'")
    if strategy not in {"fts", "vector", "both"}:
        _handle_search_error("--strategy must be 'fts', 'vector', or 'both'")
    if repeat < 1:
        _handle_search_error("--repeat must be greater than 0")

    try:
        from ..config import Config

        loaded = Config.load(_resolve_config_path(config))
        if not loaded.search.enabled:
            raise ValueError("search.enabled=false; enable search first")
        strategies = ["fts", "vector"] if strategy == "both" else [strategy]
        if strategy == "vector" and not loaded.search.embedding.enabled:
            raise ValueError("search.embedding.enabled=false; vector benchmarks require embeddings")
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

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

    if "vector" in strategies and not loaded.search.embedding.enabled:
        if not json_output:
            typer.echo("Vector benchmark skipped because embeddings are disabled.")
        strategies = [item for item in strategies if item != "vector"]

    benchmark_payload: dict[str, object] = {
        "chat_id": chat_id,
        "kind": kind,
        "query": query,
        "repeat": repeat,
        "filters": filters,
        "strategies": [],
        "comparison": {},
    }

    if not json_output:
        typer.echo(f"Search benchmark for {chat_id} ({kind}).")
        typer.echo(f"Query: {query}")
        if kind == "knowledge":
            typer.echo(f"Filters: {', '.join(filters) if filters else '<none>'}")

    for candidate_strategy in strategies:
        try:
            search_store = _build_sqlite_search_store(loaded, candidate_strategy=candidate_strategy)
            elapsed_runs: list[float] = []
            hits = []
            for _ in range(repeat):
                elapsed_ms, hits = _benchmark_one_strategy(
                    search_store,
                    kind=kind,
                    chat_id=chat_id,
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
        benchmark_payload["strategies"].append(
            {
                "strategy": candidate_strategy,
                "summary": summary,
                "hit_count": len(hits),
                "hits": _serialize_benchmark_hits(hits, limit=limit),
                "_raw_hits": hits,
            }
        )

        if json_output:
            continue

        typer.echo("")
        typer.echo(f"Strategy: {candidate_strategy}")
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
            typer.echo("")
            typer.echo(
                "Comparison: "
                f"overlap={comparison['overlap_count']} ({comparison['overlap_ratio']:.2%}) "
                f"top_hit_same={_format_presence(bool(comparison['top_hit_same']))} "
                f"{comparison['strategies'][0]}_only={comparison['first_only_count']} "
                f"{comparison['strategies'][1]}_only={comparison['second_only_count']}"
            )
            if comparison["first_top"] or comparison["second_top"]:
                typer.echo(
                    "Top hits: "
                    f"{comparison['strategies'][0]}={comparison['first_top'] or '<none>'} | "
                    f"{comparison['strategies'][1]}={comparison['second_top'] or '<none>'}"
                )

    if json_output:
        strategies_payload = []
        for item in benchmark_payload["strategies"]:
            serialized = dict(item)
            serialized.pop("_raw_hits", None)
            strategies_payload.append(serialized)
        benchmark_payload["strategies"] = strategies_payload
        typer.echo(json.dumps(benchmark_payload, ensure_ascii=False, indent=2))


@search_app.command("retry-embeddings")
def search_retry_embeddings(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    chat_id: str | None = typer.Option(
        None,
        "--chat-id",
        help="Optional chat id to retry instead of retrying all failed embeddings.",
    ),
) -> None:
    """Retry failed embedding jobs and wait for the queue to go idle."""
    try:
        loaded, search_store = _load_sqlite_search_store(config)
        if not loaded.search.embedding.enabled:
            raise ValueError("search.embedding.enabled=false; enable embeddings first")
        status = asyncio.run(search_store.retry_failed_embeddings(chat_id=chat_id, wait=True))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _handle_search_error(exc)

    scope = chat_id or "all chats"
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
    """Compare multiple benchmark strategy outputs by overlap and top-hit agreement."""
    if len(results) < 2:
        return {}

    first = results[0]
    second = results[1]
    first_hits = first.get("_raw_hits", [])
    second_hits = second.get("_raw_hits", [])
    first_keys = {_benchmark_hit_identity(hit) for hit in first_hits}
    second_keys = {_benchmark_hit_identity(hit) for hit in second_hits}
    overlap = first_keys & second_keys
    union = first_keys | second_keys
    first_top = _benchmark_hit_identity(first_hits[0]) if first_hits else None
    second_top = _benchmark_hit_identity(second_hits[0]) if second_hits else None

    return {
        "strategies": [first.get("strategy"), second.get("strategy")],
        "overlap_count": len(overlap),
        "overlap_ratio": (len(overlap) / len(union)) if union else 1.0,
        "top_hit_same": bool(first_top and second_top and first_top == second_top),
        "first_only_count": len(first_keys - second_keys),
        "second_only_count": len(second_keys - first_keys),
        "first_top": first_top,
        "second_top": second_top,
    }


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


def _build_sqlite_search_store(loaded, *, candidate_strategy: str | None = None):
    """Build a SQLite search store from an already loaded config."""
    from ..runtime import create_search_embedding_provider
    from ..search.sqlite_store import SQLiteSearchStore

    embedding_provider = create_search_embedding_provider(loaded)
    strategy = candidate_strategy or loaded.search.embedding.candidate_strategy
    return SQLiteSearchStore(
        path=loaded.storage.path,
        history_top_k=loaded.search.history_top_k,
        knowledge_top_k=loaded.search.knowledge_top_k,
        embedding_provider=embedding_provider,
        hybrid_candidate_count=loaded.search.embedding.candidate_count,
        embedding_candidate_strategy=strategy,
        vector_candidate_count=loaded.search.embedding.vector_candidate_count,
        retry_failed_on_startup=loaded.search.embedding.retry_failed_on_startup,
    )


def _benchmark_one_strategy(
    search_store,
    *,
    kind: str,
    chat_id: str,
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
        hits = asyncio.run(search_store.search_history(chat_id, query, limit=limit))
    else:
        hits = asyncio.run(
            search_store.search_knowledge(
                chat_id,
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


def _get_cron_service(session: str) -> CronService:
    """Open the cron service store for a session without starting a timer loop."""
    workspace = get_chat_workspace(session, workspace_root=_resolve_workspace_root())
    return CronService(workspace / "cron" / "jobs.json", session_chat_id=session)


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
    from zoneinfo import ZoneInfo

    dt = datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz_name))
    return f"{dt.isoformat()} ({tz_name})"


def _format_cron_timing(schedule: CronSchedule, default_timezone: str = "UTC") -> str:
    """Format a cron schedule in the same style as the runtime tool."""
    if schedule.kind == "cron":
        tz = f" ({schedule.tz})" if schedule.tz else ""
        return f"cron: {schedule.expr}{tz}"
    if schedule.kind == "every" and schedule.every_ms:
        if schedule.every_ms % 3_600_000 == 0:
            return f"every {schedule.every_ms // 3_600_000}h"
        if schedule.every_ms % 60_000 == 0:
            return f"every {schedule.every_ms // 60_000}m"
        if schedule.every_ms % 1000 == 0:
            return f"every {schedule.every_ms // 1000}s"
        return f"every {schedule.every_ms}ms"
    if schedule.kind == "at" and schedule.at_ms:
        return f"at {_format_cron_timestamp(schedule.at_ms, schedule.tz or default_timezone)}"
    return schedule.kind


def _render_cron_jobs(service: CronService, default_timezone: str = "UTC") -> str:
    """Render the stored jobs for CLI list output."""
    jobs = service.list_jobs(include_disabled=True)
    if not jobs:
        return "No scheduled jobs."

    lines = []
    for job in jobs:
        line = f"- {job.name} (id: {job.id}, {_format_cron_timing(job.schedule, default_timezone)})"
        if job.state.next_run_at_ms:
            line += f"\n  Next run: {_format_cron_timestamp(job.state.next_run_at_ms, job.schedule.tz or default_timezone)}"
        lines.append(line)
    return "Scheduled jobs:\n" + "\n".join(lines)


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
        help="Session chat id, for example telegram:user-a.",
    ),
) -> None:
    """List scheduled jobs for one session."""
    service = _get_cron_service(session)
    typer.echo(_render_cron_jobs(service))


@cron_app.command("add")
def cron_add(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
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
) -> None:
    """Add a scheduled job to one session."""
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

    typer.echo(f"Created job '{job.name}' (id: {job.id})")


@cron_app.command("remove")
def cron_remove(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to remove.",
    ),
) -> None:
    """Remove one scheduled job from a session."""
    service = _get_cron_service(session)
    if not service.remove_job(job_id):
        _handle_cron_error(f"job {job_id} not found")
    typer.echo(f"Removed job {job_id}")


@cron_app.command("pause")
def cron_pause(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to pause.",
    ),
) -> None:
    """Pause one scheduled job in a session without deleting it."""
    service = _get_cron_service(session)
    if not service.pause_job(job_id):
        _handle_cron_error(f"job {job_id} not found or already paused")
    typer.echo(f"Paused job {job_id}")


@cron_app.command("enable")
def cron_enable(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to re-enable.",
    ),
) -> None:
    """Re-enable a paused scheduled job in a session."""
    service = _get_cron_service(session)
    if not service.enable_job(job_id):
        _handle_cron_error(f"job {job_id} not found or already enabled")
    typer.echo(f"Enabled job {job_id}")


@cron_app.command("run")
def cron_run(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to execute immediately.",
    ),
) -> None:
    """Run one scheduled job immediately in a session."""
    service = _get_cron_service(session)
    if not asyncio.run(service.run_job(job_id)):
        _handle_cron_error(f"job {job_id} not found")
    typer.echo(f"Ran job {job_id}")


if __name__ == "__main__":
    app()
