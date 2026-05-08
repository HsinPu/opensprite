"""Tool evidence payloads used by agent completion checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolEvidence:
    """One completed tool call summarized for contract evaluation."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    resource_ids: tuple[str, ...] = ()
    result_preview: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": dict(self.args),
            "ok": self.ok,
            "resource_ids": list(self.resource_ids),
            "result_preview": self.result_preview,
        }


def build_tool_evidence(tool_name: str, args: dict[str, Any], result: str, *, ok: bool) -> ToolEvidence:
    """Create default evidence for tools without resource-specific metadata."""
    return ToolEvidence(
        name=tool_name,
        args=dict(args or {}),
        ok=ok,
        result_preview=str(result or "")[:240],
    )


def indexed_resource_id(prefix: str, value: Any) -> str:
    """Build a stable index resource id without letting malformed args crash evidence recording."""
    try:
        index = int(value)
    except (TypeError, ValueError):
        index = 0
    return f"{prefix}:{max(0, index)}"
