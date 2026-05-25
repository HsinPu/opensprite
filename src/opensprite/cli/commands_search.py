"""Search command helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import typer


def format_search_timestamp(value: float | None) -> str:
    """Render an optional unix timestamp for search status output."""
    if not value:
        return "never"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def load_sqlite_search_store(config: str | None, *, resolve_config_path: Callable[[str | None], Path]):
    """Load the configured SQLite search store or fail with a clear message."""
    from ..config import Config
    from ..runtime import create_search_store
    from ..search.sqlite_store import SQLiteSearchStore

    loaded = Config.load(resolve_config_path(config))
    search_store = create_search_store(loaded)
    if search_store is None:
        raise ValueError("search.enabled=false; enable search first")
    if not isinstance(search_store, SQLiteSearchStore):
        raise ValueError("configured history search backend does not support status inspection")
    return loaded, search_store


def search_status_command(*, config: str | None, session_id: str | None, load_sqlite_search_store: Callable[[str | None], Any], handle_search_error: Callable[[Exception | str], None], format_presence: Callable[[bool], str]) -> None:
    try:
        loaded, search_store = load_sqlite_search_store(config)
        status = asyncio.run(search_store.get_status(session_id=session_id))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        handle_search_error(exc)

    scope = session_id or "all sessions"
    embedding = loaded.search.embedding
    typer.echo(f"Chat history search status for {scope}.")
    typer.echo(f"Storage DB: {Path(loaded.storage.path).expanduser()}")
    typer.echo(f"Sessions: {status['session_count']}")
    typer.echo(f"Messages: {status['message_count']}")
    typer.echo(f"Chunks: {status['chunk_count']}")
    typer.echo(
        "Embedding: "
        f"enabled={format_presence(bool(embedding.enabled))} "
        f"provider={embedding.provider} model={embedding.model or '<unset>'} "
        f"candidate_strategy={embedding.candidate_strategy} "
        f"vector_backend={embedding.vector_backend} "
        f"retry_failed_on_startup={format_presence(bool(embedding.retry_failed_on_startup))}"
    )
    typer.echo(
        "Embedding jobs: "
        f"total={status['embedding_total']} queued={status['queued']} pending={status['pending']} processing={status['processing']} completed={status['completed']} failed={status['failed']} missing={status['missing']} stale={status['stale']}"
    )
    typer.echo(f"Vector backend: requested={status['vector_backend_requested']} effective={status['vector_backend_effective']}")
    typer.echo(
        "Queue worker: "
        f"running={format_presence(bool(status['worker_running']))} "
        f"owner={status['worker_owner'] or '<none>'} "
        f"expires={format_search_timestamp(status['worker_expires_at'])}"
    )
    typer.echo(
        "Last queue run: "
        f"mode={status['last_run_mode'] or '<none>'} "
        f"started={format_search_timestamp(status['last_run_started_at'])} "
        f"finished={format_search_timestamp(status['last_run_finished_at'])} "
        f"refreshed={status['last_run_refreshed']} processed={status['last_run_processed']} failed={status['last_run_failed']}"
    )
