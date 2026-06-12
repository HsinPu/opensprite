"""CLI command helpers for one-shot chat turns."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from aiohttp import ClientError, ClientSession, WSMsgType
import typer

from ..channels.cli import CliAdapter, CliChatResult
from ..config import Config
from ..context.paths import get_session_workspace, get_tool_workspace
from ..agent.turn_input import CLI_VIA_WEB_TURN_SOURCE, TURN_SOURCE_METADATA_KEY
from ..runs.events import TOOL_STARTED_EVENT
from ..runs.lifecycle import RUN_CANCELLED_EVENT, RUN_FAILED_EVENT, RUN_STARTED_EVENT, TERMINAL_RUN_EVENTS
from ..runtime import (
    apply_network_environment,
    create_agent,
    start_search_queue_worker,
    stop_background_task,
)
from ..utils.log import setup_log


_SNAPSHOT_DIR_NAME = "repo"
_SNAPSHOT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    ".codegraph",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "htmlcov",
    "playwright-report",
    "test-results",
    "tmp",
}


def build_ws_url(
    gateway_url: str,
    *,
    ws_url: str | None = None,
    external_chat_id: str | None = None,
    access_token: str | None = None,
) -> str:
    """Build a WebSocket URL for a running Web gateway."""
    base = (ws_url or gateway_url or "http://127.0.0.1:8765").strip()
    parsed = urlparse(base)
    if parsed.scheme in {"http", "https"}:
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = parsed.path.rstrip("/") or ""
        if not path or path == "/":
            path = "/ws"
        parsed = parsed._replace(scheme=scheme, path=path)
    elif parsed.scheme not in {"ws", "wss"}:
        raise ValueError(f"Unsupported gateway URL scheme: {parsed.scheme or '<missing>'}")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if external_chat_id:
        query["external_chat_id"] = external_chat_id
    if access_token:
        query["access_token"] = access_token
    return urlunparse(parsed._replace(query=urlencode(query)))


def _event_payload(event: Any) -> dict[str, Any]:
    return {
        "run_id": event.run_id,
        "event_type": event.event_type,
        "status": event.payload.get("status") if isinstance(event.payload, dict) else None,
        "created_at": event.created_at,
    }


def _json_for_stdout(payload: dict[str, Any], *, encoding: str | None = None) -> str:
    """Render JSON safely for the active terminal encoding."""
    output_encoding = (encoding or getattr(sys.stdout, "encoding", None) or "").lower()
    ensure_ascii = output_encoding not in {"utf-8", "utf8"}
    return json.dumps(payload, ensure_ascii=ensure_ascii, indent=2)


def _echo_json(payload: dict[str, Any]) -> None:
    typer.echo(_json_for_stdout(payload))


def _snapshot_ignore(directory: str, names: list[str]) -> set[str]:
    _ = directory
    ignored: set[str] = set()
    for name in names:
        if name in _SNAPSHOT_IGNORES or name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def snapshot_workspace_for_session(
    source: str | Path | None,
    *,
    session_id: str,
    config_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Copy a local source tree into the target session workspace for test chats."""
    if source is None:
        return None
    src = Path(source).expanduser().resolve(strict=True)
    if not src.is_dir():
        raise ValueError(f"workspace snapshot source is not a directory: {src}")

    app_home = Path(config_path).expanduser().resolve().parent if config_path is not None else Path.home() / ".opensprite"
    workspace_root = get_tool_workspace(app_home)
    session_workspace = get_session_workspace(session_id, workspace_root=workspace_root)
    dest = (session_workspace / _SNAPSHOT_DIR_NAME).resolve(strict=False)
    try:
        dest.relative_to(session_workspace.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("workspace snapshot destination escaped the session workspace") from exc

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=_snapshot_ignore)
    copied_files = sum(1 for item in dest.rglob("*") if item.is_file())
    return {
        "source": str(src),
        "path": _SNAPSHOT_DIR_NAME,
        "session_workspace": str(session_workspace),
        "files": copied_files,
    }


