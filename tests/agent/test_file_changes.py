import asyncio
import hashlib
from pathlib import Path

from opensprite.agent.file_changes import RunFileChangeService
from opensprite.storage import MemoryStorage


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _service(storage: MemoryStorage, workspace_root: Path) -> RunFileChangeService:
    async def emit_run_event(*args, **kwargs):
        return None

    return RunFileChangeService(
        storage=storage,
        workspace_for_chat=lambda _chat_id: workspace_root,
        emit_run_event=emit_run_event,
        format_log_preview=lambda content, max_chars=160: str(content or "")[:max_chars],
    )


def test_file_change_service_can_preview_and_apply_safe_revert(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        service = _service(storage, tmp_path)
        target = tmp_path / "notes.txt"
        target.write_text("after\n", encoding="utf-8")
        await storage.create_run("chat-1", "run-1")
        change = await storage.add_run_file_change(
            "chat-1",
            "run-1",
            "edit_file",
            "notes.txt",
            "update",
            before_sha256=_sha256("before\n"),
            after_sha256=_sha256("after\n"),
            before_content="before\n",
            after_content="after\n",
        )

        preview = await service.preview_revert("chat-1", "run-1", change.change_id)
        dry_run = await service.revert("chat-1", "run-1", change.change_id)
        after_dry_run = target.read_text(encoding="utf-8")
        applied = await service.revert("chat-1", "run-1", change.change_id, dry_run=False)
        return preview, dry_run, after_dry_run, applied, target.read_text(encoding="utf-8")

    preview, dry_run, after_dry_run, applied, final_content = asyncio.run(scenario())

    assert preview["status"] == "ready"
    assert preview["ok"] is True
    assert preview["revert_action"] == "write"
    assert "-after" in preview["diff"]
    assert "+before" in preview["diff"]
    assert dry_run["dry_run"] is True
    assert dry_run["applied"] is False
    assert after_dry_run == "after\n"
    assert applied["status"] == "applied"
    assert applied["ok"] is True
    assert applied["applied"] is True
    assert applied["post_sha256"] == _sha256("before\n")
    assert final_content == "before\n"


def test_file_change_service_refuses_current_hash_conflict(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        service = _service(storage, tmp_path)
        target = tmp_path / "notes.txt"
        target.write_text("user change\n", encoding="utf-8")
        await storage.create_run("chat-1", "run-1")
        change = await storage.add_run_file_change(
            "chat-1",
            "run-1",
            "edit_file",
            "notes.txt",
            "update",
            before_sha256=_sha256("before\n"),
            after_sha256=_sha256("after\n"),
            before_content="before\n",
            after_content="after\n",
        )

        preview = await service.preview_revert("chat-1", "run-1", change.change_id)
        applied = await service.revert("chat-1", "run-1", change.change_id, dry_run=False)
        return preview, applied, target.read_text(encoding="utf-8")

    preview, applied, final_content = asyncio.run(scenario())

    assert preview["status"] == "conflict"
    assert preview["ok"] is False
    assert "current file hash" in preview["reason"]
    assert applied["applied"] is False
    assert final_content == "user change\n"


def test_file_change_service_requires_before_snapshot(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        service = _service(storage, tmp_path)
        target = tmp_path / "notes.txt"
        target.write_text("after\n", encoding="utf-8")
        await storage.create_run("chat-1", "run-1")
        change = await storage.add_run_file_change(
            "chat-1",
            "run-1",
            "edit_file",
            "notes.txt",
            "update",
            before_sha256=_sha256("before\n"),
            after_sha256=_sha256("after\n"),
            before_content=None,
            after_content="after\n",
        )

        preview = await service.preview_revert("chat-1", "run-1", change.change_id)
        applied = await service.revert("chat-1", "run-1", change.change_id, dry_run=False)
        return preview, applied, target.read_text(encoding="utf-8")

    preview, applied, final_content = asyncio.run(scenario())

    assert preview["status"] == "unavailable"
    assert "before_content snapshot" in preview["reason"]
    assert applied["applied"] is False
    assert final_content == "after\n"
