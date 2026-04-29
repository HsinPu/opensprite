import asyncio
import json

from opensprite.skills import SkillsLoader
from opensprite.tools.skill_config import (
    MIN_SKILL_BODY_LEN,
    MIN_SKILL_DESCRIPTION_LEN,
    MIN_SKILL_DESCRIPTION_WORDS,
    ConfigureSkillTool,
)

# Meets fixed minimums for add/upsert validation.
_VALID_DESCRIPTION = (
    "Session-scoped helper: applies a repeatable workflow for tasks tied to this conversation only. "
    "Use when the user asks for the same multi-step process within this session workspace."
)
_VALID_BODY = (
    "# Instructions\n\n"
    "Do the thing with care. Follow project conventions and prefer small, focused edits.\n"
)


def test_configure_skill_lists_skills(tmp_path):
    skills_root = tmp_path / "home_skills"
    session_ws = tmp_path / "session_workspace"
    user_dir = session_ws / "skills"
    (user_dir / "alpha").mkdir(parents=True)
    (user_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: First skill\n---\n\n# Alpha\n",
        encoding="utf-8",
    )

    loader = SkillsLoader(default_skills_dir=skills_root)
    tool = ConfigureSkillTool(
        skills_loader=loader,
        workspace_resolver=lambda: session_ws,
    )

    result = asyncio.run(tool.execute(action="list"))
    payload = json.loads(result)

    assert "scope" not in payload
    assert payload["skills_dir"] == str(user_dir.resolve())
    assert "alpha" in payload["skills"]
    assert payload["skills"]["alpha"]["description"] == "First skill"


def test_configure_skill_add_upsert_and_get(tmp_path):
    ws = tmp_path / "workspace"
    loader = SkillsLoader(default_skills_dir=tmp_path / "global_skills")
    tool = ConfigureSkillTool(
        skills_loader=loader,
        workspace_resolver=lambda: ws,
    )

    created = asyncio.run(
        tool.execute(
            action="add",
            skill_name="my-skill",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
        )
    )
    assert "Added skill" in created

    duplicate = asyncio.run(
        tool.execute(
            action="add",
            skill_name="my-skill",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
        )
    )
    assert "already exists" in duplicate

    updated = asyncio.run(
        tool.execute(
            action="upsert",
            skill_name="my-skill",
            description=_VALID_DESCRIPTION,
            body=(
                "# Instructions\n\n"
                "Replaced body: follow conventions and verify results after each step in this session.\n"
            ),
        )
    )
    assert "Updated skill" in updated

    skill_file = ws / "skills" / "my-skill" / "SKILL.md"
    assert skill_file.is_file()

    got = asyncio.run(tool.execute(action="get", skill_name="my-skill"))
    payload = json.loads(got)
    assert "scope" not in payload
    assert payload["skill_name"] == "my-skill"
    assert "Replaced body" in payload["content"]


def test_configure_skill_remove(tmp_path):
    skills_root = tmp_path / "home_skills"
    session_ws = tmp_path / "session_workspace"
    skill_dir = session_ws / "skills" / "gone"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: gone\ndescription: x\n---\n\n# Gone\n",
        encoding="utf-8",
    )

    loader = SkillsLoader(default_skills_dir=skills_root)
    tool = ConfigureSkillTool(
        skills_loader=loader,
        workspace_resolver=lambda: session_ws,
    )

    result = asyncio.run(tool.execute(action="remove", skill_name="gone"))
    assert "Removed skill" in result
    assert not skill_dir.exists()


def test_configure_skill_rejects_invalid_skill_id(tmp_path):
    tool = ConfigureSkillTool(
        skills_loader=SkillsLoader(default_skills_dir=tmp_path / "s"),
        workspace_resolver=lambda: tmp_path / "ws",
    )
    out = asyncio.run(
        tool.execute(
            action="add",
            skill_name="My_Skill",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
        )
    )
    assert "lowercase ASCII" in out


def test_configure_skill_rejects_short_description(tmp_path):
    tool = ConfigureSkillTool(
        skills_loader=SkillsLoader(default_skills_dir=tmp_path / "s"),
        workspace_resolver=lambda: tmp_path / "ws",
    )
    out = asyncio.run(
        tool.execute(
            action="add",
            skill_name="ok-skill",
            description="too short",
            body=_VALID_BODY,
        )
    )
    assert str(MIN_SKILL_DESCRIPTION_LEN) in out


def test_configure_skill_rejects_short_body(tmp_path):
    tool = ConfigureSkillTool(
        skills_loader=SkillsLoader(default_skills_dir=tmp_path / "s"),
        workspace_resolver=lambda: tmp_path / "ws",
    )
    out = asyncio.run(
        tool.execute(
            action="add",
            skill_name="ok-skill",
            description=_VALID_DESCRIPTION,
            body="short",
        )
    )
    assert str(MIN_SKILL_BODY_LEN) in out


def test_configure_skill_rejects_too_few_english_words(tmp_path):
    tool = ConfigureSkillTool(
        skills_loader=SkillsLoader(default_skills_dir=tmp_path / "s"),
        workspace_resolver=lambda: tmp_path / "ws",
    )
    # Long enough in characters but only 10 repeated tokens.
    padded = " ".join(["supercalifragilistic"] * 10)
    out = asyncio.run(
        tool.execute(
            action="add",
            skill_name="ok-skill",
            description=padded,
            body=_VALID_BODY,
        )
    )
    assert str(MIN_SKILL_DESCRIPTION_WORDS) in out


def test_configure_skill_rejects_low_substance_glue_words(tmp_path):
    tool = ConfigureSkillTool(
        skills_loader=SkillsLoader(default_skills_dir=tmp_path / "s"),
        workspace_resolver=lambda: tmp_path / "ws",
    )
    glue = " ".join(
        ["the", "a", "an", "of", "to", "in", "for", "on", "at", "by", "and", "or", "if", "so", "is", "are"]
        * 8
    )
    assert len(glue) >= MIN_SKILL_DESCRIPTION_LEN
    out = asyncio.run(
        tool.execute(
            action="add",
            skill_name="ok-skill",
            description=glue,
            body=_VALID_BODY,
        )
    )
    assert "substantive" in out.lower()


def test_configure_skill_can_shadow_system_skill_id(tmp_path):
    """Session workspace skills may shadow a bundled system skill id."""
    skills_root = tmp_path / "home_skills"
    session_ws = tmp_path / "session_workspace"
    (skills_root / "memory").mkdir(parents=True)
    (skills_root / "memory" / "SKILL.md").write_text(
        "---\nname: memory\ndescription: system memory skill\n---\n\n# System\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(default_skills_dir=skills_root)
    tool = ConfigureSkillTool(skills_loader=loader, workspace_resolver=lambda: session_ws)
    out = asyncio.run(
        tool.execute(
            action="upsert",
            skill_name="memory",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
        )
    )
    assert "memory" in out and "Error" not in out
    user_skill = session_ws / "skills" / "memory" / "SKILL.md"
    assert user_skill.is_file()
    assert "System" not in user_skill.read_text(encoding="utf-8")


def test_configure_skill_rejects_repetitive_description(tmp_path):
    tool = ConfigureSkillTool(
        skills_loader=SkillsLoader(default_skills_dir=tmp_path / "s"),
        workspace_resolver=lambda: tmp_path / "ws",
    )
    padded = " ".join(["foobar"] * 22)
    out = asyncio.run(
        tool.execute(
            action="add",
            skill_name="ok-skill",
            description=padded,
            body=_VALID_BODY,
        )
    )
    assert "repetitive" in out.lower()
