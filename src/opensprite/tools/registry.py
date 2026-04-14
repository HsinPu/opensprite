"""Tool registry for managing available tools."""

from typing import Any

from .base import Tool
from .validation import format_param_preview, validate_required_tool_params
from ..utils.log import logger


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

    def filtered(self, *, exclude_names: set[str] | None = None) -> "ToolRegistry":
        """Return a registry copy filtered by excluded tool names."""
        filtered_registry = ToolRegistry()
        excluded = exclude_names or set()
        for name, tool in self._tools.items():
            if name in excluded:
                continue
            filtered_registry.register(tool)
        return filtered_registry

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        validation_error = validate_required_tool_params(name, params)
        if validation_error is not None:
            logger.warning(
                "tool.validation-failed | name={} params={} error={}",
                name,
                format_param_preview(params),
                validation_error,
            )
            return validation_error
        
        try:
            result = await tool.execute(**params)
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}"

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())
