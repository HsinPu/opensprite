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


def test_skills_loader_uses_personal_over_global_and_ignores_workspace_default(tmp_path):
    global_dir = tmp_path / "global-skills"
    personal_dir = tmp_path / "chat-skills"
    workspace_dir = tmp_path / "workspace"

    _write_skill(global_dir, "planner", "global planner", "Global body")
    _write_skill(global_dir, "global-only", "global only", "Global only body")
    _write_skill(personal_dir, "planner", "personal planner", "Personal body")
    _write_skill(workspace_dir / "skills", "workspace-only", "workspace only", "Workspace body")

    loader = SkillsLoader(workspace=workspace_dir, default_skills_dir=global_dir)

    names = [skill.name for skill in loader.get_skills(personal_dir)]

    assert names == ["planner", "global-only"]
    assert loader.load_skill_content("planner", personal_dir) == "Personal body"
    assert "workspace-only" not in loader.get_valid_skill_names(personal_dir)
    assert "workspace-only" not in loader.get_valid_skill_names()
