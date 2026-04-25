import asyncio

from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry


class EchoTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs):
        return f"ran:{self.name}"


def test_registry_hides_and_blocks_denied_tools():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(denied_tools=["exec"])
    )
    registry.register(EchoTool("read_file"))
    registry.register(EchoTool("exec"))

    assert registry.tool_names == ["read_file"]
    definitions = registry.get_definitions()
    assert [item["function"]["name"] for item in definitions] == ["read_file"]

    result = asyncio.run(registry.execute("exec", {}))

    assert result == "Error: Tool 'exec' blocked by permission policy: tool 'exec' is listed in denied_tools."


def test_registry_blocks_denied_risk_levels():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(denied_risk_levels=["network"])
    )
    registry.register(EchoTool("web_fetch"))

    assert registry.tool_names == []
    result = asyncio.run(registry.execute("web_fetch", {}))

    assert result == "Error: Tool 'web_fetch' blocked by permission policy: risk level(s) denied: network."


def test_registry_restricts_allowed_tools_by_glob():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(allowed_tools=["read_*", "grep_files"])
    )
    registry.register(EchoTool("read_file"))
    registry.register(EchoTool("grep_files"))
    registry.register(EchoTool("write_file"))

    assert registry.tool_names == ["read_file", "grep_files"]
    assert asyncio.run(registry.execute("write_file", {})).startswith(
        "Error: Tool 'write_file' blocked by permission policy: tool 'write_file' is not in allowed_tools."
    )


def test_approval_required_policy_blocks_until_external_approval_layer_exists():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_required_tools=["apply_patch"])
    )
    registry.register(EchoTool("apply_patch"))

    result = asyncio.run(registry.execute("apply_patch", {}))

    assert result == "Error: Tool 'apply_patch' blocked by permission policy: tool 'apply_patch' requires user approval."
