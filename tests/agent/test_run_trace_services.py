import asyncio
import subprocess
from types import SimpleNamespace

from opensprite.agent.run_hooks import RunHookService
from opensprite.agent.execution import LlmStepEvent
from opensprite.agent.run_trace import RUN_PART_CONTENT_MAX_CHARS, RunEventSink, RunTraceRecorder, truncate_run_part_content
from opensprite.agent.worktree import WorktreeSandboxInspector
from opensprite.bus import MessageBus
from opensprite.runs.schema import (
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


def test_serialize_run_event_projects_curator_artifact():
    event = SimpleNamespace(
        event_id=44,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="curator.completed",
        payload={
            "status": "completed",
            "changed": ["memory", "skills"],
            "summary": "Updated memory and skills.",
        },
        created_at=13.0,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "completed"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "curator",
        "artifact_type": "curator",
        "kind": "work",
        "status": "completed",
        "title": "Curator",
        "detail": "Updated memory and skills.",
        "metadata": {
            "status": "completed",
            "changed": ["memory", "skills"],
            "summary": "Updated memory and skills.",
        },
    }


def test_serialize_run_event_projects_curator_job_artifact():
    event = SimpleNamespace(
        event_id=45,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="curator.job.completed",
        payload={
            "status": "completed",
            "job": "memory",
            "label": "memory",
            "summary": "Updated memory.",
        },
        created_at=13.0,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "completed"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "curator_job:memory",
        "artifact_type": "curator_job",
        "kind": "work",
        "status": "completed",
        "title": "Curator job: memory",
        "detail": "Updated memory.",
        "metadata": {
            "status": "completed",
            "job": "memory",
            "label": "memory",
            "summary": "Updated memory.",
        },
    }


def test_serialize_run_event_projects_curator_failed_artifact():
    event = SimpleNamespace(
        event_id=46,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="curator.failed",
        payload={
            "status": "failed",
            "error": "memory broke",
            "job": "memory",
        },
        created_at=13.0,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "failed"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "curator",
        "artifact_type": "curator",
        "kind": "work",
        "status": "failed",
        "title": "Curator",
        "detail": "memory broke",
        "metadata": {
            "status": "failed",
            "error": "memory broke",
            "job": "memory",
        },
    }


def test_serialize_run_event_projects_subagent_artifact():
    event = SimpleNamespace(
        event_id=47,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="subagent.completed",
        payload={
            "status": "completed",
            "task_id": "task_abc12345",
            "prompt_type": "implementer",
            "child_session_id": "web:browser-1:subagent:task_abc12345",
            "child_run_id": "run_child_1",
            "parent_session_id": "web:browser-1",
            "parent_run_id": "run-1",
            "resume": False,
            "summary": "Applied focused implementation changes.",
        },
        created_at=13.25,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "completed"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "subagent:task_abc12345",
        "artifact_type": "subagent_task",
        "kind": "work",
        "status": "completed",
        "title": "Subagent: implementer",
        "detail": "Applied focused implementation changes.",
        "metadata": {
            "status": "completed",
            "task_id": "task_abc12345",
            "prompt_type": "implementer",
            "child_session_id": "web:browser-1:subagent:task_abc12345",
            "child_run_id": "run_child_1",
            "parent_session_id": "web:browser-1",
            "parent_run_id": "run-1",
            "resume": False,
            "summary": "Applied focused implementation changes.",
        },
    }


def test_serialize_run_event_projects_cancelled_subagent_artifact():
    event = SimpleNamespace(
        event_id=48,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="subagent.cancelled",
        payload={
            "status": "cancelled",
            "task_id": "task_abc12345",
            "prompt_type": "researcher",
            "child_session_id": "web:browser-1:subagent:task_abc12345",
            "child_run_id": "run_child_2",
            "parent_session_id": "web:browser-1",
            "parent_run_id": "run-1",
            "resume": False,
            "error": "cancelled",
        },
        created_at=13.5,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "cancelled"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "subagent:task_abc12345",
        "artifact_type": "subagent_task",
        "kind": "work",
        "status": "cancelled",
        "title": "Subagent: researcher",
        "detail": "cancelled",
        "metadata": {
            "status": "cancelled",
            "task_id": "task_abc12345",
            "prompt_type": "researcher",
            "child_session_id": "web:browser-1:subagent:task_abc12345",
            "child_run_id": "run_child_2",
            "parent_session_id": "web:browser-1",
            "parent_run_id": "run-1",
            "resume": False,
            "error": "cancelled",
        },
    }


def test_serialize_run_event_projects_parallel_subagent_group_artifact():
    event = SimpleNamespace(
        event_id=49,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="subagent.group.completed",
        payload={
            "status": "completed",
            "group_id": "fanout_abc12345",
            "total_tasks": 2,
            "max_parallel": 2,
            "completed_count": 2,
            "failed_count": 0,
            "cancelled_count": 0,
            "task_ids": ["task_a", "task_b"],
            "tasks": [
                {"task_id": "task_a", "prompt_type": "researcher", "status": "completed"},
                {"task_id": "task_b", "prompt_type": "code-reviewer", "status": "completed"},
            ],
            "summary": "Completed 2/2 parallel subagent task(s).",
        },
        created_at=13.75,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "completed"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "subagent_group:fanout_abc12345",
        "artifact_type": "subagent_group",
        "kind": "work",
        "status": "completed",
        "title": "Parallel subagents",
        "detail": "Completed 2/2 parallel subagent task(s).",
        "metadata": {
            "status": "completed",
            "group_id": "fanout_abc12345",
            "total_tasks": 2,
            "max_parallel": 2,
            "completed_count": 2,
            "failed_count": 0,
            "cancelled_count": 0,
            "task_ids": ["task_a", "task_b"],
            "tasks": [
                {"task_id": "task_a", "prompt_type": "researcher", "status": "completed"},
                {"task_id": "task_b", "prompt_type": "code-reviewer", "status": "completed"},
            ],
            "summary": "Completed 2/2 parallel subagent task(s).",
        },
    }


def test_serialize_run_event_projects_workflow_artifact():
    event = SimpleNamespace(
        event_id=50,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="workflow.completed",
        payload={
            "workflow_run_id": "workflow_abc12345",
            "workflow": "implement_then_review",
            "status": "completed",
            "summary": "Completed 2/2 workflow step(s).",
            "total_steps": 2,
        },
        created_at=14.0,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "completed"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "workflow:workflow_abc12345",
        "artifact_type": "workflow",
        "kind": "work",
        "status": "completed",
        "title": "Workflow: implement_then_review",
        "detail": "Completed 2/2 workflow step(s).",
        "metadata": {
            "workflow_run_id": "workflow_abc12345",
            "workflow": "implement_then_review",
            "status": "completed",
            "summary": "Completed 2/2 workflow step(s).",
            "total_steps": 2,
        },
    }


def test_serialize_run_event_projects_workflow_step_artifact():
    event = SimpleNamespace(
        event_id=51,
        run_id="run-1",
        session_id="web:browser-1",
        event_type="workflow.step.completed",
        payload={
            "workflow_run_id": "workflow_abc12345",
            "workflow": "implement_then_review",
            "step_id": "review",
            "label": "Code review",
            "prompt_type": "code-reviewer",
            "step_index": 2,
            "total_steps": 2,
            "summary": "No major findings.",
            "status": "completed",
        },
        created_at=14.2,
    )

    payload = serialize_run_event(event)

    assert payload["kind"] == "work"
    assert payload["status"] == "completed"
    assert payload["artifact"] == {
        "schema_version": 1,
        "artifact_id": "workflow_step:workflow_abc12345:review",
        "artifact_type": "workflow_step",
        "kind": "work",
        "status": "completed",
        "title": "Workflow step: Code review",
        "detail": "No major findings.",
        "metadata": {
            "workflow_run_id": "workflow_abc12345",
            "workflow": "implement_then_review",
            "step_id": "review",
            "label": "Code review",
            "prompt_type": "code-reviewer",
            "step_index": 2,
            "total_steps": 2,
            "summary": "No major findings.",
            "status": "completed",
        },
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


def test_reasoning_delta_hook_emits_inspector_only_events():
    calls = []

    async def emit_run_event(session_id, run_id, event_type, payload, **kwargs):
        calls.append((session_id, run_id, event_type, payload, kwargs))

    service = RunHookService(
        message_bus_getter=lambda: None,
        add_run_part=lambda *args, **kwargs: None,
        emit_run_event=emit_run_event,
        format_log_preview=lambda text, max_chars=200: str(text)[:max_chars],
    )
    hook = service.make_reasoning_delta_hook(
        channel="web",
        external_chat_id="browser-1",
        session_id="web:browser-1",
        run_id="run-1",
        enabled=True,
    )

    async def scenario():
        await hook("thinking", 1)

    asyncio.run(scenario())

    assert calls == [
        (
            "web:browser-1",
            "run-1",
            "reasoning_delta",
            {"content_delta": "thinking", "sequence": 1, "inspector_only": True},
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
            SimpleNamespace(
                event_id=4,
                run_id="run-1",
                session_id="web:browser-1",
                event_type="subagent.group.completed",
                payload={
                    "status": "completed",
                    "group_id": "fanout_abc12345",
                    "total_tasks": 2,
                    "max_parallel": 2,
                    "completed_count": 2,
                    "failed_count": 0,
                    "cancelled_count": 0,
                    "task_ids": ["task_a", "task_b"],
                    "tasks": [
                        {"task_id": "task_a", "prompt_type": "researcher", "status": "completed", "summary": "ok:a"},
                        {"task_id": "task_b", "prompt_type": "code-reviewer", "status": "completed", "summary": "ok:b"},
                    ],
                    "summary": "Completed 2/2 parallel subagent task(s).",
                },
                created_at=15.5,
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
    assert summary["review"] == {
        "required": False,
        "attempted": False,
        "passed": False,
        "status": "not_required",
        "summary": "",
        "prompt_types": [],
        "finding_count": 0,
    }
    assert summary["structured_subagents"] == {
        "total": 0,
        "by_prompt_type": {},
        "by_status": {},
        "total_sections": 0,
        "total_items": 0,
        "total_findings": 0,
        "total_questions": 0,
        "total_residual_risks": 0,
        "results": [],
    }
    assert summary["parallel_delegation"] == {
        "group_count": 1,
        "task_count": 2,
        "groups": [
            {
                "group_id": "fanout_abc12345",
                "status": "completed",
                "total_tasks": 2,
                "max_parallel": 2,
                "completed_count": 2,
                "failed_count": 0,
                "cancelled_count": 0,
                "summary": "Completed 2/2 parallel subagent task(s).",
                "tasks": [
                    {"task_id": "task_a", "prompt_type": "researcher", "status": "completed", "summary": "ok:a"},
                    {"task_id": "task_b", "prompt_type": "code-reviewer", "status": "completed", "summary": "ok:b"},
                ],
                "created_at": 15.5,
            }
        ],
    }
    assert summary["artifact_counts"] == {"total": 4, "tool": 1, "file": 1, "verification": 1}
    assert summary["completion"] == {"status": "complete"}
    assert summary["warnings"] == []
    assert summary["counts"] == {"events": 4, "parts": 1, "tool_calls": 1, "file_changes": 1}


def test_serialize_run_summary_collects_structured_subagent_results():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-structured",
            session_id="web:browser-structured",
            status="completed",
            metadata={"objective": "Structured review"},
            created_at=20.0,
            updated_at=24.0,
            finished_at=25.0,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-structured",
                session_id="web:browser-structured",
                event_type="subagent.completed",
                payload={
                    "status": "completed",
                    "task_id": "task_review",
                    "prompt_type": "code-reviewer",
                    "summary": "One correctness risk found.",
                    "structured_output": {
                        "schema_version": 1,
                        "contract": "readonly_subagent_result",
                        "prompt_type": "code-reviewer",
                        "status": "ok",
                        "summary": "One correctness risk found.",
                        "section_count": 1,
                        "item_count": 1,
                        "finding_count": 1,
                        "question_count": 0,
                        "residual_risk_count": 1,
                        "sections": [
                            {
                                "key": "findings",
                                "title": "Review Findings",
                                "type": "finding_list",
                                "items": [{"title": "Null handling", "severity": "high"}],
                            }
                        ],
                        "questions": [],
                        "residual_risks": ["Did not run integration tests."],
                        "sources": [{"kind": "file", "path": "src/foo.py", "start_line": 10, "end_line": 14}],
                        "truncated": False,
                    },
                },
                created_at=22.0,
            ),
            SimpleNamespace(
                event_id=2,
                run_id="run-structured",
                session_id="web:browser-structured",
                event_type="completion_gate.evaluated",
                payload={"status": "complete"},
                created_at=23.0,
            ),
        ],
        parts=[],
        file_changes=[],
    )

    summary = serialize_run_summary(trace)

    assert summary["structured_subagents"] == {
        "total": 1,
        "by_prompt_type": {"code-reviewer": 1},
        "by_status": {"ok": 1},
        "total_sections": 1,
        "total_items": 1,
        "total_findings": 1,
        "total_questions": 0,
        "total_residual_risks": 1,
        "results": [
            {
                "task_id": "task_review",
                "prompt_type": "code-reviewer",
                "status": "ok",
                "summary": "One correctness risk found.",
                "section_count": 1,
                "item_count": 1,
                "finding_count": 1,
                "question_count": 0,
                "residual_risk_count": 1,
                "created_at": 22.0,
            }
        ],
    }


def test_serialize_run_summary_collects_workflow_results():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-workflow-summary",
            session_id="web:browser-workflow",
            status="completed",
            metadata={"objective": "Workflow summary"},
            created_at=30.0,
            updated_at=35.0,
            finished_at=36.0,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-workflow-summary",
                session_id="web:browser-workflow",
                event_type="workflow.completed",
                payload={
                    "workflow_run_id": "workflow_abc12345",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "task_preview": "Implement a safe change.",
                    "total_steps": 2,
                    "completed_steps": 2,
                    "failed_steps": 0,
                    "summary": "Completed 2/2 workflow step(s).",
                },
                created_at=34.0,
            ),
        ],
        parts=[],
        file_changes=[],
    )

    summary = serialize_run_summary(trace)

    assert summary["workflows"] == {
        "total": 1,
        "by_workflow": {"implement_then_review": 1},
        "by_status": {"completed": 1},
        "results": [
            {
                "workflow_run_id": "workflow_abc12345",
                "workflow": "implement_then_review",
                "status": "completed",
                "task_preview": "Implement a safe change.",
                "total_steps": 2,
                "completed_steps": 2,
                "failed_steps": 0,
                "summary": "Completed 2/2 workflow step(s).",
                "created_at": 34.0,
            }
        ],
    }


