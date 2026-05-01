import asyncio
import subprocess
from types import SimpleNamespace

from opensprite.agent.run_hooks import RunHookService
from opensprite.agent.execution import LlmStepEvent
from opensprite.agent.run_trace import RUN_PART_CONTENT_MAX_CHARS, RunEventSink, RunTraceRecorder, truncate_run_part_content
from opensprite.agent.worktree import WorktreeSandboxInspector
from opensprite.bus import MessageBus
from opensprite.run_schema import (
    compact_run_events,
    serialize_file_change,
    serialize_run_artifacts,
    serialize_run_event,
    serialize_run_event_counts,
    serialize_run_events,
    serialize_run_part,
    serialize_run_summary,
)
from opensprite.storage import MemoryStorage, StoredWorkState


def test_truncate_run_part_content_bounds_large_payloads():
    long_content = "a" * (RUN_PART_CONTENT_MAX_CHARS + 1000) + "THE-END"

    content, metadata = truncate_run_part_content(long_content)

    assert len(content) <= RUN_PART_CONTENT_MAX_CHARS
    assert "run part content truncated" in content
    assert content.endswith("THE-END")
    assert metadata["content_truncated"] is True
    assert metadata["content_original_len"] == RUN_PART_CONTENT_MAX_CHARS + 1007


def test_run_trace_recorder_persists_bounded_parts():
    async def scenario():
        storage = MemoryStorage()
        recorder = RunTraceRecorder(storage=storage, message_bus_getter=lambda: None)
        await storage.create_run("web:browser-1", "run-1")
        await recorder.add_part(
            "web:browser-1",
            "run-1",
            "tool_result",
            content="a" * (RUN_PART_CONTENT_MAX_CHARS + 1000) + "THE-END",
            tool_name="dummy",
        )
        return await storage.get_run_parts("web:browser-1", "run-1")

    parts = asyncio.run(scenario())

    assert len(parts) == 1
    assert len(parts[0].content) <= RUN_PART_CONTENT_MAX_CHARS
    assert parts[0].content.endswith("THE-END")
    assert parts[0].metadata["content_truncated"] is True


def test_run_trace_recorder_persists_task_checklist_part():
    async def scenario():
        storage = MemoryStorage()
        recorder = RunTraceRecorder(storage=storage, message_bus_getter=lambda: None)
        await storage.create_run("web:browser-1", "run-1")
        todos = await recorder.record_task_checklist_part(
            "web:browser-1",
            "run-1",
            StoredWorkState(
                session_id="web:browser-1",
                objective="Ship task artifacts",
                kind="implementation",
                current_step="build artifact",
                next_step="verify artifact",
                completed_steps=("inspect state",),
                updated_at=123.0,
            ),
        )
        return todos, await storage.get_run_parts("web:browser-1", "run-1")

    todos, parts = asyncio.run(scenario())

    assert [item["status"] for item in todos] == ["completed", "in_progress", "pending"]
    assert len(parts) == 1
    assert parts[0].part_type == "task_checklist"
    assert "[in_progress] build artifact" in parts[0].content
    assert parts[0].metadata["objective"] == "Ship task artifacts"
    assert parts[0].metadata["todos"] == todos


def test_run_trace_recorder_persists_llm_step_part():
    async def scenario():
        storage = MemoryStorage()
        recorder = RunTraceRecorder(storage=storage, message_bus_getter=lambda: None)
        await storage.create_run("web:browser-1", "run-1")
        await recorder.record_llm_step_parts(
            "web:browser-1",
            "run-1",
            [
                LlmStepEvent(
                    iteration=1,
                    attempt=1,
                    status="completed",
                    model="fake-model",
                    duration_ms=12,
                    estimated_input_tokens=42,
                    message_tokens=40,
                    tool_schema_tokens=2,
                    output_tokens=7,
                    total_tokens=49,
                    finish_reason="stop",
                )
            ],
        )
        return await storage.get_run_parts("web:browser-1", "run-1")

    parts = asyncio.run(scenario())

    assert len(parts) == 1
    assert parts[0].part_type == "llm_step"
    assert parts[0].metadata["model"] == "fake-model"
    assert parts[0].metadata["estimated_input_tokens"] == 42
    assert serialize_run_part(parts[0])["artifact"]["kind"] == "llm"


def test_worktree_sandbox_inspector_reports_disabled(tmp_path):
    metadata = WorktreeSandboxInspector(enabled=False, workspace_root=tmp_path).inspect().to_payload()

    assert metadata["enabled"] is False
    assert metadata["status"] == "disabled"
    assert metadata["workspace_root"] == str(tmp_path.resolve())


