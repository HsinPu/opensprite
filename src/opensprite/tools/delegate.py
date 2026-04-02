"""Delegate Tool - 委派子代理執行任務"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .base import Tool
from ..subagent_prompts import ALL_SUBAGENTS


def _build_description(subagents: dict[str, str]) -> str:
    """動態生成 delegate tool 的 description"""
    subagent_list = "\n".join([f"- {name}: {desc}" for name, desc in subagents.items()])
    return f"""內部使用：將聚焦子任務交給專門 worker 完成，並直接回收結果。

可用子代理類型：
{subagent_list}

子代理會自行載入對應 prompt 並組合執行時 context。
不要對使用者提到委派、subagent 或內部 worker；直接用結果完成回覆。
不要加上「這是 subagent 寫的」這類來源說明。
"""


class DelegateTool(Tool):
    """Delegate tool - 同步等待子代理完成"""

    name = "delegate"

    def __init__(self, run_subagent: Callable[[str, str], Awaitable[str]]):
        self._run_subagent = run_subagent

    @property
    def description(self) -> str:
        return _build_description(ALL_SUBAGENTS)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要委派的工作描述"},
                "prompt_type": {
                    "type": "string",
                    "description": f"子代理類型，可選: {list(ALL_SUBAGENTS.keys())}",
                    "default": "writer",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, prompt_type: str = "writer", **kwargs: Any) -> str:
        return await self._run_subagent(task, prompt_type)
