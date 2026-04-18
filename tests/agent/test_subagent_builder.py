from pathlib import Path

from opensprite.agent.subagent_builder import SubagentMessageBuilder
from opensprite.skills import SkillsLoader


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_subagent_builder_includes_skill_summary_for_workspace(tmp_path):
    global_dir = tmp_path / "global-skills"
    workspace = tmp_path / "workspace"
    personal_dir = workspace / "skills"

    _write_skill(global_dir, "global-skill", "global description", "Global body")
    _write_skill(personal_dir, "chat-skill", "chat description", "Chat body")

    builder = SubagentMessageBuilder(skills_loader=SkillsLoader(default_skills_dir=global_dir))
    prompt = builder.build_system_prompt(
        prompt_type="implementer", workspace=workspace, app_home=tmp_path / "home"
    )

    assert "If a listed skill is relevant, read it before using other non-trivial tools" in prompt
    assert "Available skills (use read_skill tool to read instructions):" in prompt
    assert "chat-skill: chat description" in prompt
    assert "global-skill: global description" in prompt