def test_worktree_sandbox_creates_and_cleans_git_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)

    metadata = WorktreeSandboxInspector(enabled=True, workspace_root=repo).create(
        session_id="web:browser-1",
        run_id="run-1",
    ).to_payload()

    sandbox_path = metadata["sandbox_path"]
    assert metadata["status"] == "created"
    assert metadata["created"] is True
    assert metadata["cleanup_supported"] is True
    assert sandbox_path is not None
    assert (tmp_path / "repo.opensprite-worktrees" / "web_browser-1" / "run-1" / "README.md").exists()

    cleanup = WorktreeSandboxInspector.cleanup(sandbox_path)

    assert cleanup["ok"] is True
    assert cleanup["status"] == "removed"
    assert not (tmp_path / "repo.opensprite-worktrees" / "web_browser-1" / "run-1").exists()


def test_run_trace_recorder_persists_worktree_sandbox_part():
    async def scenario():
        storage = MemoryStorage()
        recorder = RunTraceRecorder(storage=storage, message_bus_getter=lambda: None)
        await storage.create_run("web:browser-1", "run-1")
        await recorder.record_worktree_sandbox_part(
            "web:browser-1",
            "run-1",
            {"enabled": True, "status": "ready", "base_branch": "main", "base_commit": "abc123"},
        )
        return await storage.get_run_parts("web:browser-1", "run-1")

    parts = asyncio.run(scenario())

    assert len(parts) == 1
    assert parts[0].part_type == "worktree_sandbox"
    assert parts[0].metadata["base_branch"] == "main"
    assert serialize_run_part(parts[0])["artifact"]["kind"] == "work"


def test_run_event_sink_persists_and_publishes_safe_payloads():
    async def scenario():
        storage = MemoryStorage()
        bus = MessageBus()
        sink = RunEventSink(storage=storage, message_bus_getter=lambda: bus)
        await storage.create_run("web:browser-1", "run-1")
        await sink.emit(
            "web:browser-1",
            "run-1",
            "tool_result",
            {"tool_name": "demo", "value": object()},
            channel="web",
            external_chat_id="browser-1",
        )
        return (
            await storage.get_run_events("web:browser-1", "run-1"),
            await bus.consume_run_event(),
        )

    stored_events, bus_event = asyncio.run(scenario())

    assert len(stored_events) == 1
    assert stored_events[0].event_type == "tool_result"
    assert stored_events[0].payload["tool_name"] == "demo"
    assert isinstance(stored_events[0].payload["value"], str)
    assert bus_event.event_type == "tool_result"
    assert bus_event.payload == stored_events[0].payload
    assert bus_event.channel == "web"
    assert bus_event.external_chat_id == "browser-1"


def test_serialize_run_event_builds_stable_envelope():
    event = SimpleNamespace(
        event_id=42,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="tool_result",
        payload={"tool_name": "demo", "tool_call_id": "call-1", "ok": False, "result_preview": "failed"},
        created_at=12.5,
    )

    payload = serialize_run_event(event)

    assert payload == {
        "schema_version": 1,
        "event_id": 42,
        "run_id": "run-1",
        "session_id": "web:browser-1",
        "event_type": "tool_result",
        "kind": "tool",
        "status": "error",
        "payload": {"tool_name": "demo", "tool_call_id": "call-1", "ok": False, "result_preview": "failed"},
        "artifact": {
            "schema_version": 1,
            "artifact_id": "tool:call-1",
            "artifact_type": "tool",
            "kind": "tool",
            "status": "error",
            "phase": "result",
            "tool_name": "demo",
            "tool_call_id": "call-1",
            "iteration": None,
            "title": "demo",
            "detail": "failed",
        },
        "created_at": 12.5,
    }


def test_serialize_run_event_projects_permission_artifacts():
    event = SimpleNamespace(
        event_id=43,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="permission_requested",
        payload={
            "request_id": "perm-1",
            "tool_name": "apply_patch",
            "reason": "tool requires approval",
            "status": "pending",
        },
        created_at=12.75,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "permission"
    assert payload["status"] == "pending"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "permission:perm-1",
        "artifact_type": "permission",
        "kind": "permission",
        "status": "pending",
        "title": "apply_patch",
        "detail": "tool requires approval",
        "tool_name": "apply_patch",
        "request_id": "perm-1",
    }


