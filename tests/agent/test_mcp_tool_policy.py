from opensprite.agent.mcp_lifecycle import (
    is_mcp_tool_name,
    mcp_tool_display_name,
    mcp_tool_names,
    tool_warrants_progress_notice,
)


def test_mcp_tool_policy_classifies_mcp_tool_names():
    assert is_mcp_tool_name("mcp_demo_echo") is True
    assert is_mcp_tool_name("configure_mcp") is False
    assert is_mcp_tool_name("") is False


def test_mcp_tool_policy_formats_display_name():
    assert mcp_tool_display_name("mcp_demo_echo") == "demo_echo"
    assert mcp_tool_display_name("configure_mcp") == "configure_mcp"


def test_mcp_tool_policy_filters_sorted_tool_names():
    assert mcp_tool_names(["web_search", "mcp_z_tool", "mcp_a_tool"]) == [
        "mcp_a_tool",
        "mcp_z_tool",
    ]


def test_mcp_tool_policy_classifies_progress_notice_tools():
    assert tool_warrants_progress_notice("read_skill") is True
    assert tool_warrants_progress_notice("delegate") is True
    assert tool_warrants_progress_notice("delegate_many") is True
    assert tool_warrants_progress_notice("run_workflow") is True
    assert tool_warrants_progress_notice("mcp_demo_echo") is True
    assert tool_warrants_progress_notice("web_search") is False
