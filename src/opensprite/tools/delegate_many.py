"""Parallel delegation tool for safe read-only or research subagents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from ..agent.subagent_policy import supports_parallel_delegation
from ..subagent_prompts import get_all_subagents
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN

MAX_PARALLEL_DELEGATED_TASKS = 4
DEFAULT_MAX_PARALLEL = 2


def _eligible_subagents(app_home: Path | None, session_workspace: Path | None) -> dict[str, str]:
    subagents = get_all_subagents(app_home, session_workspace=session_workspace)
    return {
        name: description
        for name, description in subagents.items()
        if supports_parallel_delegation(name, app_home=app_home, session_workspace=session_workspace)
    }


def _build_description(subagents: dict[str, str]) -> str:
    subagent_lines = "\n".join(f"- {name}: {description}" for name, description in subagents.items()) or "- (none)"
    return f"""平行委派多個子代理任務。

只允許 read-only 或 research 類型的子代理平行執行；不能用於 implementer、debugger、test-writer 等可寫檔或執行命令的子代理。
每個 task 都會建立自己的 child task session 與 child run，並在 parent trace 中留下各自的子代理事件。
這個工具只支援新任務 fan-out，不支援 task_id 續跑；若要續跑單一既有 child task，請改用 `delegate`。

可用平行子代理類型：
{subagent_lines}
"""


class DelegateManyTool(Tool):
    """Run multiple read-only or research subagents concurrently."""

    name = "delegate_many"

    def __init__(
        self,
        run_subagents_many: Callable[[list[dict[str, Any]], int | None], Awaitable[str]],
        *,
        app_home: Path | None = None,
        workspace_resolver: Callable[[], Path] | None = None,
    ):
        self._run_subagents_many = run_subagents_many
        self._app_home = app_home
        self._workspace_resolver = workspace_resolver

    def _subagents_for_current_session(self) -> dict[str, str]:
        session_workspace = self._workspace_resolver() if self._workspace_resolver is not None else None
        return _eligible_subagents(self._app_home, session_workspace)

    @property
    def description(self) -> str:
        return _build_description(self._subagents_for_current_session())

    @property
    def parameters(self) -> dict[str, Any]:
        subagents = sorted(self._subagents_for_current_session())
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": (
                        f"Required. 1 to {MAX_PARALLEL_DELEGATED_TASKS} new child tasks to run in parallel. "
                        "Each task must explicitly set one eligible read-only or research subagent id."
                    ),
                    "minItems": 1,
                    "maxItems": MAX_PARALLEL_DELEGATED_TASKS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "Required. The focused child task description.",
                                "pattern": NON_EMPTY_STRING_PATTERN,
                            },
                            "prompt_type": {
                                "type": "string",
                                "description": f"Required. Eligible read-only or research subagent id: {subagents}",
                            },
                        },
                        "required": ["task", "prompt_type"],
                    },
                },
                "max_parallel": {
                    "type": "integer",
                    "description": (
                        f"Optional. Concurrency cap from 1 to {MAX_PARALLEL_DELEGATED_TASKS}. "
                        f"Defaults to {DEFAULT_MAX_PARALLEL}."
                    ),
                    "minimum": 1,
                    "maximum": MAX_PARALLEL_DELEGATED_TASKS,
                },
            },
            "required": ["tasks"],
        }

    async def _execute(
        self,
        tasks: list[dict[str, Any]],
        max_parallel: int | None = None,
        **kwargs: Any,
    ) -> str:
        return await self._run_subagents_many(tasks, max_parallel)
