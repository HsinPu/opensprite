"""Fixed orchestration workflow tool for common multi-subagent flows."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


def _build_description(workflows: dict[str, str]) -> str:
    lines = "\n".join(f"- {name}: {description}" for name, description in workflows.items()) or "- (none)"
    return (
        "Run one fixed multi-step workflow that orchestrates several subagents in a known order. "
        "Use this when the task already fits a standard pattern such as implement-then-review or research-then-outline. "
        "You can also resume a workflow from a specific step by providing `start_step`.\n\n"
        "Available workflows:\n"
        f"{lines}"
    )


class RunWorkflowTool(Tool):
    """Run one predefined multi-step workflow."""

    name = "run_workflow"

    def __init__(
        self,
        run_workflow: Callable[[str, str, str | None], Awaitable[str]],
        *,
        workflow_catalog_getter: Callable[[], dict[str, str]],
    ):
        self._run_workflow = run_workflow
        self._workflow_catalog_getter = workflow_catalog_getter

    @property
    def description(self) -> str:
        return _build_description(self._workflow_catalog_getter())

    @property
    def parameters(self) -> dict[str, Any]:
        workflows = sorted(self._workflow_catalog_getter())
        return {
            "type": "object",
            "properties": {
                "workflow": {
                    "type": "string",
                    "description": f"Required. Workflow id. Available: {workflows}",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "task": {
                    "type": "string",
                    "description": "Required. The objective or task description for the workflow.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "start_step": {
                    "type": "string",
                    "description": "Optional. Resume from this workflow step id instead of starting from the first step.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
            },
            "required": ["workflow", "task"],
        }

    async def _execute(self, workflow: str, task: str, start_step: str | None = None, **kwargs: Any) -> str:
        return await self._run_workflow(workflow, task, start_step)
