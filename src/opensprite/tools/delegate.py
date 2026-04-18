"""Delegate Tool - 委派子代理執行任務"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN
from ..subagent_prompts import get_all_subagents


def _build_description(subagents: dict[str, str]) -> str:
    """動態生成 delegate tool 的 description"""
    subagent_list = "\n".join([f"- {name}: {desc}" for name, desc in subagents.items()])
    return f"""委派任務給子代理執行。

可用子代理類型：
{subagent_list}

子代理會自行載入對應 prompt 並組合執行時 context。
"""


class DelegateTool(Tool):
    """Delegate tool - 同步等待子代理完成"""

    name = "delegate"

    def __init__(
        self,
        run_subagent: Callable[[str, str], Awaitable[str]],
        *,
        app_home: Path | None = None,
    ):
        self._run_subagent = run_subagent
        self._app_home = app_home

    @property
    def description(self) -> str:
        return _build_description(get_all_subagents(self._app_home))

    @property
    def parameters(self) -> dict[str, Any]:
        subs = get_all_subagents(self._app_home)
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要委派的工作描述", "pattern": NON_EMPTY_STRING_PATTERN},
                "prompt_type": {
                    "type": "string",
                    "description": f"子代理類型，可選: {list(subs.keys())}",
                    "default": "writer",
                },
            },
            "required": ["task"],
        }

    async def _execute(self, task: str, prompt_type: str = "writer", **kwargs: Any) -> str:
        return await self._run_subagent(task, prompt_type)
