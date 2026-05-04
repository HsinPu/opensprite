from opensprite.context.file_builder import FileContextBuilder


def test_file_builder_includes_recent_summary_in_system_prompt(tmp_path):
    builder = FileContextBuilder(
        app_home=tmp_path / "home",
        bootstrap_dir=tmp_path / "bootstrap",
        memory_dir=tmp_path / "memory",
        tool_workspace=tmp_path / "workspace",
    )

    builder.recent_summary_store.write("telegram:room-1", "# Active Threads\n- current refactor")

    prompt = builder.build_system_prompt("telegram:room-1")

    assert "# Recent Summary" in prompt
    assert "Approx size:" in prompt
    assert "Keep this document concise; use search tools for detailed past transcripts." in prompt
    assert "current refactor" in prompt
