import json
import sqlite3

from typer.testing import CliRunner

from opensprite.cli import commands
from opensprite.cli.commands_chat_smoke import (
    SmokeCase,
    check_trace,
    load_trace_readonly,
    resolve_external_chat_prefix,
    select_cases,
    summarize_trace,
)
from opensprite.cli.commands_trace import trace_payload
from opensprite.runs.events import (
    COMPLETION_GATE_EVALUATED_EVENT,
    TASK_CONTRACT_CREATED_EVENT,
    TOOL_RESULT_EVENT,
    TOOL_STARTED_EVENT,
)
from opensprite.storage.base import StoredRun, StoredRunEvent, StoredRunPart, StoredRunTrace


def test_summarize_trace_extracts_task_tools_and_completion():
    trace = StoredRunTrace(
        run=StoredRun(
            run_id="run-1",
            session_id="web:smoke",
            status="completed",
            created_at=1.0,
            updated_at=2.0,
        ),
        events=[
            StoredRunEvent(
                run_id="run-1",
                session_id="web:smoke",
                event_type=TASK_CONTRACT_CREATED_EVENT,
                payload={"task_type": "web_research"},
            ),
            StoredRunEvent(
                run_id="run-1",
                session_id="web:smoke",
                event_type=TOOL_STARTED_EVENT,
                payload={"tool_name": "web_research"},
            ),
            StoredRunEvent(
                run_id="run-1",
                session_id="web:smoke",
                event_type=TOOL_RESULT_EVENT,
                payload={"tool_name": "web_fetch", "ok": False},
            ),
            StoredRunEvent(
                run_id="run-1",
                session_id="web:smoke",
                event_type=COMPLETION_GATE_EVALUATED_EVENT,
                payload={"status": "complete", "reason": "done"},
            ),
        ],
    )

    summary = summarize_trace(trace)

    assert summary["run_id"] == "run-1"
    assert summary["run_status"] == "completed"
    assert summary["task_type"] == "web_research"
    assert summary["contract"] == "web_research"
    assert summary["tools"] == ["web_research"]
    assert summary["failed_tools"] == ["web_fetch"]
    assert summary["completion_status"] == "complete"
    assert summary["completion_reason"] == "done"


