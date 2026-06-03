"""Batch execution tool for safe parallel read-only tool calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, TYPE_CHECKING

from ..tool_names import BATCH_TOOL_NAME
from .base import Tool
from .result_status import classify_tool_result_status, tool_error_result
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
    }
)
_MAX_BATCH_CALLS = 8
_MAX_BATCH_RESULT_CHARS = 2_000
_MAX_BATCH_OUTPUT_CHARS = 16_000


class BatchTool(Tool):
    """Run multiple safe read-only tools concurrently."""

    name = BATCH_TOOL_NAME
    description = (
        "Run up to 8 read-only tool calls concurrently and return their results together. "
        "Allowed child tools: read_file, list_dir, glob_files, grep_files, read_skill, "
        "search_history. Do not use for write, edit, exec, delegate, cron, media, or config tools. "
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
    def _truncate_result(result: str, max_chars: int = _MAX_BATCH_RESULT_CHARS) -> str:
        if len(result) <= max_chars:
            return result
        head = max_chars // 2
        tail = max_chars - head
        return (
            result[:head].rstrip()
            + f"\n... (result truncated, total {len(result)} chars) ...\n"
            + result[-tail:].lstrip()
        )

    @classmethod
    def _json_output(cls, payload: dict[str, Any]) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= _MAX_BATCH_OUTPUT_CHARS:
            return text
        compact = dict(payload)
        compact["results"] = [
            {
                **item,
                "result": cls._truncate_result(str(item.get("result") or ""), max_chars=500),
            }
            for item in payload.get("results", [])
            if isinstance(item, dict)
        ]
        text = json.dumps(compact, ensure_ascii=False)
        if len(text) <= _MAX_BATCH_OUTPUT_CHARS:
            return text
        compact["results"] = [
            {
                **item,
                "result": cls._truncate_result(str(item.get("result") or ""), max_chars=200),
            }
            for item in compact.get("results", [])
            if isinstance(item, dict)
        ]
        return json.dumps(compact, ensure_ascii=False)

    async def _run_one(self, index: int, call: dict[str, Any]) -> tuple[int, str, str]:
        tool_name = str(call.get("tool") or "")
        arguments = call.get("arguments")
        if tool_name not in READ_ONLY_BATCH_TOOLS:
            return index, tool_name or "<missing>", tool_error_result(
                f"batch cannot run non-read-only tool '{tool_name}'.",
                error_type="ToolValidationError",
                category="invalid_arguments",
                invalid_arguments=True,
                metadata={"tool_name": "batch"},
            )
        if not isinstance(arguments, dict):
            return index, tool_name, tool_error_result(
                "batch child arguments must be an object.",
                error_type="ToolValidationError",
                category="invalid_arguments",
                invalid_arguments=True,
                metadata={"tool_name": "batch"},
            )
        result = await self._registry_resolver().execute(tool_name, arguments)
        return index, tool_name, result

    async def _execute(self, **kwargs: Any) -> str:
        calls = kwargs["calls"]
        if not calls:
            return tool_error_result(
                "calls must contain at least one child tool call.",
                error_type="ToolValidationError",
                category="invalid_arguments",
                invalid_arguments=True,
                metadata={"tool_name": "batch"},
            )
        if len(calls) > _MAX_BATCH_CALLS:
            return tool_error_result(
                f"batch supports at most {_MAX_BATCH_CALLS} calls.",
                error_type="ToolValidationError",
                category="invalid_arguments",
                invalid_arguments=True,
                metadata={"tool_name": "batch"},
            )

        tasks = [self._run_one(index, call) for index, call in enumerate(calls, start=1)]
        results = await asyncio.gather(*tasks)
        results.sort(key=lambda item: item[0])

        child_results = []
        for index, tool_name, result in results:
            status = classify_tool_result_status(result)
            child_results.append(
                {
                    "index": index,
                    "tool": tool_name,
                    "ok": status.ok,
                    "error_type": status.error_type,
                    "category": status.category,
                    "result": self._truncate_result(str(result)),
                }
            )
        failed = sum(1 for item in child_results if not item["ok"])
        summary = f"Batch completed: {len(results)} call(s), {failed} failed."
        payload: dict[str, Any] = {
            "type": "batch",
            "ok": failed == 0,
            "summary": summary,
            "total": len(results),
            "failed": failed,
            "results": child_results,
        }
        if failed:
            payload.update(
                {
                    "error": summary,
                    "error_type": "ToolFailure",
                    "category": "batch_failure",
                }
            )
        return self._json_output(payload)
