from agent_test_helpers import make_agent_loop

from opensprite.config.schema import Config
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.web_research import WebResearchTool
from opensprite.tools.web_search import WebSearchTool


def test_agent_reload_web_search_from_config_updates_registered_tools(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    agent = make_agent_loop(tmp_path / "workspace", tools=ToolRegistry(), config_path=config_path)

    config = Config.from_json(config_path)
    config.tools.web_search.provider = "jina"
    config.tools.web_search.freshness = "week"
    config.tools.web_search.max_results = 7
    config.tools.web_search.duckduckgo_max_pages = 3
    config.tools.web_search.searxng_max_pages = 4
    config.tools.web_search.searxng_engines = ["google", "bing"]
    config.tools.web_search.searxng_categories = ["general", "news"]
    config.tools.web_search.proxy = "http://proxy.local:8080"
    config.tools.web_search.jina_api_key = "jina-secret"

    payload = agent.reload_web_search_from_config(config)

    web_search_tool = agent.tools.get("web_search")
    web_research_tool = agent.tools.get("web_research")
    assert payload == {
        "provider": "jina",
        "freshness": "week",
        "max_results": 7,
        "searxng_max_pages": 4,
        "searxng_engines": ["google", "bing"],
        "searxng_categories": ["general", "news"],
        "tool_updated": True,
        "research_tool_updated": True,
    }
    assert agent.tools_config.web_search.provider == "jina"
    assert isinstance(web_search_tool, WebSearchTool)
    assert web_search_tool.provider == "jina"
    assert web_search_tool.max_results == 7
    assert web_search_tool.duckduckgo_max_pages == 3
    assert web_search_tool.searxng_max_pages == 4
    assert web_search_tool.searxng_engines == ["google", "bing"]
    assert web_search_tool.searxng_categories == ["general", "news"]
    assert web_search_tool.proxy == "http://proxy.local:8080"
    assert web_search_tool.jina_api_key == "jina-secret"
    assert isinstance(web_research_tool, WebResearchTool)
    assert web_research_tool.search_config.provider == "jina"
    assert web_research_tool.search_tool.provider == "jina"
    assert web_research_tool.search_tool.max_results == 7
