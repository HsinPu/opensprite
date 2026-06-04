"""CLI smoke runner for end-to-end Web chat traces."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

import typer

from ..runs.events import HARNESS_PROFILE_SELECTED_EVENT, TASK_CONTRACT_CREATED_EVENT, TOOL_RESULT_EVENT, TOOL_STARTED_EVENT
from ..storage.base import StoredRun, StoredRunEvent, StoredRunFileChange, StoredRunPart, StoredRunTrace
from .commands_chat import _json_for_stdout, run_web_chat


WEB_TOOL_NAMES = {"web_search", "web_fetch", "web_research"}


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    prompt: str
    expect_web_tools: bool | None = None


DEFAULT_SMOKE_CASES: tuple[SmokeCase, ...] = (
    SmokeCase("pong", "請只回答 pong。", expect_web_tools=False),
    SmokeCase("summary-no-web", "請用三句話介紹 OpenSprite，不要讀檔也不要上網。", expect_web_tools=False),
    SmokeCase("math", "請計算 17 * 23 + 19，請用三個編號列出：算式、計算過程、最終答案。", expect_web_tools=False),
    SmokeCase("translate", "請把這句翻成英文：我正在測試 CLI 對話流程。", expect_web_tools=False),
    SmokeCase("format-list", "請把 apple、banana、cherry 改成三行編號清單。不要上網。", expect_web_tools=False),
    SmokeCase("direct-debug", "請幫我 debug Python ModuleNotFoundError 的常見原因，不要讀檔、不要上網。", expect_web_tools=False),
    SmokeCase("trace-metric", "請用一個表格列出 CLI chat trace 最重要的三個欄位。不要讀檔、不要上網。", expect_web_tools=False),
    SmokeCase("web-search", "請務必使用 web_search 搜尋 OpenAI 2026 最新消息，回覆一個來源網址即可。", expect_web_tools=True),
    SmokeCase(
        "web-research",
        "請務必使用 web_research 搜尋 2026 AI agent tools market trends，整理兩點並列出來源網址。",
        expect_web_tools=True,
    ),
    SmokeCase(
        "current-source",
        "請上網查詢 2026 AI agent tools market trends，最多整理三個來源網址。",
        expect_web_tools=True,
    ),
)


SendWebChat = Callable[..., Awaitable[dict[str, Any]]]


DEFAULT_SESSIONS_DB_PATH = Path.home() / ".opensprite" / "data" / "sessions.db"


def _payload_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _tool_name_from_event(event_payload: dict[str, Any]) -> str:
    value = _payload_value(event_payload, "tool_name", "name", "tool")
    if isinstance(value, str):
        return value
    tool_call = event_payload.get("tool_call")
    if isinstance(tool_call, dict) and isinstance(tool_call.get("name"), str):
        return str(tool_call["name"])
    return ""


def _profile_from_payload(payload: dict[str, Any]) -> str:
    value = payload.get("name")
    if isinstance(value, str):
        return value
    effective = payload.get("effective")
    if isinstance(effective, dict) and isinstance(effective.get("name"), str):
        return str(effective["name"])
    profile = payload.get("harness_profile")
    if isinstance(profile, dict) and isinstance(profile.get("name"), str):
        return str(profile["name"])
    return ""


def _contract_type_from_payload(payload: dict[str, Any]) -> str:
    value = payload.get("task_type")
    return str(value) if isinstance(value, str) else ""


def _load_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _resolve_db_path(db_path: str | Path | None) -> Path:
    if db_path is None or str(db_path).strip() == "":
        return DEFAULT_SESSIONS_DB_PATH
    return Path(db_path).expanduser()


def _connect_readonly(db_path: str | Path | None, *, immutable: bool = False) -> sqlite3.Connection:
    """Open sessions.db for trace inspection without triggering migrations."""
    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        raise FileNotFoundError(f"Trace database not found: {resolved}")
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    conn = sqlite3.connect(f"file:{resolved}{suffix}", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _query_readonly(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    return conn.execute(query, params).fetchall()


def _load_trace_readonly(
    session_id: str,
    run_id: str,
    *,
    db_path: str | Path | None = None,
    immutable: bool = False,
) -> StoredRunTrace | None:
    conn = _connect_readonly(db_path, immutable=immutable)
    try:
        run_rows = _query_readonly(conn, "SELECT * FROM runs WHERE session_id = ? AND run_id = ?", (session_id, run_id))
        if not run_rows:
            return None
        run_row = run_rows[0]
        run = StoredRun(
            run_id=str(run_row["run_id"]),
            session_id=str(run_row["session_id"]),
            status=str(run_row["status"]),
            created_at=float(run_row["created_at"] or 0),
            updated_at=float(run_row["updated_at"] or 0),
            finished_at=float(run_row["finished_at"]) if run_row["finished_at"] is not None else None,
            metadata=_load_json_object(run_row["metadata_json"]),
        )
        event_rows = _query_readonly(
            conn,
            """
            SELECT id, run_id, session_id, event_type, payload_json, created_at
            FROM run_events
            WHERE session_id = ? AND run_id = ?
            ORDER BY id ASC
            """,
            (session_id, run_id),
        )
        part_rows = _query_readonly(
            conn,
            """
            SELECT id, run_id, session_id, part_type, content, tool_name, metadata_json, created_at
            FROM run_parts
            WHERE session_id = ? AND run_id = ?
            ORDER BY id ASC
            """,
            (session_id, run_id),
        )
        change_rows = _query_readonly(
            conn,
            """
            SELECT id, run_id, session_id, tool_name, path, action, before_sha256, after_sha256,
                   before_content, after_content, diff, metadata_json, created_at
            FROM run_file_changes
            WHERE session_id = ? AND run_id = ?
            ORDER BY id ASC
            """,
            (session_id, run_id),
        )
        return StoredRunTrace(
            run=run,
            events=[
                StoredRunEvent(
                    run_id=str(row["run_id"]),
                    session_id=str(row["session_id"]),
                    event_type=str(row["event_type"]),
                    payload=_load_json_object(row["payload_json"]),
                    created_at=float(row["created_at"] or 0),
                    event_id=int(row["id"]),
                )
                for row in event_rows
            ],
            parts=[
                StoredRunPart(
                    run_id=str(row["run_id"]),
                    session_id=str(row["session_id"]),
                    part_type=str(row["part_type"]),
                    content=str(row["content"] or ""),
                    tool_name=row["tool_name"],
                    metadata=_load_json_object(row["metadata_json"]),
                    created_at=float(row["created_at"] or 0),
                    part_id=int(row["id"]),
                )
                for row in part_rows
            ],
            file_changes=[
                StoredRunFileChange(
                    run_id=str(row["run_id"]),
                    session_id=str(row["session_id"]),
                    tool_name=str(row["tool_name"]),
                    path=str(row["path"]),
                    action=str(row["action"]),
                    before_sha256=row["before_sha256"],
                    after_sha256=row["after_sha256"],
                    before_content=row["before_content"],
                    after_content=row["after_content"],
                    diff=str(row["diff"] or ""),
                    metadata=_load_json_object(row["metadata_json"]),
                    created_at=float(row["created_at"] or 0),
                    change_id=int(row["id"]),
                )
                for row in change_rows
            ],
        )
    finally:
        conn.close()


def load_trace_readonly(session_id: str, run_id: str, *, db_path: str | Path | None = None) -> StoredRunTrace | None:
    """Load one persisted run trace without initializing or upgrading storage."""
    try:
        return _load_trace_readonly(session_id, run_id, db_path=db_path)
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).lower():
            raise
        return _load_trace_readonly(session_id, run_id, db_path=db_path, immutable=True)


def summarize_trace(trace: StoredRunTrace | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract the trace fields that are useful when comparing chat smoke runs."""
    fallback = fallback or {}
    if trace is None:
        return {
            "run_id": fallback.get("run_id"),
            "run_status": fallback.get("run_status") or "",
            "event_count": int(fallback.get("run_event_count") or 0),
            "profile": "",
            "contract": "",
            "completion_status": "",
            "completion_reason": "",
            "tool_count": int(fallback.get("tool_call_count") or 0),
            "tools": [],
            "failed_tool_count": 0,
            "failed_tools": [],
        }

    tools: list[str] = []
    failed_tools: list[str] = []
    profile = ""
    contract = ""
    completion_status = ""
    completion_reason = ""

    for event in trace.events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == TOOL_STARTED_EVENT:
            tool_name = _tool_name_from_event(payload)
            if tool_name:
                tools.append(tool_name)
        elif event.event_type == TOOL_RESULT_EVENT:
            ok = payload.get("ok")
            if ok is False:
                failed_tool = _tool_name_from_event(payload)
                if failed_tool:
                    failed_tools.append(failed_tool)
        elif event.event_type == HARNESS_PROFILE_SELECTED_EVENT:
            profile = _profile_from_payload(payload) or profile
        elif event.event_type == TASK_CONTRACT_CREATED_EVENT:
            contract = _contract_type_from_payload(payload) or contract
        elif event.event_type.startswith("completion_gate"):
            completion_status = str(payload.get("status") or completion_status)
            completion_reason = str(payload.get("reason") or completion_reason)

    run = trace.run
    return {
        "run_id": run.run_id,
        "run_status": run.status,
        "event_count": len(trace.events),
        "part_count": len(trace.parts),
        "file_change_count": len(trace.file_changes),
        "profile": profile,
        "contract": contract,
        "completion_status": completion_status,
        "completion_reason": completion_reason,
        "tool_count": len(tools),
        "tools": tools,
        "failed_tool_count": len(failed_tools),
        "failed_tools": failed_tools,
    }