def test_serialize_run_summary_collects_failed_workflow_follow_up_detail():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-workflow-failed",
            session_id="web:browser-workflow",
            status="completed",
            metadata={"objective": "Workflow summary"},
            created_at=30.0,
            updated_at=35.0,
            finished_at=36.0,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-workflow-failed",
                session_id="web:browser-workflow",
                event_type="workflow.failed",
                payload={
                    "workflow_run_id": "workflow_abc12345",
                    "workflow": "implement_then_review",
                    "status": "failed",
                    "task_preview": "Implement a safe change.",
                    "total_steps": 2,
                    "completed_steps": 1,
                    "failed_steps": 1,
                    "summary": "Workflow stopped after 1/2 completed step(s).",
                    "next_step_id": "review",
                    "next_step_label": "Code review",
                    "error": "review step failed",
                },
                created_at=34.0,
            ),
        ],
        parts=[],
        file_changes=[],
    )

    summary = serialize_run_summary(trace)

    assert summary["workflows"] == {
        "total": 1,
        "by_workflow": {"implement_then_review": 1},
        "by_status": {"failed": 1},
        "results": [
            {
                "workflow_run_id": "workflow_abc12345",
                "workflow": "implement_then_review",
                "status": "failed",
                "task_preview": "Implement a safe change.",
                "total_steps": 2,
                "completed_steps": 1,
                "failed_steps": 1,
                "summary": "Resolve the Code review step failure: review step failed",
                "created_at": 34.0,
            }
        ],
    }


