import asyncio

from opensprite.tools.base import Tool
from opensprite.tools.batch import BatchTool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry


class EchoTool(Tool):
    def __init__(self, name: str, prefix: str = "ok"):
        self._name = name
        self.prefix = prefix

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    async def _execute(self, value: str, **kwargs):
        return f"{self.prefix}:{value}"


class SlowReadTool(Tool):
    def __init__(self):
        self.active = 0
        self.max_active = 0

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "read"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    async def _execute(self, path: str, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return f"read:{path}"
        finally:
            self.active -= 1


def _registry_with_batch(permission_policy=None):
    registry = ToolRegistry(permission_policy=permission_policy)
    registry.register(EchoTool("read_file", "read"))
    registry.register(EchoTool("grep_files", "grep"))
    registry.register(EchoTool("write_file", "write"))
    registry.register(BatchTool(lambda: registry))
    return registry


def test_batch_runs_read_only_calls_and_preserves_order():
    registry = _registry_with_batch()

    result = asyncio.run(
        registry.execute(
            "batch",
            {
                "calls": [
                    {"tool": "grep_files", "arguments": {"value": "needle"}},
                    {"tool": "read_file", "arguments": {"value": "notes.txt"}},
                ]
            },
        )
    )

    assert result.startswith("Batch completed: 2 call(s), 0 failed.")
    assert "[1] grep_files\ngrep:needle" in result
    assert "[2] read_file\nread:notes.txt" in result


def test_batch_rejects_non_read_only_child_tools():
    registry = _registry_with_batch()

    result = asyncio.run(
        registry.execute(
            "batch",
            {"calls": [{"tool": "write_file", "arguments": {"value": "x"}}]},
        )
    )

    assert result.startswith("Error: Invalid arguments for batch:")
    assert "calls[0].tool must be one of" in result
    assert "write:x" not in result


def test_batch_child_calls_still_follow_permission_policy():
    registry = _registry_with_batch(
        ToolPermissionPolicy(denied_tools=["read_file"])
    )

    result = asyncio.run(
        registry.execute(
            "batch",
            {"calls": [{"tool": "read_file", "arguments": {"value": "notes.txt"}}]},
        )
    )

    assert "Batch completed: 1 call(s), 1 failed." in result
    assert "Tool 'read_file' blocked by permission policy" in result


def test_batch_enforces_call_limit():
    registry = _registry_with_batch()
    calls = [
        {"tool": "read_file", "arguments": {"value": f"file-{index}"}}
        for index in range(9)
    ]

    result = asyncio.run(registry.execute("batch", {"calls": calls}))

    assert result == "Error: batch supports at most 8 calls."


def test_batch_executes_children_concurrently():
    registry = ToolRegistry()
    slow_tool = SlowReadTool()
    registry.register(slow_tool)
    registry.register(BatchTool(lambda: registry))

    result = asyncio.run(
        registry.execute(
            "batch",
            {
                "calls": [
                    {"tool": "read_file", "arguments": {"path": "a.txt"}},
                    {"tool": "read_file", "arguments": {"path": "b.txt"}},
                ]
            },
        )
    )

    assert "Batch completed: 2 call(s), 0 failed." in result
    assert slow_tool.max_active == 2


def test_batch_truncates_large_child_results():
    registry = ToolRegistry()
    registry.register(EchoTool("read_file", "A" * 2500))
    registry.register(BatchTool(lambda: registry))

    result = asyncio.run(
        registry.execute(
            "batch",
            {"calls": [{"tool": "read_file", "arguments": {"value": "x"}}]},
        )
    )

    assert "result truncated" in result
    assert len(result) < 2600
