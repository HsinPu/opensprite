import asyncio
import sys
from types import ModuleType, SimpleNamespace

from opensprite.tools.mcp import MCPToolWrapper


class _TextContent:
    def __init__(self, text: str):
        self.text = text


def _install_fake_mcp(monkeypatch):
    mod = ModuleType("mcp")
    mod.types = SimpleNamespace(TextContent=_TextContent)
    monkeypatch.setitem(sys.modules, "mcp", mod)


def test_mcp_tool_wrapper_normalizes_nullable_schema_and_executes(monkeypatch):
    _install_fake_mcp(monkeypatch)

    async def call_tool(name, arguments):
        assert name == "echo"
        assert arguments == {"note": "hi"}
        return SimpleNamespace(content=[_TextContent("hello from mcp")])

    tool_def = SimpleNamespace(
        name="echo",
        description="Echo content",
        inputSchema={
            "type": "object",
            "properties": {
                "note": {"type": ["string", "null"]},
            },
        },
    )
    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=call_tool), "demo", tool_def)

    result = asyncio.run(wrapper.execute(note="hi"))

    assert wrapper.name == "mcp_demo_echo"
    assert wrapper.parameters["properties"]["note"]["type"] == "string"
    assert wrapper.parameters["properties"]["note"]["nullable"] is True
    assert result == "hello from mcp"


def test_mcp_tool_wrapper_returns_timeout_message(monkeypatch):
    _install_fake_mcp(monkeypatch)

    async def call_tool(name, arguments):
        await asyncio.sleep(0.05)
        return SimpleNamespace(content=[])

    tool_def = SimpleNamespace(
        name="slow",
        description="Slow tool",
        inputSchema={"type": "object", "properties": {}},
    )
    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=call_tool), "demo", tool_def, tool_timeout=0.01)

    result = asyncio.run(wrapper.execute())

    assert result == "(MCP tool call timed out after 0.01s)"