def test_serialize_run_summary_preserves_completion_follow_up_target():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-follow-up",
            session_id="web:browser-follow-up",
            status="completed",
            metadata={"objective": "Workflow follow-up"},
            created_at=40.0,
            updated_at=45.0,
            finished_at=46.0,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-follow-up",
                session_id="web:browser-follow-up",
                event_type="completion_gate.evaluated",
                payload={
                    "status": "incomplete",
                    "reason": "workflow implement_then_review did not complete successfully",
                    "active_task_detail": "Resume with the Code review step in implement_then_review. Workflow stopped after 1/2 completed step(s).",
                    "follow_up_workflow": "implement_then_review",
                    "follow_up_step_id": "review",
                    "follow_up_step_label": "Code review",
                    "follow_up_prompt_type": "code-reviewer",
                },
                created_at=44.0,
            ),
        ],
        parts=[],
        file_changes=[],
    )

    summary = serialize_run_summary(trace)

    assert summary["completion"] == {
        "status": "incomplete",
        "reason": "workflow implement_then_review did not complete successfully",
        "active_task_detail": "Resume with the Code review step in implement_then_review. Workflow stopped after 1/2 completed step(s).",
        "follow_up_workflow": "implement_then_review",
        "follow_up_step_id": "review",
        "follow_up_step_label": "Code review",
        "follow_up_prompt_type": "code-reviewer",
    }