def test_serialize_run_event_classifies_part_delta_as_streaming_text():
    event = SimpleNamespace(
        event_id=44,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="run_part_delta",
        payload={"part_id": "assistant-1", "part_type": "assistant_message", "content_delta": "hello"},
        created_at=13.0,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "text"
    assert payload["status"] == "running"
    assert payload["artifact"] is None


def test_compact_run_events_keeps_lifecycle_events_over_text_noise():
    events = []
    for index in range(90):
        events.append(
            SimpleNamespace(
                event_id=index + 1,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="tool_result",
                payload={"tool_name": f"tool-{index}"},
                created_at=float(index),
            )
        )
    for index in range(30):
        events.append(
            SimpleNamespace(
                event_id=1000 + index,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="run_part_delta",
                payload={"content_delta": str(index)},
                created_at=100.0 + index,
            )
        )

    compacted = compact_run_events(events)
    payload = serialize_run_events(events)

    assert len(compacted) == 104
    assert sum(1 for event in compacted if event.event_type == "run_part_delta") == 24
    assert sum(1 for event in compacted if event.event_type == "tool_result") == 80
    assert compacted[0].event_id == 11
    assert compacted[-1].event_id == 1029
    assert len(payload) == 104
    assert payload[-1]["event_type"] == "run_part_delta"
    assert serialize_run_event_counts(events, payload) == {
        "total": 120,
        "returned": 104,
        "compacted": 16,
        "text_total": 30,
        "text_returned": 24,
        "max_events": 80,
        "max_text_events": 24,
    }


def test_llm_delta_hook_emits_empty_completion_marker():
    calls = []

    async def emit_run_event(session_id, run_id, event_type, payload, **kwargs):
        calls.append((session_id, run_id, event_type, payload, kwargs))

    service = RunHookService(
        message_bus_getter=lambda: None,
        add_run_part=lambda *args, **kwargs: None,
        emit_run_event=emit_run_event,
        format_log_preview=lambda text, max_chars=200: str(text)[:max_chars],
    )
    hook = service.make_llm_delta_hook(
        channel="web",
        external_chat_id="browser-1",
        session_id="web:browser-1",
        run_id="run-1",
        enabled=True,
    )

    async def scenario():
        await hook("assistant:run-1:1", "", "running", 1)
        await hook("assistant:run-1:1", "", "completed", 2)

    asyncio.run(scenario())

    assert len(calls) == 1
    assert calls[0][0] == "web:browser-1"
    assert calls[0][1] == "run-1"
    assert calls[0][2] == "run_part_delta"
    assert calls[0][3] == {
        "part_id": "assistant:run-1:1",
        "part_type": "assistant_message",
        "content_delta": "",
        "state": "completed",
        "sequence": 2,
    }
    assert calls[0][4] == {"channel": "web", "external_chat_id": "browser-1"}


def test_tool_input_delta_hook_emits_tool_input_events():
    calls = []

    async def emit_run_event(session_id, run_id, event_type, payload, **kwargs):
        calls.append((session_id, run_id, event_type, payload, kwargs))

    service = RunHookService(
        message_bus_getter=lambda: None,
        add_run_part=lambda *args, **kwargs: None,
        emit_run_event=emit_run_event,
        format_log_preview=lambda text, max_chars=200: str(text)[:max_chars],
    )
    hook = service.make_tool_input_delta_hook(
        channel="web",
        external_chat_id="browser-1",
        session_id="web:browser-1",
        run_id="run-1",
        enabled=True,
    )

    async def scenario():
        await hook("call-1", "demo", '{"value"', 1)

    asyncio.run(scenario())

    assert calls == [
        (
            "web:browser-1",
            "run-1",
            "tool_input_delta",
            {"tool_call_id": "call-1", "tool_name": "demo", "input_delta": '{"value"', "sequence": 1},
            {"channel": "web", "external_chat_id": "browser-1"},
        )
    ]


def test_serialize_run_part_builds_stable_artifact_shape():
    part = SimpleNamespace(
        part_id=7,
        run_id="run-1",
        session_id="web:browser-1",
        part_type="tool_result",
        tool_name="demo",
        content="failed",
        metadata={"tool_call_id": "call-1", "ok": False, "result_preview": "failed"},
        created_at=13.0,
    )

    payload = serialize_run_part(part)

    assert payload["schema_version"] == 1
    assert payload["part_id"] == 7
    assert payload["kind"] == "tool"
    assert payload["state"] == "error"
    assert payload["metadata"] == {"tool_call_id": "call-1", "ok": False, "result_preview": "failed"}
    assert payload["artifact"]["artifact_id"] == "tool:call-1"
    assert payload["artifact"]["phase"] == "tool_result"
    assert payload["artifact"]["status"] == "error"


def test_serialize_file_change_builds_stable_snapshot_shape():
    change = SimpleNamespace(
        change_id=3,
        run_id="run-1",
        session_id="web:browser-1",
        tool_name="apply_patch",
        path="notes.txt",
        action="modify",
        before_sha256="before",
        after_sha256="after",
        before_content="old",
        after_content="new",
        diff="-old\n+new",
        metadata={"diff_len": 9},
        created_at=14.0,
    )

    payload = serialize_file_change(change)

    assert payload["schema_version"] == 1
    assert payload["change_id"] == 3
    assert payload["kind"] == "file"
    assert payload["state"] == "completed"
    assert payload["path"] == "notes.txt"
    assert payload["before_content"] == "old"
    assert payload["after_content"] == "new"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "file_change:3",
        "artifact_type": "file_change",
        "kind": "file",
        "status": "completed",
        "path": "notes.txt",
        "action": "modify",
        "tool_name": "apply_patch",
        "diff_len": 9,
        "snapshots_available": {"before": True, "after": True},
        "metadata": {"diff_len": 9},
    }


