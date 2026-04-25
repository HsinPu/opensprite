"""Batch execution tool for safe parallel read-only tool calls."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TYPE_CHECKING

from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN

if TYPE_CHECKING:
    from .registry import ToolRegistry


READ_ONLY_BATCH_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "glob_files",
        "grep_files",
        "read_skill",
        "search_history",
        "search_knowledge",
    }
)
_MAX_BATCH_CALLS = 8
_MAX_BATCH_RESULT_CHARS = 2_000
_MAX_BATCH_OUTPUT_CHARS = 16_000


class BatchTool(Tool):
    """Run multiple safe read-only tools concurrently."""

    name = "batch"
    description = (
        "Run up to 8 read-only tool calls concurrently and return their results together. "
        "Allowed child tools: read_file, list_dir, glob_files, grep_files, read_skill, "
        "search_history, search_knowledge. Do not use for write, edit, exec, delegate, cron, media, or config tools. "
        "Each child call still goes through normal validation and permission policy."
    )

    def __init__(self, registry_resolver: Callable[[], "ToolRegistry"]):
        self._registry_resolver = registry_resolver

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "description": f"Required. Up to {_MAX_BATCH_CALLS} read-only tool calls to run concurrently.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "enum": sorted(READ_ONLY_BATCH_TOOLS),
                                "pattern": NON_EMPTY_STRING_PATTERN,
                                "description": "Required. Read-only child tool name.",
                            },
                            "arguments": {
                                "type": "object",
                                "description": "Required. Arguments object for the child tool.",
                            },
                        },
                        "required": ["tool", "arguments"],
                    },
                }
            },
            "required": ["calls"],
        }

    @staticmethod
    def _truncate_result(result: str) -> str:
        if len(result) <= _MAX_BATCH_RESULT_CHARS:
            return result
        head = _MAX_BATCH_RESULT_CHARS // 2
        tail = _MAX_BATCH_RESULT_CHARS - head
        return (
            result[:head].rstrip()
            + f"\n... (result truncated, total {len(result)} chars) ...\n"
            + result[-tail:].lstrip()
        )

    @staticmethod
    def _truncate_output(output: str) -> str:
        if len(output) <= _MAX_BATCH_OUTPUT_CHARS:
            return output
        return output[:_MAX_BATCH_OUTPUT_CHARS].rstrip() + f"\n... (batch output truncated, total {len(output)} chars)"

    async def _run_one(self, index: int, call: dict[str, Any]) -> tuple[int, str, str]:
        tool_name = str(call.get("tool") or "")
        arguments = call.get("arguments")
        if tool_name not in READ_ONLY_BATCH_TOOLS:
            return index, tool_name or "<missing>", f"Error: batch cannot run non-read-only tool '{tool_name}'."
        if not isinstance(arguments, dict):
            return index, tool_name, "Error: batch child arguments must be an object."
        result = await self._registry_resolver().execute(tool_name, arguments)
        return index, tool_name, result

    async def _execute(self, **kwargs: Any) -> str:
        calls = kwargs["calls"]
        if not calls:
            return "Error: calls must contain at least one child tool call."
        if len(calls) > _MAX_BATCH_CALLS:
            return f"Error: batch supports at most {_MAX_BATCH_CALLS} calls."

        tasks = [self._run_one(index, call) for index, call in enumerate(calls, start=1)]
        results = await asyncio.gather(*tasks)
        results.sort(key=lambda item: item[0])

        failed = sum(1 for _, _, result in results if result.startswith("Error:"))
        lines = [
            f"Batch completed: {len(results)} call(s), {failed} failed.",
        ]
        for index, tool_name, result in results:
            lines.extend(
                [
                    "",
                    f"[{index}] {tool_name}",
                    self._truncate_result(str(result)),
                ]
            )
        return self._truncate_output("\n".join(lines))