def test_serialize_run_summary_marks_parallel_delegation_warnings():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-2",
            session_id="web:browser-2",
            status="completed",
            metadata={"objective": "Review in parallel"},
            created_at=10.0,
            updated_at=12.0,
            finished_at=13.0,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-2",
                session_id="web:browser-2",
                event_type="subagent.group.failed",
                payload={
                    "status": "failed",
                    "group_id": "fanout_warn",
                    "total_tasks": 2,
                    "max_parallel": 2,
                    "completed_count": 1,
                    "failed_count": 1,
                    "cancelled_count": 0,
                    "task_ids": ["task_a", "task_b"],
                    "tasks": [
                        {"task_id": "task_a", "prompt_type": "researcher", "status": "completed"},
                        {"task_id": "task_b", "prompt_type": "code-reviewer", "status": "failed", "error": "broken"},
                    ],
                    "summary": "Completed 1/2 parallel subagent task(s); 1 failed.",
                },
                created_at=12.5,
            ),
            SimpleNamespace(
                event_id=2,
                run_id="run-2",
                session_id="web:browser-2",
                event_type="completion_gate.evaluated",
                payload={"status": "complete"},
                created_at=12.75,
            ),
        ],
        parts=[],
        file_changes=[],
    )

    summary = serialize_run_summary(trace)

    assert summary["parallel_delegation"]["group_count"] == 1
    assert summary["warnings"] == ["parallel_delegation_failed"]


