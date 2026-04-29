from pathlib import Path

from opensprite.skills import SkillsLoader


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                body,
            ]
        ),
        encoding="utf-8",
    )


def test_skills_loader_session_skills_override_system(tmp_path):
    skills_root = tmp_path / "sr"
    session_skills = tmp_path / "session_ws" / "skills"
    (skills_root / "shared").mkdir(parents=True)
    (skills_root / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: from system\n---\n\nsystem body\n",
        encoding="utf-8",
    )
    (session_skills / "shared").mkdir(parents=True)
    (session_skills / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: from session\n---\n\nsession body\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(default_skills_dir=skills_root)
    assert loader.load_skill_content("shared", session_skills) == "session body"
    names = [s.name for s in loader.get_skills(session_skills)]
    assert names == ["shared"]
    assert loader.get_skills(session_skills)[0].description == "from session"


def test_skills_loader_uses_personal_over_system(tmp_path):
    skills_root = tmp_path / "home_skills"
    personal_skills = tmp_path / "session_ws" / "skills"

    _write_skill(skills_root, "planner", "system planner", "System body")
    _write_skill(skills_root, "system-only", "system only", "System only body")
    _write_skill(personal_skills, "planner", "session planner", "Session body")

    loader = SkillsLoader(default_skills_dir=skills_root)

    names = [skill.name for skill in loader.get_skills(personal_skills)]

    assert names == ["planner", "system-only"]
    assert loader.load_skill_content("planner", personal_skills) == "Session body"
    assert "system-only" in loader.get_valid_skill_names(personal_skills)
