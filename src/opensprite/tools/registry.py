"""Tool registry for managing available tools."""

from typing import Any

from .base import Tool
from .permissions import ToolPermissionPolicy


class ToolRegistry:
    """Registry for managing agent tools."""

    def __init__(self, permission_policy: ToolPermissionPolicy | None = None):
        self._tools: dict[str, Tool] = {}
        self._permission_policy = permission_policy or ToolPermissionPolicy.allow_all()

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def set_permission_policy(self, permission_policy: ToolPermissionPolicy) -> None:
        """Set the permission policy used for tool exposure and execution."""
        self._permission_policy = permission_policy

    @property
    def permission_policy(self) -> ToolPermissionPolicy:
        """Return the active permission policy."""
        return self._permission_policy

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def unregister(self, name: str) -> Tool | None:
        """Remove one registered tool by name."""
        return self._tools.pop(name, None)

    def filtered(
        self,
        *,
        include_names: set[str] | frozenset[str] | None = None,
        exclude_names: set[str] | frozenset[str] | None = None,
        permission_policy: ToolPermissionPolicy | None = None,
    ) -> "ToolRegistry":
        """Return a registry copy filtered by included/excluded tool names."""
        filtered_registry = ToolRegistry(permission_policy=permission_policy or self._permission_policy)
        included = include_names
        excluded = exclude_names or set()
        for name, tool in self._tools.items():
            if included is not None and name not in included:
                continue
            if name in excluded:
                continue
            filtered_registry.register(tool)
        return filtered_registry

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [
            tool.to_schema()
            for tool in self._tools.values()
            if self._permission_policy.is_tool_exposed(tool.name)
        ]

    async def execute(self, name: str, params: Any) -> str:
        """Execute a tool by name with given parameters."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        decision = self._permission_policy.check(name, params)
        if not decision.allowed:
            return f"Error: Tool '{name}' blocked by permission policy: {decision.reason}."

        return await tool.execute_validated(params)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return [
            name
            for name in self._tools.keys()
            if self._permission_policy.is_tool_exposed(name)
        ]