def test_serialize_run_summary_marks_review_warning_when_required_review_missing():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-review",
            session_id="web:browser-review",
            status="completed",
            metadata={"objective": "Review-gated completion"},
            created_at=10.0,
            updated_at=12.0,
            finished_at=13.0,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-review",
                session_id="web:browser-review",
                event_type="completion_gate.evaluated",
                payload={
                    "status": "needs_review",
                    "reason": "delegated review was not recorded for code changes",
                    "review_required": True,
                    "review_attempted": False,
                    "review_passed": False,
                    "review_summary": "",
                    "review_prompt_types": [],
                    "review_finding_count": 0,
                },
                created_at=12.5,
            ),
        ],
        parts=[],
        file_changes=[],
    )

    summary = serialize_run_summary(trace)

    assert summary["review"] == {
        "required": True,
        "attempted": False,
        "passed": False,
        "status": "not_attempted",
        "summary": "",
        "prompt_types": [],
        "finding_count": 0,
    }
    assert summary["warnings"] == ["review_not_passed"]


def test_serialize_run_summary_includes_structured_subagents():
    trace = SimpleNamespace(
        run=SimpleNamespace(
            run_id="run-3",
            session_id="web:browser-3",
            status="completed",
            metadata={"objective": "Review with structure"},
            created_at=20.0,
            updated_at=24.0,
            finished_at=25.0,
        ),
        events=[
            SimpleNamespace(
                event_id=1,
                run_id="run-3",
                session_id="web:browser-3",
                event_type="subagent.completed",
                payload={
                    "status": "completed",
                    "task_id": "task_review",
                    "prompt_type": "code-reviewer",
                    "summary": "One correctness risk found.",
                    "structured_output": {
                        "schema_version": 1,
                        "contract": "readonly_subagent_result",
                        "prompt_type": "code-reviewer",
                        "status": "ok",
                        "summary": "One correctness risk found.",
                        "section_count": 1,
                        "item_count": 1,
                        "finding_count": 1,
                        "question_count": 0,
                        "residual_risk_count": 1,
                        "sections": [
                            {
                                "key": "findings",
                                "title": "Review Findings",
                                "type": "finding_list",
                                "items": [{"title": "Null handling", "severity": "high"}],
                            }
                        ],
                        "questions": [],
                        "residual_risks": ["Did not run integration tests."],
                        "sources": [{"kind": "file", "path": "src/foo.py", "start_line": 10, "end_line": 14}],
                        "truncated": False,
                    },
                },
                created_at=22.0,
            ),
            SimpleNamespace(
                event_id=2,
                run_id="run-3",
                session_id="web:browser-3",
                event_type="completion_gate.evaluated",
                payload={"status": "complete"},
                created_at=23.0,
            ),
        ],
        parts=[],
        file_changes=[],
    )

    summary = serialize_run_summary(trace)

    assert summary["structured_subagents"] == {
        "total": 1,
        "by_prompt_type": {"code-reviewer": 1},
        "by_status": {"ok": 1},
        "total_sections": 1,
        "total_items": 1,
        "total_findings": 1,
        "total_questions": 0,
        "total_residual_risks": 1,
        "results": [
            {
                "task_id": "task_review",
                "prompt_type": "code-reviewer",
                "status": "ok",
                "summary": "One correctness risk found.",
                "section_count": 1,
                "item_count": 1,
                "finding_count": 1,
                "question_count": 0,
                "residual_risk_count": 1,
                "created_at": 22.0,
            }
        ],
    }
