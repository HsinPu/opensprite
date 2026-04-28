import asyncio
import json
import sqlite3

from opensprite.storage.base import StoredMessage, StoredWorkState
from opensprite.storage.sqlite import SQLiteStorage


def test_sqlite_storage_migrates_legacy_sessions_and_drops_table(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            messages TEXT DEFAULT '[]',
            consolidated_index INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL
        )
        """
    )
    legacy_messages = [
        {"role": "user", "content": "Please keep sqlite fts docs handy", "timestamp": 1.0},
        {
            "role": "tool",
            "content": json.dumps(
                {
                    "type": "web_search",
                    "query": "sqlite fts5",
                    "url": "",
                    "final_url": "",
                    "title": "",
                    "content": "",
                    "summary": "Search results for: sqlite fts5",
                    "provider": "duckduckgo",
                    "extractor": "search",
                    "status": None,
                    "content_type": "application/json",
                    "items": [
                        {
                            "title": "SQLite FTS5",
                            "url": "https://sqlite.org/fts5.html",
                            "content": "Official full text search docs",
                        }
                    ],
                }
            ),
            "timestamp": 2.0,
            "tool_name": "web_search",
        },
        {
            "role": "tool",
            "content": json.dumps(
                {
                    "type": "web_fetch",
                    "query": "https://sqlite.org/fts5.html",
                    "title": "SQLite FTS5",
                    "url": "https://sqlite.org/fts5.html",
                    "final_url": "https://sqlite.org/fts5.html",
                    "content": "SQLite FTS5 supports full text search in a single database.",
                    "summary": "SQLite FTS5",
                    "provider": "web_fetch",
                    "extractor": "trafilatura",
                    "status": 200,
                    "content_type": "text/html",
                    "truncated": False,
                    "items": [],
                }
            ),
            "timestamp": 3.0,
            "tool_name": "web_fetch",
        },
    ]
    conn.execute(
        "INSERT INTO sessions (session_id, messages, consolidated_index, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("chat-1", json.dumps(legacy_messages), 4, 1.0, 3.0),
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db_path)

    messages = asyncio.run(storage.get_messages("chat-1"))
    consolidated_index = asyncio.run(storage.get_consolidated_index("chat-1"))
    chats = asyncio.run(storage.get_all_sessions())

    assert [message.role for message in messages] == ["user", "tool", "tool"]
    assert [message.tool_name for message in messages] == [None, "web_search", "web_fetch"]
    assert consolidated_index == 4
    assert chats == ["chat-1"]

    conn = sqlite3.connect(str(db_path))
    table_names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    knowledge_count = conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM search_chunks").fetchone()[0]
    knowledge_rows = conn.execute(
        "SELECT source_type, provider, extractor, status, content_type, truncated, summary FROM knowledge_sources ORDER BY id ASC"
    ).fetchall()
    conn.close()

    assert "sessions" not in table_names
    assert {
        "chats",
        "chat_state",
        "messages",
        "runs",
        "run_events",
        "run_parts",
        "run_file_changes",
        "work_states",
        "knowledge_sources",
        "search_chunks",
        "search_chunks_fts",
    }.issubset(table_names)
    assert knowledge_count == 2
    assert chunk_count >= 5
    assert knowledge_rows[0] == (
        "web_search",
        "duckduckgo",
        "search",
        None,
        "application/json",
        0,
        "Official full text search docs",
    )
    assert knowledge_rows[1] == (
        "web_fetch",
        "web_fetch",
        "trafilatura",
        200,
        "text/html",
        0,
        "SQLite FTS5",
    )


def test_sqlite_storage_supports_count_and_slice_reads(tmp_path):
    db_path = tmp_path / "sessions.db"
    storage = SQLiteStorage(db_path)

    async def scenario():
        for index in range(5):
            await storage.add_message(
                "chat-1",
                StoredMessage(role="user", content=f"m{index}", timestamp=float(index + 1)),
            )

        count = await storage.get_message_count("chat-1")
        middle = await storage.get_messages_slice("chat-1", start_index=1, end_index=4)
        tail = await storage.get_messages_slice("chat-1", start_index=3)
        return count, middle, tail

    count, middle, tail = asyncio.run(scenario())

    assert count == 5
    assert [message.content for message in middle] == ["m1", "m2", "m3"]
    assert [message.content for message in tail] == ["m3", "m4"]


def test_sqlite_storage_persists_runs_and_events(tmp_path):
    db_path = tmp_path / "runs.db"
    storage = SQLiteStorage(db_path)

    async def scenario():
        created = await storage.create_run(
            "chat-1",
            "run-1",
            status="running",
            metadata={"channel": "web"},
            created_at=10.0,
        )
        event = await storage.add_run_event(
            "chat-1",
            "run-1",
            "run_started",
            payload={"status": "running"},
            created_at=11.0,
        )
        part = await storage.add_run_part(
            "chat-1",
            "run-1",
            "tool_call",
            content='{"action": "auto"}',
            tool_name="verify",
            metadata={"args": {"action": "auto"}},
            created_at=11.5,
        )
        file_change = await storage.add_run_file_change(
            "chat-1",
            "run-1",
            "write_file",
            "notes.txt",
            "add",
            before_sha256=None,
            after_sha256="abc123",
            before_content=None,
            after_content="hello\n",
            diff="--- /dev/null\n+++ b/notes.txt\n@@\n+hello",
            metadata={"diff_len": 42},
            created_at=11.75,
        )
        updated = await storage.update_run_status(
            "chat-1",
            "run-1",
            "completed",
            metadata={"executed_tool_calls": 0},
            finished_at=12.0,
        )
        latest = await storage.get_latest_run("chat-1")
        single_run = await storage.get_run("chat-1", "run-1")
        events = await storage.get_run_events("chat-1", "run-1")
        parts = await storage.get_run_parts("chat-1", "run-1")
        file_changes = await storage.get_run_file_changes("chat-1", "run-1")
        single_change = await storage.get_run_file_change("chat-1", "run-1", file_change.change_id)
        trace = await storage.get_run_trace("chat-1", "run-1")
        work_state = await storage.upsert_work_state(
            StoredWorkState(
                session_id="chat-1",
                objective="Finish the refactor",
                kind="refactor",
                status="active",
                steps=("1. inspect", "2. change", "3. verify"),
                constraints=("Keep the API stable",),
                done_criteria=("tests pass",),
                long_running=True,
                coding_task=True,
                expects_code_change=True,
                expects_verification=True,
                current_step="2. change",
                next_step="3. verify",
                completed_steps=("1. inspect",),
                pending_steps=("2. change", "3. verify"),
                blockers=(),
                verification_targets=("tests pass",),
                resume_hint="Resume at current step: 2. change",
                last_progress_signals=("file_changes",),
                file_change_count=1,
                touched_paths=("src/app.py",),
                verification_attempted=False,
                verification_passed=False,
                last_next_action="continue_verification",
                active_delegate_task_id="task_abc12345",
                active_delegate_prompt_type="implementer",
                metadata={"source": "test"},
                created_at=9.0,
                updated_at=13.0,
            )
        )
        loaded_work_state = await storage.get_work_state("chat-1")
        await storage.clear_work_state("chat-1")
        cleared_work_state = await storage.get_work_state("chat-1")
        chats = await storage.get_all_sessions()
        return created, event, part, file_change, updated, latest, single_run, events, parts, file_changes, single_change, trace, work_state, loaded_work_state, cleared_work_state, chats

    created, event, part, file_change, updated, latest, single_run, events, parts, file_changes, single_change, trace, work_state, loaded_work_state, cleared_work_state, chats = asyncio.run(scenario())

    assert created is not None
    assert created.status == "running"
    assert created.metadata == {"channel": "web"}
    assert event is not None
    assert event.event_type == "run_started"
    assert event.payload == {"status": "running"}
    assert part is not None
    assert part.part_type == "tool_call"
    assert part.tool_name == "verify"
    assert part.metadata == {"args": {"action": "auto"}}
    assert file_change is not None
    assert file_change.tool_name == "write_file"
    assert file_change.path == "notes.txt"
    assert file_change.action == "add"
    assert file_change.before_sha256 is None
    assert file_change.after_sha256 == "abc123"
    assert file_change.before_content is None
    assert file_change.after_content == "hello\n"
    assert updated is not None
    assert updated.status == "completed"
    assert updated.finished_at == 12.0
    assert updated.metadata == {"channel": "web", "executed_tool_calls": 0}
    assert latest is not None
    assert latest.run_id == "run-1"
    assert latest.status == "completed"
    assert single_run is not None
    assert single_run.run_id == "run-1"
    assert [entry.event_type for entry in events] == ["run_started"]
    assert [entry.part_type for entry in parts] == ["tool_call"]
    assert parts[0].content == '{"action": "auto"}'
    assert [entry.path for entry in file_changes] == ["notes.txt"]
    assert file_changes[0].diff.startswith("--- /dev/null")
    assert file_changes[0].metadata == {"diff_len": 42}
    assert file_changes[0].after_content == "hello\n"
    assert single_change is not None
    assert single_change.path == "notes.txt"
    assert trace is not None
    assert trace.run.run_id == "run-1"
    assert [entry.event_type for entry in trace.events] == ["run_started"]
    assert [entry.part_type for entry in trace.parts] == ["tool_call"]
    assert [entry.path for entry in trace.file_changes] == ["notes.txt"]
    assert work_state is not None
    assert work_state.objective == "Finish the refactor"
    assert work_state.active_delegate_task_id == "task_abc12345"
    assert loaded_work_state is not None
    assert loaded_work_state.constraints == ("Keep the API stable",)
    assert loaded_work_state.touched_paths == ("src/app.py",)
    assert loaded_work_state.active_delegate_prompt_type == "implementer"
    assert loaded_work_state.pending_steps == ("2. change", "3. verify")
    assert loaded_work_state.verification_targets == ("tests pass",)
    assert loaded_work_state.resume_hint == "Resume at current step: 2. change"
    assert loaded_work_state.metadata == {"source": "test"}
    assert cleared_work_state is None
    assert chats == ["chat-1"]
