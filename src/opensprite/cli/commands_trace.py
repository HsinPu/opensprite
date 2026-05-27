"""CLI helpers for inspecting persisted run traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from ..runs.schema import (
    serialize_diff_summary,
    serialize_file_change,
    serialize_run_artifacts,
    serialize_run_events,
    serialize_run_part,
    serialize_run_summary,
)
from .commands_chat import _json_for_stdout
from .commands_chat_smoke import DEFAULT_SESSIONS_DB_PATH, load_trace_readonly, summarize_trace


def _text(value: Any) -> str:
    return str(value or "").strip()


def _run_payload(trace: Any) -> dict[str, Any]:
    run = trace.run
    return {
        "run_id": run.run_id,
        "session_id": run.session_id,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "metadata": dict(run.metadata or {}),
    }


def trace_payload(trace: Any, *, full: bool = False) -> dict[str, Any]:
    """Return a script-friendly trace payload."""
    summary = serialize_run_summary(trace)
    compact = summarize_trace(trace)
    payload: dict[str, Any] = {
        "ok": True,
        "run": _run_payload(trace),
        "summary": summary,
        "trace": compact,
    }
    if full:
        payload.update(
            {
                "events": serialize_run_events(trace.events),
                "parts": [serialize_run_part(part) for part in trace.parts],
                "file_changes": [serialize_file_change(change) for change in trace.file_changes],
                "diff_summary": serialize_diff_summary(trace),
                "artifacts": serialize_run_artifacts(trace),
            }
        )
    return payload


def _render_text(payload: dict[str, Any]) -> None:
    trace = payload["trace"]
    summary = payload["summary"]
    run = payload["run"]
    tools = ", ".join(str(tool) for tool in trace.get("tools") or []) or "-"
    warnings = ", ".join(str(warning) for warning in summary.get("warnings") or []) or "-"
    typer.echo("OpenSprite Run Trace")
    typer.echo(f"Session: {run.get('session_id')}")
    typer.echo(f"Run: {run.get('run_id')} [{run.get('status')}]")
    typer.echo(
        "Counts: "
        f"events={trace.get('event_count', 0)} "
        f"parts={trace.get('part_count', 0)} "
        f"files={trace.get('file_change_count', 0)} "
        f"tools={trace.get('tool_count', 0)}"
    )
    typer.echo(f"Profile: {_text(trace.get('profile')) or '-'}")
    typer.echo(f"Contract: {_text(trace.get('contract')) or '-'}")
    typer.echo(f"Completion: {_text(trace.get('completion_status')) or '-'}")
    reason = _text(trace.get("completion_reason"))
    if reason:
        typer.echo(f"Reason: {reason}")
    typer.echo(f"Tools: {tools}")
    typer.echo(f"Warnings: {warnings}")


def trace_command(
    *,
    run_id: str,
    session_id: str,
    db_path: str | None,
    full: bool,
    json_output: bool,
) -> None:
    """Run the Typer-facing trace inspection command."""
    try:
        trace = load_trace_readonly(session_id, run_id, db_path=db_path)
    except Exception as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if trace is None:
        resolved = Path(db_path).expanduser() if db_path else DEFAULT_SESSIONS_DB_PATH
        typer.secho(f"Error: trace not found in {resolved}: session_id={session_id} run_id={run_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    payload = trace_payload(trace, full=full)
    if json_output:
        typer.echo(_json_for_stdout(payload))
        return
    _render_text(payload)