def check_trace(case: SmokeCase, trace_summary: dict[str, Any]) -> list[str]:
    """Return strict smoke failures for one case."""
    failures: list[str] = []
    tools = {str(tool) for tool in trace_summary.get("tools") or []}
    web_tools = tools & WEB_TOOL_NAMES
    completion_status = str(trace_summary.get("completion_status") or "").strip().lower()
    if completion_status and completion_status != "complete":
        failures.append(f"completion gate status was {completion_status}")
    if case.expect_web_tools is True and not web_tools:
        failures.append("expected at least one web tool")
    elif case.expect_web_tools is False and web_tools:
        failures.append(f"unexpected web tool: {', '.join(sorted(web_tools))}")
    return failures


async def run_smoke_cases(
    cases: list[SmokeCase],
    *,
    gateway_url: str,
    ws_url: str | None,
    access_token: str | None,
    timeout_seconds: float,
    external_chat_prefix: str,
    db_path: str | Path | None,
    send_web_chat: SendWebChat = run_web_chat,
) -> dict[str, Any]:
    """Run all smoke cases through the Web gateway and inspect their stored traces."""
    started = time.monotonic()
    results: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        external_chat_id = f"{external_chat_prefix}-{index:02d}-{case.case_id}"
        payload = await send_web_chat(
            case.prompt,
            gateway_url=gateway_url,
            ws_url=ws_url,
            external_chat_id=external_chat_id,
            access_token=access_token,
            timeout_seconds=timeout_seconds,
        )
        session_id = str(payload.get("session_id") or f"web:{external_chat_id}")
        run_id = str(payload.get("run_id") or "")
        trace = load_trace_readonly(session_id, run_id, db_path=db_path) if run_id else None
        trace_summary = summarize_trace(trace, fallback=payload)
        failures = check_trace(case, trace_summary)
        payload_ok = bool(payload.get("ok", True))
        payload_error = str(payload.get("error") or "").strip()
        if not payload_ok:
            if trace_summary.get("run_status") == "completed" and trace_summary.get("completion_status") == "complete":
                payload_ok = True
            elif payload_error:
                failures.append(payload_error)
            else:
                failures.append("web chat payload reported failure")
        results.append(
            {
                "case": case.case_id,
                "ok": not failures and payload_ok,
                "prompt": case.prompt,
                "session_id": session_id,
                "external_chat_id": external_chat_id,
                "reply_preview": str(payload.get("reply") or "")[:240],
                "elapsed_seconds": payload.get("elapsed_seconds"),
                "payload_ok": bool(payload.get("ok", True)),
                "payload_error": payload_error,
                "failures": failures,
                "trace": trace_summary,
            }
        )

    failed = [result for result in results if not result["ok"]]
    return {
        "ok": not failed,
        "case_count": len(results),
        "failed_count": len(failed),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }


def select_cases(case_ids: list[str] | None = None) -> list[SmokeCase]:
    """Select smoke cases by id, preserving the default ordering."""
    if not case_ids:
        return list(DEFAULT_SMOKE_CASES)
    requested = set(case_ids)
    cases = [case for case in DEFAULT_SMOKE_CASES if case.case_id in requested]
    found = {case.case_id for case in cases}
    missing = sorted(requested - found)
    if missing:
        raise ValueError(f"Unknown smoke case(s): {', '.join(missing)}")
    return cases


def _render_text(payload: dict[str, Any]) -> None:
    typer.echo("OpenSprite CLI Chat Trace Smoke")
    typer.echo(f"Cases: {payload['case_count']} failed={payload['failed_count']} elapsed={payload['elapsed_seconds']}s")
    for result in payload["results"]:
        trace = result["trace"]
        status = "PASS" if result["ok"] else "FAIL"
        tools = ", ".join(trace.get("tools") or []) or "-"
        typer.echo(
            f"{status} {result['case']} run={trace.get('run_id') or '-'} "
            f"profile={trace.get('profile') or '-'} tools={tools}"
        )
        for failure in result.get("failures") or []:
            typer.echo(f"  - {failure}")


def chat_smoke_command(
    *,
    gateway_url: str,
    ws_url: str | None,
    access_token: str | None,
    timeout_seconds: float,
    external_chat_prefix: str,
    db_path: str | None,
    case_ids: list[str] | None,
    json_output: bool,
) -> None:
    """Run the Typer-facing chat trace smoke command."""
    try:
        cases = select_cases(case_ids)
        payload = asyncio.run(
            run_smoke_cases(
                cases,
                gateway_url=gateway_url,
                ws_url=ws_url,
                access_token=access_token,
                timeout_seconds=timeout_seconds,
                external_chat_prefix=external_chat_prefix,
                db_path=db_path,
            )
        )
    except Exception as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(_json_for_stdout(payload))
    else:
        _render_text(payload)
    if not payload["ok"]:
        raise typer.Exit(code=1)