def test_serialize_run_artifacts_merges_tool_event_and_part_by_call_id():
    trace = SimpleNamespace(
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="tool_started",
                payload={"tool_name": "demo", "tool_call_id": "call-1", "args_preview": "{}"},
                created_at=10.0,
            ),
            SimpleNamespace(
                event_id=2,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="tool_result",
                payload={"tool_name": "demo", "tool_call_id": "call-1", "ok": True, "result_preview": "done"},
                created_at=11.0,
            ),
        ],
        parts=[
            SimpleNamespace(
                part_id=7,
                run_id="run-1",
                session_id="web:browser-1",
                part_type="tool_result",
                tool_name="demo",
                content="done",
                metadata={"tool_call_id": "call-1", "ok": True, "result_preview": "done"},
                created_at=12.0,
            )
        ],
        file_changes=[],
    )

    artifacts = serialize_run_artifacts(trace)

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["artifact_id"] == "tool:call-1"
    assert artifact["kind"] == "tool"
    assert artifact["status"] == "completed"
    assert artifact["phase"] == "tool_result"
    assert artifact["tool_call_id"] == "call-1"
    assert artifact["source"] == "part"
    assert artifact["sources"] == ["event", "part"]


def test_serialize_run_summary_builds_stable_card_payload():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-1",
            session_id="web:browser-1",
            status="completed",
            metadata={"objective": "Ship the fix", "verification_attempted": True, "verification_passed": True},
            created_at=10.0,
            updated_at=15.0,
            finished_at=16.5,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="tool_started",
                payload={"tool_name": "demo", "tool_call_id": "call-1"},
                created_at=11.0,
            ),
            SimpleNamespace(
                event_id=2,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="verification_result",
                payload={"ok": True, "verification_status": "passed", "verification_name": "pytest", "result_preview": "ok"},
                created_at=14.0,
            ),
            SimpleNamespace(
                event_id=3,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="completion_gate.evaluated",
                payload={"status": "complete"},
                created_at=15.0,
            ),
        ],
        parts=[
            SimpleNamespace(
                part_id=1,
                run_id="run-1",
                session_id="web:browser-1",
                part_type="tool_call",
                tool_name="demo",
                content="{}",
                metadata={"tool_call_id": "call-1"},
                created_at=11.5,
            )
        ],
        file_changes=[
            SimpleNamespace(
                change_id=5,
                run_id="run-1",
                session_id="web:browser-1",
                tool_name="apply_patch",
                path="notes.txt",
                action="modify",
                before_sha256="before",
                after_sha256="after",
                before_content="old",
                after_content="new",
                diff="-old\n+new",
                metadata={"diff_len": 9},
                created_at=13.0,
            )
        ],
    )

    summary = serialize_run_summary(trace)

    assert summary["schema_version"] == 1
    assert summary["run_id"] == "run-1"
    assert summary["objective"] == "Ship the fix"
    assert summary["duration_seconds"] == 6.5
    assert summary["tools"] == [{"name": "demo", "count": 1}]
    assert summary["verification"] == {"attempted": True, "passed": True, "status": "passed", "name": "pytest", "summary": "ok"}
    assert summary["artifact_counts"] == {"total": 3, "tool": 1, "file": 1, "verification": 1}
    assert summary["completion"] == {"status": "complete"}
    assert summary["warnings"] == []
    assert summary["counts"] == {"events": 3, "parts": 1, "tool_calls": 1, "file_changes": 1}