def test_load_trace_readonly_reads_sqlite_without_storage_initialization(tmp_path):
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE TABLE run_parts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                part_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tool_name TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE TABLE run_file_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                path TEXT NOT NULL,
                action TEXT NOT NULL,
                before_sha256 TEXT,
                after_sha256 TEXT,
                before_content TEXT,
                after_content TEXT,
                diff TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("run-1", "web:smoke", "completed", '{"channel":"web"}', 1.0, 2.0, 3.0),
        )
        conn.execute(
            "INSERT INTO run_events (run_id, session_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            ("run-1", "web:smoke", TOOL_STARTED_EVENT, '{"tool_name":"web_search"}', 1.5),
        )
        conn.execute(
            "INSERT INTO run_parts (run_id, session_id, part_type, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("run-1", "web:smoke", "assistant_message", "pong", '{"ok":true}', 2.5),
        )
        conn.execute(
            """
            INSERT INTO run_file_changes (
                run_id, session_id, tool_name, path, action, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-1", "web:smoke", "edit_file", "a.txt", "modify", '{"ok":true}', 2.7),
        )
        conn.commit()
    finally:
        conn.close()

    trace = load_trace_readonly("web:smoke", "run-1", db_path=db_path)

    assert trace is not None
    assert trace.run.status == "completed"
    assert trace.run.metadata == {"channel": "web"}
    assert trace.events[0].payload == {"tool_name": "web_search"}
    assert trace.parts[0].content == "pong"
    assert trace.file_changes[0].path == "a.txt"


def test_check_trace_flags_web_tool_mismatch():
    no_web_case = SmokeCase("direct", "請直接回答", expect_web_tools=False)

    failures = check_trace(no_web_case, {"tools": ["web_search"]})

    assert "unexpected web tool: web_search" in failures


def test_check_trace_requires_web_tool_for_web_case():
    web_case = SmokeCase("web", "請上網查", expect_web_tools=True)

    assert check_trace(web_case, {"tools": []}) == ["expected at least one web tool"]
    assert check_trace(web_case, {"tools": ["web_fetch"]}) == []


def test_check_trace_flags_incomplete_completion_gate():
    case = SmokeCase("web", "search", expect_web_tools=True)

    failures = check_trace(case, {"tools": ["web_research"], "completion_status": "incomplete"})

    assert failures == ["completion gate status was incomplete"]


def test_select_cases_rejects_unknown_case():
    try:
        select_cases(["missing"])
    except ValueError as exc:
        assert "Unknown smoke case(s): missing" in str(exc)
    else:
        raise AssertionError("select_cases should reject unknown ids")


def test_resolve_external_chat_prefix_uses_unique_default():
    assert resolve_external_chat_prefix(" fixed-prefix ") == "fixed-prefix"

    generated = resolve_external_chat_prefix()

    assert generated.startswith("cli-trace-smoke-")
    assert generated != "cli-trace-smoke"


def test_chat_smoke_command_outputs_json(monkeypatch):
    runner = CliRunner()

    async def fake_run_smoke_cases(*args, **kwargs):
        assert str(kwargs["external_chat_prefix"]).startswith("cli-trace-smoke-")
        assert kwargs["external_chat_prefix"] != "cli-trace-smoke"
        return {
            "ok": True,
            "external_chat_prefix": kwargs["external_chat_prefix"],
            "case_count": 1,
            "failed_count": 0,
            "elapsed_seconds": 0.1,
            "results": [
                {
                    "case": "pong",
                    "ok": True,
                    "prompt": "ping",
                    "session_id": "web:smoke",
                    "external_chat_id": "smoke",
                    "reply_preview": "pong",
                    "elapsed_seconds": 0.1,
                    "failures": [],
                    "trace": {"run_id": "run-1", "profile": "chat", "tools": []},
                }
            ],
        }

    monkeypatch.setattr(commands.commands_chat_smoke, "run_smoke_cases", fake_run_smoke_cases)

    result = runner.invoke(commands.app, ["chat-smoke", "--case", "pong", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["results"][0]["trace"]["profile"] == "chat"


def test_trace_payload_can_include_full_serialized_trace():
    trace = StoredRunTrace(
        run=StoredRun(
            run_id="run-1",
            session_id="web:smoke",
            status="completed",
            created_at=1.0,
            updated_at=2.0,
        ),
        events=[
            StoredRunEvent(
                run_id="run-1",
                session_id="web:smoke",
                event_type=COMPLETION_GATE_EVALUATED_EVENT,
                payload={"status": "complete", "reason": "done"},
            ),
        ],
        parts=[
            StoredRunPart(
                run_id="run-1",
                session_id="web:smoke",
                part_type="assistant_message",
                content="pong",
            )
        ],
    )

    payload = trace_payload(trace, full=True)

    assert payload["ok"] is True
    assert payload["run"]["run_id"] == "run-1"
    assert payload["trace"]["completion_status"] == "complete"
    assert payload["events"][0]["event_type"] == COMPLETION_GATE_EVALUATED_EVENT
    assert payload["parts"][0]["content"] == "pong"


def test_trace_command_outputs_json_from_readonly_db(tmp_path):
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE TABLE run_parts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                part_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tool_name TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE TABLE run_file_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                path TEXT NOT NULL,
                action TEXT NOT NULL,
                before_sha256 TEXT,
                after_sha256 TEXT,
                before_content TEXT,
                after_content TEXT,
                diff TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("run-1", "web:smoke", "completed", "{}", 1.0, 2.0, 3.0),
        )
        conn.execute(
            "INSERT INTO run_events (run_id, session_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            ("run-1", "web:smoke", COMPLETION_GATE_EVALUATED_EVENT, '{"status":"complete","reason":"done"}', 1.5),
        )
        conn.commit()
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        commands.app,
        ["trace", "run-1", "--session-id", "web:smoke", "--db-path", str(db_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["run"]["status"] == "completed"
    assert payload["trace"]["completion_status"] == "complete"
