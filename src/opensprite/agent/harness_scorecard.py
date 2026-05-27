"""Structured harness scorecard schema for one agent run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


HarnessCheckStatus = Literal["pass", "warn", "fail", "not_applicable"]


@dataclass(frozen=True)
class HarnessSensorResult:
    """One deterministic or inferential harness sensor verdict."""

    sensor_id: str
    status: HarnessCheckStatus
    summary: str = ""
    details: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe sensor result."""
        return {
            "sensor_id": self.sensor_id,
            "status": self.status,
            "summary": self.summary,
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class HarnessScorecard:
    """One compact view of profile, policy, sensors, completion, and trace health."""

    profile: dict[str, Any]
    contract: dict[str, Any]
    tools: dict[str, Any]
    permissions: dict[str, Any]
    sensors: tuple[HarnessSensorResult, ...]
    completion: dict[str, Any]
    trace_health: dict[str, Any]

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe scorecard payload."""
        return {
            "schema_version": 1,
            "kind": "harness_scorecard",
            "profile": dict(self.profile),
            "contract": dict(self.contract),
            "tools": dict(self.tools),
            "permissions": dict(self.permissions),
            "sensors": [sensor.to_metadata() for sensor in self.sensors],
            "completion": dict(self.completion),
            "trace_health": dict(self.trace_health),
        }
