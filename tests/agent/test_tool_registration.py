from pathlib import Path

from opensprite.agent.tool_registration import register_default_tools
from opensprite.config.schema import SearchConfig, ToolsConfig
from opensprite.skills import SkillsLoader
from opensprite.tools.cron import CronTool
from opensprite.tools.mcp_config import ConfigureMCPTool
from opensprite.tools.process import ProcessTool
from opensprite.tools.skill_config import ConfigureSkillTool
from opensprite.tools.subagent_config import ConfigureSubagentTool
from opensprite.tools.shell import ExecTool
from opensprite.tools.search import SearchKnowledgeTool
from opensprite.tools.web_fetch import WebFetchTool
from opensprite.tools.web_search import WebSearchTool
from opensprite.tools.outbound_media import SendMediaTool
from opensprite.tools.registry import ToolRegistry


async def _fake_run_subagent(task: str, prompt_type: str) -> str:
    return f"{prompt_type}:{task}"


async def _fake_reload_mcp() -> str:
    return "reloaded"


class FakeSearchStore:
    async def search_history(self, chat_id: str, query: str, limit: int = 5):
        return []

    async def search_knowledge(self, chat_id: str, query: str, limit: int = 5):
        return []


def test_register_default_tools_includes_optional_skill_and_search_tools(tmp_path):
    registry = ToolRegistry()

    register_default_tools(
        registry,
        workspace_resolver=lambda: Path.cwd(),
        get_chat_id=lambda: "chat-1",
        run_subagent=_fake_run_subagent,
        config_path_resolver=lambda: Path.cwd() / "opensprite.json",
        reload_mcp=_fake_reload_mcp,
        skills_loader=SkillsLoader(default_skills_dir=tmp_path / "skills"),
        search_store=FakeSearchStore(),
        search_config=SearchConfig(history_top_k=7, knowledge_top_k=9),
    )

    assert registry.tool_names == [
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "read_skill",
        "configure_skill",
        "configure_mcp",
        "configure_subagent",
        "exec",
        "process",
        "web_search",
        "web_fetch",
        "analyze_image",
        "ocr_image",
        "transcribe_audio",
        "analyze_video",
        "send_media",
        "delegate",
        "search_history",
        "search_knowledge",
        "cron",
    ]
    assert isinstance(registry.get("configure_skill"), ConfigureSkillTool)
    assert isinstance(registry.get("configure_subagent"), ConfigureSubagentTool)
    assert isinstance(registry.get("send_media"), SendMediaTool)


def test_register_default_tools_skips_optional_skill_and_search_tools_when_dependencies_missing():
    registry = ToolRegistry()

    register_default_tools(
        registry,
        workspace_resolver=lambda: Path.cwd(),
        get_chat_id=lambda: "chat-1",
        run_subagent=_fake_run_subagent,
        config_path_resolver=lambda: Path.cwd() / "opensprite.json",
        reload_mcp=_fake_reload_mcp,
    )

    assert registry.tool_names == [
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "configure_mcp",
        "configure_subagent",
        "exec",
        "process",
        "web_search",
        "web_fetch",
        "analyze_image",
        "ocr_image",
        "transcribe_audio",
        "analyze_video",
        "send_media",
        "delegate",
        "cron",
    ]


def test_register_default_tools_applies_typed_tools_config_values():
    registry = ToolRegistry()

    register_default_tools(
        registry,
        workspace_resolver=lambda: Path.cwd(),
        get_chat_id=lambda: "chat-1",
        run_subagent=_fake_run_subagent,
        config_path_resolver=lambda: Path.cwd() / "opensprite.json",
        reload_mcp=_fake_reload_mcp,
        tools_config=ToolsConfig(
            **{
                "exec": {
                    "timeout": 12,
                    "notify_on_exit": False,
                    "notify_on_exit_empty_success": True,
                },
                "web_search": {"provider": "jina", "max_results": 7},
                "web_fetch": {
                    "max_chars": 1234,
                    "max_response_size": 2048,
                    "timeout": 9,
                    "prefer_trafilatura": False,
                    "firecrawl_api_key": "firecrawl-key",
                },
            }
        ),
    )

    exec_tool = registry.get("exec")
    process_tool = registry.get("process")
    web_search_tool = registry.get("web_search")
    web_fetch_tool = registry.get("web_fetch")
    cron_tool = registry.get("cron")
    configure_mcp_tool = registry.get("configure_mcp")

    assert isinstance(exec_tool, ExecTool)
    assert isinstance(process_tool, ProcessTool)
    assert isinstance(cron_tool, CronTool)
    assert isinstance(configure_mcp_tool, ConfigureMCPTool)
    assert isinstance(web_search_tool, WebSearchTool)
    assert isinstance(web_fetch_tool, WebFetchTool)
    assert exec_tool.timeout == 12
    assert exec_tool.notify_on_exit is False
    assert exec_tool.notify_on_exit_empty_success is True
    assert "UTC" in cron_tool.description
    assert web_search_tool.provider == "jina"
    assert web_search_tool.max_results == 7
    assert web_fetch_tool.fetcher.max_chars == 1234
    assert web_fetch_tool.fetcher.max_response_size == 2048
    assert web_fetch_tool.fetcher.timeout == 9
    assert web_fetch_tool.fetcher.prefer_trafilatura is False
    assert web_fetch_tool.fetcher.firecrawl_api_key == "firecrawl-key"


def test_search_and_web_tools_describe_retrieval_preference():
    registry = ToolRegistry()

    register_default_tools(
        registry,
        workspace_resolver=lambda: Path.cwd(),
        get_chat_id=lambda: "chat-1",
        run_subagent=_fake_run_subagent,
        config_path_resolver=lambda: Path.cwd() / "opensprite.json",
        reload_mcp=_fake_reload_mcp,
        search_store=FakeSearchStore(),
        search_config=SearchConfig(history_top_k=7, knowledge_top_k=9),
    )

    web_search_tool = registry.get("web_search")
    web_fetch_tool = registry.get("web_fetch")
    search_knowledge_tool = registry.get("search_knowledge")

    assert isinstance(web_search_tool, WebSearchTool)
    assert isinstance(web_fetch_tool, WebFetchTool)
    assert isinstance(search_knowledge_tool, SearchKnowledgeTool)
    assert "prefer search_knowledge first" in web_search_tool.description.lower()
    assert "stored web_fetch results" in web_fetch_tool.description.lower()
    assert "prefer this before repeating web_search or web_fetch" in search_knowledge_tool.description.lower()


def test_register_default_tools_applies_cron_default_timezone_from_tools_config():
    registry = ToolRegistry()

    register_default_tools(
        registry,
        workspace_resolver=lambda: Path.cwd(),
        get_chat_id=lambda: "chat-1",
        run_subagent=_fake_run_subagent,
        config_path_resolver=lambda: Path.cwd() / "opensprite.json",
        reload_mcp=_fake_reload_mcp,
        tools_config=ToolsConfig(**{"cron": {"default_timezone": "Asia/Taipei"}}),
    )

    cron_tool = registry.get("cron")

    assert isinstance(cron_tool, CronTool)
    assert "Asia/Taipei" in cron_tool.description
