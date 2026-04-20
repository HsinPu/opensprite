"""write_file / edit_file refuse paths under ~/.opensprite/skills/ (monkeypatched in tests)."""

import asyncio

from opensprite.tools.filesystem import EditFileTool, WriteFileTool


def test_write_file_allows_session_workspace_skill_subdir(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)
    out = asyncio.run(
        tool.execute(path="skills/custom-skill/notes.md", content="hello")
    )
    assert "Successfully wrote" in out
    assert (tmp_path / "skills" / "custom-skill" / "notes.md").read_text() == "hello"


def test_write_file_blocks_under_app_skills_dir(tmp_path, monkeypatch):
    app_skills = tmp_path / "opensprite_skills"
    monkeypatch.setattr("opensprite.context.paths.get_skills_dir", lambda app_home=None: app_skills)
    app_skills.mkdir(parents=True)

    tool = WriteFileTool(workspace=app_skills)
    out = asyncio.run(
        tool.execute(path="memory/SKILL.md", content="x")
    )
    assert ".opensprite/skills" in out or "Cannot modify" in out
    assert not (app_skills / "memory" / "SKILL.md").exists()


def test_write_file_allows_skills_memory_in_session_workspace(tmp_path, monkeypatch):
    """Session workspace skills/memory is not the app-home bundled tree."""
    app_skills = tmp_path / "opensprite_skills"
    monkeypatch.setattr("opensprite.context.paths.get_skills_dir", lambda app_home=None: app_skills)
    app_skills.mkdir(parents=True)

    session = tmp_path / "chat_ws"
    tool = WriteFileTool(workspace=session)
    out = asyncio.run(
        tool.execute(path="skills/memory/SKILL.md", content="---\nname: memory\n---\n\nbody\n")
    )
    assert "Successfully wrote" in out


def test_edit_file_blocks_under_app_skills_dir(tmp_path, monkeypatch):
    app_skills = tmp_path / "opensprite_skills"
    monkeypatch.setattr("opensprite.context.paths.get_skills_dir", lambda app_home=None: app_skills)
    app_skills.mkdir(parents=True)
    mem = app_skills / "foo" / "SKILL.md"
    mem.parent.mkdir(parents=True)
    mem.write_text("old", encoding="utf-8")

    tool = EditFileTool(workspace=app_skills)
    out = asyncio.run(
        tool.execute(path="foo/SKILL.md", old_text="old", new_text="new")
    )
    assert ".opensprite/skills" in out or "Cannot modify" in out
    assert mem.read_text() == "old"
