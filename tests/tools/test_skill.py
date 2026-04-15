import asyncio
from pathlib import Path

from opensprite.skills import SkillsLoader
from opensprite.tools.skill import ReadSkillTool


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_read_skill_tool_prefers_personal_skill_dir(tmp_path):
    global_dir = tmp_path / "global-skills"
    personal_dir = tmp_path / "chat-skills"
    _write_skill(global_dir, "planner", "global planner", "Global body")
    _write_skill(personal_dir, "planner", "personal planner", "Personal body")

    loader = SkillsLoader(default_skills_dir=global_dir)
    tool = ReadSkillTool(loader, personal_skills_dir_resolver=lambda: personal_dir)

    result = asyncio.run(tool.execute(skill_name="planner"))

    assert result == "Personal body"
