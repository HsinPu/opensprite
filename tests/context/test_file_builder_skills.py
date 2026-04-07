from opensprite.context.file_builder import FileContextBuilder


def _write_skill(root, name: str, description: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_file_builder_includes_personal_skill_summary_for_chat(tmp_path):
    app_home = tmp_path / "home"
    bootstrap_dir = tmp_path / "bootstrap"
    memory_dir = tmp_path / "memory"
    workspace_root = tmp_path / "workspace"
    global_dir = tmp_path / "global-skills"

    _write_skill(global_dir, "planner", "global planner", "Global body")

    builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=bootstrap_dir,
        memory_dir=memory_dir,
        tool_workspace=workspace_root,
        default_skills_dir=global_dir,
    )

    personal_dir = builder.get_chat_skills_dir("telegram:room-1")
    _write_skill(personal_dir, "planner", "personal planner", "Personal body")
    _write_skill(personal_dir, "chat-only", "chat only", "Chat body")

    prompt = builder.build_system_prompt("telegram:room-1")

    assert "planner: personal planner" in prompt
    assert "chat-only: chat only" in prompt
    assert "global planner" not in prompt
