from opensprite.context.file_builder import FileContextBuilder


def test_file_builder_includes_active_workspace_agents_file(tmp_path):
    builder = FileContextBuilder(
        app_home=tmp_path / "home",
        bootstrap_dir=tmp_path / "bootstrap",
        memory_dir=tmp_path / "memory",
        tool_workspace=tmp_path / "workspace",
        default_skills_dir=tmp_path / "skills",
    )

    agents_path = builder.get_workspace_agents_path("telegram:room-1")
    agents_path.write_text("# Project Rules\n\n- Run focused tests first.\n", encoding="utf-8")

    prompt = builder.build_system_prompt("telegram:room-1")

    assert "# Workspace AGENTS.md" in prompt
    assert f"Loaded from: `{agents_path.resolve()}`" in prompt
    assert "- Run focused tests first." in prompt


def test_file_builder_uses_only_the_active_session_agents_file(tmp_path):
    builder = FileContextBuilder(
        app_home=tmp_path / "home",
        bootstrap_dir=tmp_path / "bootstrap",
        memory_dir=tmp_path / "memory",
        tool_workspace=tmp_path / "workspace",
        default_skills_dir=tmp_path / "skills",
    )

    builder.get_workspace_agents_path("telegram:room-1").write_text(
        "# Room One Rules\n\n- Use pytest.\n",
        encoding="utf-8",
    )
    builder.get_workspace_agents_path("telegram:room-2").write_text(
        "# Room Two Rules\n\n- Use npm.\n",
        encoding="utf-8",
    )

    prompt_one = builder.build_system_prompt("telegram:room-1")
    prompt_two = builder.build_system_prompt("telegram:room-2")

    assert "- Use pytest." in prompt_one
    assert "- Use npm." not in prompt_one
    assert "- Use npm." in prompt_two
    assert "- Use pytest." not in prompt_two
