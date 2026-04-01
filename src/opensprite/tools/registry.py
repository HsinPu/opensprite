"""Tool registry for managing available tools."""

from typing import Any

from .base import Tool


class ToolRegistry:
    """Registry for managing agent tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any] | None) -> str:
        """Execute a tool by name with given parameters."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        if params is None:
            return f"Error executing {name}: tool arguments are missing"

        if not isinstance(params, dict):
            return f"Error executing {name}: tool arguments must be an object, got {type(params).__name__}"
        
        try:
            result = await tool.execute(**params)
            if result is None:
                return f"Error executing {name}: tool returned no result"
            return str(result)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())
