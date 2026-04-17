from opensprite.context.file_builder import FileContextBuilder


def test_file_builder_includes_retrieval_strategy_in_system_prompt(tmp_path):
    builder = FileContextBuilder(
        app_home=tmp_path / "home",
        bootstrap_dir=tmp_path / "bootstrap",
        memory_dir=tmp_path / "memory",
        tool_workspace=tmp_path / "workspace",
    )

    prompt = builder.build_system_prompt("telegram:room-1")

    assert "# Retrieval Strategy" in prompt
    assert "Prefer `search_knowledge` before repeating `web_search` or `web_fetch`" in prompt
    assert "If `search_knowledge` already returns a relevant `web_fetch` result" in prompt
