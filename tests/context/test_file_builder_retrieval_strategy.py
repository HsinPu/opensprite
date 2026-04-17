from opensprite.context.file_builder import FileContextBuilder
from opensprite.subagent_prompts import ALL_SUBAGENTS


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


def test_file_builder_includes_available_subagents_in_system_prompt(tmp_path):
    builder = FileContextBuilder(
        app_home=tmp_path / "home",
        bootstrap_dir=tmp_path / "bootstrap",
        memory_dir=tmp_path / "memory",
        tool_workspace=tmp_path / "workspace",
    )

    prompt = builder.build_system_prompt("telegram:room-1")

    assert "# Available Subagents" in prompt
    assert "Use `delegate` when a focused subproblem would benefit from a dedicated prompt." in prompt
    first_name, first_description = next(iter(ALL_SUBAGENTS.items()))
    assert f"- `{first_name}`: {first_description}" in prompt
