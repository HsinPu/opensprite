"""Tool base class for OpenSprite."""

from abc import ABC, abstractmethod
from typing import Any

from ..utils.log import logger
from .evidence import ToolEvidence, build_tool_evidence
from .result_status import tool_error_result
from .validation import format_param_preview, validate_tool_params


class Tool(ABC):
    """Abstract base class for agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        pass

    @property
    def risk_levels(self) -> frozenset[str] | None:
        """Optional risk metadata used by runtime permission policies."""
        return None

    @property
    def capability_groups(self) -> frozenset[str] | None:
        """Optional planner capability groups exposed by this tool."""
        return None

    async def execute(self, **kwargs: Any) -> str:
        """
        Validate and execute the tool with given parameters.

        Returns:
            String result of the tool execution.
        """
        return await self.execute_validated(kwargs)

    async def execute_validated(self, params: Any) -> str:
        """Execute pre-validated params or return a validation error."""
        validation_error = self.validate_params(params)
        if validation_error is not None:
            logger.warning(
                "tool.validation-failed | name={} params={} error={}",
                self.name,
                format_param_preview(self.sanitize_params_for_display(params)),
                validation_error,
            )
            return validation_error

        assert isinstance(params, dict)
        try:
            return await self._execute(**params)
        except Exception as e:
            return tool_error_result(
                str(e),
                error_type="ToolExecutionError",
                metadata={"tool_name": self.name},
            )

    def validate_params(self, params: Any) -> str | None:
        """Validate params against the tool schema before execution."""
        return validate_tool_params(self.name, self.parameters, params)

    def sanitize_params_for_display(self, params: Any) -> Any:
        """Return params safe for logs, approvals, and run trace displays."""
        return params

    def sanitize_input_delta_for_display(self, delta: str) -> str:
        """Return streamed tool-input chunks safe for run trace displays."""
        return delta

    def build_evidence(self, params: Any, result: str, *, ok: bool) -> ToolEvidence:
        """Return completion-check evidence for this tool execution."""
        safe_params = params if isinstance(params, dict) else {}
        return build_tool_evidence(self.name, safe_params, result, ok=ok)

    @abstractmethod
    async def _execute(self, **kwargs: Any) -> str:
        """Implement the tool's business logic after validation."""
        pass

    def to_schema(self) -> dict[str, Any]:
        """Convert tool to OpenAI function schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
