import asyncio
import hashlib

from opensprite.tools.filesystem import ApplyPatchTool, EditFileTool, ReadFileTool, WriteFileTool


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_read_file_returns_sha256_for_stale_read_guard(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello\n", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(path="notes.txt"))

    assert f"SHA256: {_sha256('hello\n')}" in result


def test_write_file_returns_unified_diff(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(path="notes.txt", content="hello\n"))

    assert "Successfully wrote to notes.txt" in result
    assert "Diff:" in result
    assert "--- /dev/null" in result
    assert "+++ b/notes.txt" in result
    assert "+hello" in result


def test_write_file_reports_post_edit_python_diagnostics(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(path="app.py", content="VALUE = 1\n"))

    assert "Successfully wrote to app.py" in result
    assert "Diagnostics:" in result
    assert "app.py [python_syntax] OK" in result


def test_edit_file_returns_unified_diff(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("old\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(
            path="notes.txt",
            old_text="old",
            new_text="new",
            expected_sha256=_sha256("old\n"),
        )
    )

    assert "Successfully edited notes.txt" in result
    assert "File Versions:" in result
    assert "Diff:" in result
    assert "--- a/notes.txt" in result
    assert "+++ b/notes.txt" in result
    assert "-old" in result
    assert "+new" in result


def test_edit_file_surfaces_post_edit_python_syntax_failures(tmp_path):
    target = tmp_path / "broken.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(
            path="broken.py",
            old_text="VALUE = 1",
            new_text="def broken(:",
            expected_sha256=_sha256("VALUE = 1\n"),
        )
    )

    assert result.startswith("Error: Changes were written successfully but post-edit diagnostics failed.")
    assert "broken.py [python_syntax]: invalid syntax" in result
    assert target.read_text(encoding="utf-8") == "def broken(:\n"


def test_apply_patch_updates_adds_and_deletes_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    old_file = src / "old.py"
    old_file.write_text("obsolete\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)
    app_hash = _sha256("VALUE = 1\n")
    old_hash = _sha256("obsolete\n")

    result = asyncio.run(
        tool.execute(
            changes=[
                {
                    "action": "update",
                    "path": "src/app.py",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 2",
                    "expected_sha256": app_hash,
                },
                {"action": "add", "path": "src/new.py", "content": "NEW = True\n"},
                {"action": "delete", "path": "src/old.py", "expected_sha256": old_hash},
            ]
        )
    )

    assert "Successfully applied patch (3 file(s) changed)" in result
    assert "--- a/src/app.py" in result
    assert "+++ b/src/app.py" in result
    assert "--- /dev/null" in result
    assert "+++ b/src/new.py" in result
    assert "--- a/src/old.py" in result
    assert "+++ /dev/null" in result
    assert (src / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (src / "new.py").read_text(encoding="utf-8") == "NEW = True\n"
    assert not old_file.exists()


def test_apply_patch_validates_all_changes_before_writing(tmp_path):
    tool = ApplyPatchTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(
            changes=[
                {"action": "add", "path": "created.txt", "content": "created\n"},
                {
                    "action": "update",
                    "path": "missing.txt",
                    "old_text": "old",
                    "new_text": "new",
                },
            ]
        )
    )

    assert result == "Error: Change 2: file not found: missing.txt"
    assert not (tmp_path / "created.txt").exists()


def test_apply_patch_rejects_ambiguous_update(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(
            changes=[
                {
                    "action": "update",
                    "path": "notes.txt",
                    "old_text": "same",
                    "new_text": "changed",
                    "expected_sha256": _sha256("same\nsame\n"),
                }
            ]
        )
    )

    assert result == "Error: Change 1: old_text appears 2 times in notes.txt. Provide more context."
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


def test_apply_patch_blocks_protected_config_paths(tmp_path):
    tool = ApplyPatchTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(
            changes=[
                {"action": "add", "path": "opensprite.json", "content": "{}"},
            ]
        )
    )

    assert "configuration files" in result.lower()
    assert not (tmp_path / "opensprite.json").exists()


