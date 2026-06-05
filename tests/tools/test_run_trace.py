import asyncio
import json

from opensprite.storage import MemoryStorage
from opensprite.tools.result_status import classify_tool_result_status
from opensprite.tools.run_trace import ListRunFileChangesTool, PreviewRunFileChangeRevertTool


def test_list_run_file_changes_tool_lists_specific_run():
    async def scenario():
        storage = MemoryStorage()
        await storage.create_run("chat-1", "run-1", created_at=10.0)
        await storage.add_run_file_change(
            "chat-1",
            "run-1",
            "write_file",
            "notes.txt",
            "add",
            after_sha256="a" * 64,
            after_content="hello\n",
            diff="--- /dev/null\n+++ b/notes.txt",
            created_at=11.0,
        )
        tool = ListRunFileChangesTool(storage=storage, get_session_id=lambda: "chat-1")
        return await tool.execute(run_id="run-1", include_diffs=True)

    payload = json.loads(asyncio.run(scenario()))

    assert payload["session_id"] == "chat-1"
    assert payload["run_id"] == "run-1"
    assert payload["count"] == 1
    assert payload["file_changes"][0]["change_id"] == 1
    assert payload["file_changes"][0]["path"] == "notes.txt"
    assert payload["file_changes"][0]["action"] == "add"
    assert payload["file_changes"][0]["snapshots_available"] == {"before": False, "after": True}
    assert "+++ b/notes.txt" in payload["file_changes"][0]["diff_preview"]


def test_list_run_file_changes_tool_scans_recent_runs():
    async def scenario():
        storage = MemoryStorage()
        await storage.create_run("chat-1", "run-1", created_at=10.0)
        await storage.create_run("chat-1", "run-2", created_at=20.0)
        await storage.add_run_file_change(
            "chat-1",
            "run-1",
            "write_file",
            "old.txt",
            "add",
            created_at=11.0,
        )
        await storage.add_run_file_change(
            "chat-1",
            "run-2",
            "edit_file",
            "new.txt",
            "update",
            created_at=21.0,
        )
        tool = ListRunFileChangesTool(storage=storage, get_session_id=lambda: "chat-1")
        return await tool.execute(change_limit=2)

    payload = json.loads(asyncio.run(scenario()))

    assert payload["run_id"] is None
    assert payload["scanned_runs"] == 2
    assert [entry["run_id"] for entry in payload["file_changes"]] == ["run-2", "run-1"]
    assert [entry["path"] for entry in payload["file_changes"]] == ["new.txt", "old.txt"]


def test_list_run_file_changes_tool_keeps_default_scan_floor():
    async def scenario():
        storage = MemoryStorage()
        for index in range(1, 11):
            await storage.create_run("chat-1", f"run-{index}", created_at=float(index))
        await storage.add_run_file_change(
            "chat-1",
            "run-3",
            "write_file",
            "older.txt",
            "add",
            created_at=3.1,
        )
        tool = ListRunFileChangesTool(storage=storage, get_session_id=lambda: "chat-1")
        return await tool.execute(run_limit=5, change_limit=1)

    payload = json.loads(asyncio.run(scenario()))

    assert payload["scanned_runs"] > 5
    assert payload["count"] == 1
    assert payload["file_changes"][0]["run_id"] == "run-3"
    assert payload["file_changes"][0]["path"] == "older.txt"


def test_preview_run_file_change_revert_tool_delegates_with_current_session():
    calls = []

    async def preview(session_id, run_id, change_id):
        calls.append((session_id, run_id, change_id))
        return {"ok": True, "status": "ready", "path": "notes.txt"}

    async def scenario():
        tool = PreviewRunFileChangeRevertTool(get_session_id=lambda: "chat-1", preview_revert=preview)
        return await tool.execute(run_id="run-1", change_id=7)

    payload = json.loads(asyncio.run(scenario()))

    assert calls == [("chat-1", "run-1", 7)]
    assert payload == {"ok": True, "path": "notes.txt", "status": "ready"}


def test_run_trace_tools_require_current_session():
    async def preview(session_id, run_id, change_id):
        raise AssertionError("preview should not be called without session context")

    async def scenario():
        storage = MemoryStorage()
        list_tool = ListRunFileChangesTool(storage=storage, get_session_id=lambda: None)
        preview_tool = PreviewRunFileChangeRevertTool(get_session_id=lambda: None, preview_revert=preview)
        return (
            await list_tool.execute(),
            await preview_tool.execute(run_id="run-1", change_id=1),
        )

    list_result, preview_result = asyncio.run(scenario())

    list_status = classify_tool_result_status(list_result)
    preview_status = classify_tool_result_status(preview_result)
    assert list_status.ok is False
    assert list_status.error_type == "ToolValidationError"
    assert list_status.category == "session_unavailable"
    assert list_status.invalid_arguments is True
    assert "list_run_file_changes requires an active session context" in list_status.error
    assert preview_status.ok is False
    assert preview_status.error_type == "ToolValidationError"
    assert preview_status.category == "session_unavailable"
    assert preview_status.invalid_arguments is True
    assert "preview_run_file_change_revert requires an active session context" in preview_status.error
