from pathlib import Path

from opensprite.agent.tool_registration import register_default_tools
from opensprite.config.schema import SearchConfig
from opensprite.tools.registry import ToolRegistry


async def _fake_run_subagent(task: str, prompt_type: str) -> str:
    return f"{prompt_type}:{task}"


def test_register_default_tools_includes_optional_skill_and_search_tools():
    registry = ToolRegistry()

    register_default_tools(
        registry,
        workspace_resolver=lambda: Path.cwd(),
        get_chat_id=lambda: "chat-1",
        run_subagent=_fake_run_subagent,
        skills_loader=object(),
        search_store=object(),
        search_config=SearchConfig(history_top_k=7, knowledge_top_k=9),
    )

    assert registry.tool_names == [
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "read_skill",
        "exec",
        "web_search",
        "web_fetch",
        "delegate",
        "search_history",
        "search_knowledge",
    ]


def test_register_default_tools_skips_optional_skill_and_search_tools_when_dependencies_missing():
    registry = ToolRegistry()

    register_default_tools(
        registry,
        workspace_resolver=lambda: Path.cwd(),
        get_chat_id=lambda: "chat-1",
        run_subagent=_fake_run_subagent,
    )

    assert registry.tool_names == [
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "exec",
        "web_search",
        "web_fetch",
        "delegate",
    ]