def test_write_file_requires_expected_sha256_when_overwriting(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("old\n", encoding="utf-8")
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(path="notes.txt", content="new\n"))

    assert "Stale-read guard failed for notes.txt" in result
    assert "expected_sha256 is required" in result
    assert target.read_text(encoding="utf-8") == "old\n"


def test_write_file_rejects_stale_expected_sha256(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("newer\n", encoding="utf-8")
    tool = WriteFileTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(path="notes.txt", content="replacement\n", expected_sha256="0" * 64)
    )

    assert "current SHA256" in result
    assert "Re-read the file before editing" in result
    assert target.read_text(encoding="utf-8") == "newer\n"


def test_edit_file_requires_expected_sha256(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("old\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(path="notes.txt", old_text="old", new_text="new"))

    assert "Stale-read guard failed for notes.txt" in result
    assert "expected_sha256 is required" in result
    assert target.read_text(encoding="utf-8") == "old\n"


def test_apply_patch_requires_expected_sha256_for_existing_update(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("old\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(
            changes=[
                {
                    "action": "update",
                    "path": "notes.txt",
                    "old_text": "old",
                    "new_text": "new",
                }
            ]
        )
    )

    assert "Error: Change 1: Stale-read guard failed for notes.txt" in result
    assert target.read_text(encoding="utf-8") == "old\n"


def test_apply_patch_rejects_stale_expected_sha256_before_writing_anything(tmp_path):
    existing = tmp_path / "existing.txt"
    existing.write_text("newer\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    result = asyncio.run(
        tool.execute(
            changes=[
                {"action": "add", "path": "created.txt", "content": "created\n"},
                {
                    "action": "update",
                    "path": "existing.txt",
                    "old_text": "newer",
                    "new_text": "changed",
                    "expected_sha256": "0" * 64,
                },
            ]
        )
    )

    assert "Error: Change 2: Stale-read guard failed for existing.txt" in result
    assert not (tmp_path / "created.txt").exists()
    assert existing.read_text(encoding="utf-8") == "newer\n"


def test_mutating_filesystem_tools_emit_file_change_records(tmp_path):
    recorded = []

    async def recorder(tool_name, changes):
        recorded.append((tool_name, changes))

    write_tool = WriteFileTool(workspace=tmp_path, file_change_recorder=recorder)
    edit_tool = EditFileTool(workspace=tmp_path, file_change_recorder=recorder)
    patch_tool = ApplyPatchTool(workspace=tmp_path, file_change_recorder=recorder)

    write_result = asyncio.run(write_tool.execute(path="notes.txt", content="hello\n"))
    edit_result = asyncio.run(
        edit_tool.execute(
            path="notes.txt",
            old_text="hello",
            new_text="hi",
            expected_sha256=_sha256("hello\n"),
        )
    )
    patch_result = asyncio.run(
        patch_tool.execute(
            changes=[
                {
                    "action": "delete",
                    "path": "notes.txt",
                    "expected_sha256": _sha256("hi\n"),
                }
            ]
        )
    )

    assert "Successfully wrote" in write_result
    assert "Successfully edited" in edit_result
    assert "Successfully applied patch" in patch_result
    assert [entry[0] for entry in recorded] == ["write_file", "edit_file", "apply_patch"]
    assert [entry[1][0]["action"] for entry in recorded] == ["add", "update", "delete"]
    assert recorded[0][1][0]["path"] == "notes.txt"
    assert recorded[0][1][0]["before_sha256"] is None
    assert recorded[0][1][0]["after_sha256"] == _sha256("hello\n")
    assert recorded[0][1][0]["before_content"] is None
    assert recorded[0][1][0]["after_content"] == "hello\n"
    assert recorded[1][1][0]["before_sha256"] == _sha256("hello\n")
    assert recorded[1][1][0]["after_sha256"] == _sha256("hi\n")
    assert recorded[1][1][0]["before_content"] == "hello\n"
    assert recorded[1][1][0]["after_content"] == "hi\n"
    assert recorded[2][1][0]["before_sha256"] == _sha256("hi\n")
    assert recorded[2][1][0]["after_sha256"] is None
    assert recorded[2][1][0]["before_content"] == "hi\n"
    assert recorded[2][1][0]["after_content"] is None
    assert "+++ /dev/null" in recorded[2][1][0]["diff"]