def _web_chat_payload(
    *,
    ok: bool,
    gateway_url: str,
    socket_url: str,
    resolved_session_id: str,
    resolved_external_chat_id: str,
    run_id: str | None,
    run_status: str,
    reply_text: str,
    run_events: list[dict[str, Any]],
    started: float,
    error: str = "",
    error_type: str = "",
    workspace_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_call_count = sum(1 for event in run_events if event.get("event_type") == TOOL_STARTED_EVENT)
    payload: dict[str, Any] = {
        "ok": ok,
        "mode": "web",
        "gateway_url": gateway_url,
        "ws_url": socket_url,
        "session_id": resolved_session_id,
        "external_chat_id": resolved_external_chat_id,
        "run_id": run_id,
        "run_status": run_status,
        "reply": reply_text,
        "run_event_count": len(run_events),
        "tool_call_count": tool_call_count,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "recent_events": [
            {
                "run_id": event.get("run_id"),
                "event_type": event.get("event_type"),
                "status": event.get("status"),
                "created_at": event.get("created_at"),
            }
            for event in run_events[-8:]
        ],
    }
    if error:
        payload["error"] = error
    if error_type:
        payload["error_type"] = error_type
    if workspace_snapshot is not None:
        payload["workspace_snapshot"] = workspace_snapshot
    return payload


async def run_web_chat(
    message: str,
    *,
    gateway_url: str = "http://127.0.0.1:8765",
    ws_url: str | None = None,
    external_chat_id: str = "cli-smoke",
    session_id: str | None = None,
    sender_name: str = "OpenSprite CLI",
    access_token: str | None = None,
    config_path: str | Path | None = None,
    workspace_snapshot: str | Path | None = None,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Send one message through an already-running Web gateway."""
    if not message.strip():
        raise ValueError("message is required")
    socket_url = build_ws_url(gateway_url, ws_url=ws_url, external_chat_id=external_chat_id, access_token=access_token)
    started = time.monotonic()
    deadline = started + timeout_seconds
    run_id: str | None = None
    run_status = ""
    run_events: list[dict[str, Any]] = []
    reply_text = ""
    terminal_run_seen = False
    terminal_reply_deadline: float | None = None
    resolved_session_id = session_id or ""
    resolved_external_chat_id = external_chat_id
    snapshot_metadata: dict[str, Any] | None = None

    try:
        async with ClientSession() as session:
            async with session.ws_connect(socket_url, timeout=timeout_seconds) as ws:
                first = await ws.receive_json(timeout=min(timeout_seconds, 10.0))
                if not isinstance(first, dict) or first.get("type") != "session":
                    raise RuntimeError(f"Expected session frame, got: {first}")
                resolved_session_id = session_id or str(first.get("session_id") or "")
                resolved_external_chat_id = external_chat_id or str(first.get("external_chat_id") or "")
                snapshot_metadata = snapshot_workspace_for_session(
                    workspace_snapshot,
                    session_id=resolved_session_id,
                    config_path=config_path,
                )

                outgoing: dict[str, Any] = {
                    "external_chat_id": resolved_external_chat_id,
                    "sender_name": sender_name,
                    "text": message,
                    "metadata": {
                        TURN_SOURCE_METADATA_KEY: CLI_VIA_WEB_TURN_SOURCE,
                        "gateway_url": gateway_url,
                        "ws_url": socket_url,
                    },
                }
                if snapshot_metadata is not None:
                    outgoing["metadata"]["workspace_snapshot"] = snapshot_metadata
                if resolved_session_id:
                    outgoing["session_id"] = resolved_session_id
                await ws.send_json(outgoing)

                while True:
                    now = time.monotonic()
                    effective_deadline = deadline
                    if terminal_reply_deadline is not None:
                        effective_deadline = min(effective_deadline, terminal_reply_deadline)
                    remaining = effective_deadline - now
                    if remaining <= 0:
                        if terminal_run_seen and reply_text:
                            break
                        raise TimeoutError(f"Timed out waiting for chat reply after {timeout_seconds:g}s")
                    msg = await ws.receive(timeout=remaining)
                    if msg.type == WSMsgType.TEXT:
                        frame = json.loads(msg.data)
                    elif msg.type in {WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.ERROR}:
                        if terminal_run_seen and reply_text:
                            break
                        raise RuntimeError("WebSocket closed before a reply was received")
                    else:
                        continue
                    if not isinstance(frame, dict):
                        continue
                    frame_session_id = str(frame.get("session_id") or "")
                    if frame.get("type") == "error":
                        raise RuntimeError(str(frame.get("error") or "gateway returned an error"))
                    if frame_session_id and resolved_session_id and frame_session_id != resolved_session_id:
                        continue
                    if frame.get("type") == "run_event":
                        event_type = str(frame.get("event_type") or "")
                        frame_run_id = str(frame.get("run_id") or "") or None
                        if run_id is None:
                            if event_type not in {RUN_STARTED_EVENT, RUN_FAILED_EVENT, RUN_CANCELLED_EVENT}:
                                continue
                            run_id = frame_run_id
                        elif frame_run_id != run_id:
                            continue
                        run_events.append(frame)
                        if frame.get("event_type") in TERMINAL_RUN_EVENTS:
                            terminal_run_seen = True
                            run_status = str(frame.get("status") or "")
                            if not run_status and isinstance(frame.get("payload"), dict):
                                run_status = str(frame["payload"].get("status") or "")
                            if reply_text:
                                terminal_reply_deadline = time.monotonic() + 1.0
                    elif frame.get("type") == "message":
                        frame_run_id = str(frame.get("run_id") or "") or None
                        if frame_run_id and run_id and frame_run_id != run_id:
                            continue
                        if run_id is None:
                            continue
                        reply_text = str(frame.get("text") or "")
                        if terminal_run_seen:
                            break
    except (ClientError, asyncio.TimeoutError, TimeoutError, OSError, RuntimeError) as exc:
        if reply_text and run_id and isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return _web_chat_payload(
                ok=True,
                gateway_url=gateway_url,
                socket_url=socket_url,
                resolved_session_id=resolved_session_id,
                resolved_external_chat_id=resolved_external_chat_id,
                run_id=run_id,
                run_status=run_status,
                reply_text=reply_text,
                run_events=run_events,
                started=started,
                workspace_snapshot=snapshot_metadata,
            )
        return _web_chat_payload(
            ok=False,
            gateway_url=gateway_url,
            socket_url=socket_url,
            resolved_session_id=resolved_session_id,
            resolved_external_chat_id=resolved_external_chat_id,
            run_id=run_id,
            run_status=run_status,
            reply_text=reply_text,
            run_events=run_events,
            started=started,
            error=f"Web gateway chat failed: {exc}",
            error_type=exc.__class__.__name__,
            workspace_snapshot=snapshot_metadata,
        )

    terminal_status = run_status.strip().lower()
    ok = terminal_status not in {"failed", "incomplete", "needs_verification", "cancelled", "canceled", "error"}
    return _web_chat_payload(
        ok=ok,
        gateway_url=gateway_url,
        socket_url=socket_url,
        resolved_session_id=resolved_session_id,
        resolved_external_chat_id=resolved_external_chat_id,
        run_id=run_id,
        run_status=run_status,
        reply_text=reply_text,
        run_events=run_events,
        started=started,
        error=("Web gateway run ended with status: " + run_status) if not ok else "",
        error_type="RunStatusError" if not ok else "",
        workspace_snapshot=snapshot_metadata,
    )


async def run_cli_chat(
    message: str,
    *,
    config_path: str | Path | None = None,
    external_chat_id: str = "default",
    session_id: str | None = None,
    sender_name: str = "OpenSprite CLI",
    workspace_snapshot: str | Path | None = None,
    timeout_seconds: float = 300.0,
) -> tuple[CliChatResult, dict[str, Any]]:
    """Run a one-shot local CLI channel turn through the normal agent queue."""
    if not message.strip():
        raise ValueError("message is required")

    early_app_home = Path(config_path).expanduser().resolve().parent if config_path is not None else None
    setup_log(app_home=early_app_home)
    config = Config.load(config_path)
    app_home = config.source_path.parent if config.source_path is not None else early_app_home
    setup_log(config.log, app_home=app_home)
    apply_network_environment(config)

    started = time.monotonic()
    agent, mq, cron_manager = await create_agent(config)
    search_queue_worker = start_search_queue_worker(getattr(agent, "search_store", None))
    processor = asyncio.create_task(mq.process_queue())
    trace_summary: dict[str, Any] = {}

    try:
        await agent.connect_mcp()
        await cron_manager.start()
        adapter = CliAdapter(
            mq,
            external_chat_id=external_chat_id,
            session_id=session_id,
            sender_name=sender_name,
        )
        snapshot_metadata = snapshot_workspace_for_session(
            workspace_snapshot,
            session_id=adapter.session_id,
            config_path=config_path,
        )
        result = await adapter.run_once(
            message,
            timeout=timeout_seconds,
            metadata={"workspace_snapshot": snapshot_metadata} if snapshot_metadata is not None else None,
        )
        if result.run_id:
            trace = await agent.storage.get_run_trace(result.response.session_id or adapter.session_id, result.run_id)
            if trace is not None:
                trace_summary = {
                    "event_count": len(trace.events),
                    "part_count": len(trace.parts),
                    "file_change_count": len(trace.file_changes),
                }
        if snapshot_metadata is not None:
            trace_summary["workspace_snapshot"] = snapshot_metadata
        trace_summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result, trace_summary
    finally:
        await mq.stop()
        await stop_background_task(processor, name="message queue processor")
        await stop_background_task(search_queue_worker, name="search embedding queue worker")
        await cron_manager.stop()
        await agent.close_background_maintenance()
        await agent.close_background_skill_reviews()
        close_background_processes = getattr(agent, "close_background_processes", None)
        if close_background_processes is not None:
            await close_background_processes()
        await agent.close_mcp()


def result_payload(result: CliChatResult, trace_summary: dict[str, Any]) -> dict[str, Any]:
    """Convert a chat result into stable JSON for scripts."""
    return {
        "ok": not bool(result.error),
        "mode": "cli",
        "session_id": result.response.session_id,
        "external_chat_id": result.response.external_chat_id,
        "run_id": result.run_id,
        "run_status": result.run_status,
        "reply": result.response.text,
        "run_event_count": len(result.run_events),
        "tool_call_count": result.tool_call_count,
        "trace": trace_summary,
        "recent_events": [_event_payload(event) for event in result.run_events[-8:]],
    }


def _render_text(result: CliChatResult, trace_summary: dict[str, Any]) -> None:
    typer.echo("OpenSprite CLI Chat")
    typer.echo(f"Session: {result.response.session_id}")
    if result.run_id:
        status = f" [{result.run_status}]" if result.run_status else ""
        typer.echo(f"Run: {result.run_id}{status}")
    typer.echo(f"Events: run={len(result.run_events)} tools={result.tool_call_count}")
    if trace_summary:
        typer.echo(
            "Trace: "
            f"events={trace_summary.get('event_count', 0)} "
            f"parts={trace_summary.get('part_count', 0)} "
            f"files={trace_summary.get('file_change_count', 0)}"
        )
    elapsed = trace_summary.get("elapsed_seconds")
    if elapsed is not None:
        typer.echo(f"Elapsed: {elapsed}s")
    typer.echo("")
    typer.echo(result.response.text)


def _render_web_payload(payload: dict[str, Any]) -> None:
    typer.echo("OpenSprite Web Chat Smoke")
    typer.echo(f"Gateway: {payload.get('gateway_url')}")
    typer.echo(f"Session: {payload.get('session_id')}")
    if payload.get("run_id"):
        status = f" [{payload.get('run_status')}]" if payload.get("run_status") else ""
        typer.echo(f"Run: {payload.get('run_id')}{status}")
    typer.echo(f"Events: run={payload.get('run_event_count', 0)} tools={payload.get('tool_call_count', 0)}")
    typer.echo(f"Elapsed: {payload.get('elapsed_seconds')}s")
    typer.echo("")
    typer.echo(str(payload.get("reply") or ""))


def chat_command(
    *,
    message: str,
    config: str | None,
    external_chat_id: str,
    session_id: str | None,
    sender_name: str,
    timeout_seconds: float,
    json_output: bool,
    via_web: bool,
    gateway_url: str,
    ws_url: str | None,
    access_token: str | None,
    workspace_snapshot: str | None,
) -> None:
    """Run the Typer-facing one-shot chat command."""
    try:
        if via_web:
            payload = asyncio.run(
                run_web_chat(
                    message,
                    gateway_url=gateway_url,
                    ws_url=ws_url,
                    external_chat_id=external_chat_id,
                    session_id=session_id,
                    sender_name=sender_name,
                    access_token=access_token,
                    config_path=config,
                    workspace_snapshot=workspace_snapshot,
                    timeout_seconds=timeout_seconds,
                )
            )
            if json_output:
                _echo_json(payload)
            else:
                _render_web_payload(payload)
            if not payload.get("ok", False):
                raise typer.Exit(code=1)
            return

        result, trace_summary = asyncio.run(
            run_cli_chat(
                message,
                config_path=config,
                external_chat_id=external_chat_id,
                session_id=session_id,
                sender_name=sender_name,
                workspace_snapshot=workspace_snapshot,
                timeout_seconds=timeout_seconds,
            )
        )
    except Exception as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(result_payload(result, trace_summary))
        return
    _render_text(result, trace_summary)
