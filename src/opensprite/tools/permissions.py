"""Centralized tool permission policy."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any


ALL_RISK_LEVELS = frozenset(
    {
        "read",
        "write",
        "execute",
        "network",
        "external_side_effect",
        "configuration",
        "delegation",
        "memory",
        "mcp",
    }
)

DEFAULT_TOOL_RISKS: dict[str, frozenset[str]] = {
    "read_file": frozenset({"read"}),
    "batch": frozenset({"read"}),
    "list_dir": frozenset({"read"}),
    "glob_files": frozenset({"read"}),
    "grep_files": frozenset({"read"}),
    "read_skill": frozenset({"read"}),
    "search_history": frozenset({"read"}),
    "search_knowledge": frozenset({"read"}),
    "analyze_image": frozenset({"read", "network"}),
    "ocr_image": frozenset({"read", "network"}),
    "transcribe_audio": frozenset({"read", "network"}),
    "analyze_video": frozenset({"read", "network"}),
    "write_file": frozenset({"write"}),
    "edit_file": frozenset({"write"}),
    "apply_patch": frozenset({"write"}),
    "configure_skill": frozenset({"write", "configuration"}),
    "configure_subagent": frozenset({"write", "configuration"}),
    "save_memory": frozenset({"write", "memory"}),
    "task_update": frozenset({"write", "memory"}),
    "exec": frozenset({"execute"}),
    "process": frozenset({"execute"}),
    "delegate": frozenset({"delegation"}),
    "web_search": frozenset({"network"}),
    "web_fetch": frozenset({"network"}),
    "send_media": frozenset({"external_side_effect"}),
    "cron": frozenset({"external_side_effect"}),
    "configure_mcp": frozenset({"configuration"}),
}


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""


class ToolPermissionPolicy:
    """Evaluate whether a tool should be exposed and executed."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        allowed_tools: list[str] | None = None,
        denied_tools: list[str] | None = None,
        allowed_risk_levels: list[str] | None = None,
        denied_risk_levels: list[str] | None = None,
        approval_required_tools: list[str] | None = None,
        approval_required_risk_levels: list[str] | None = None,
    ):
        self.enabled = enabled
        self.allowed_tools = tuple(allowed_tools or ["*"])
        self.denied_tools = tuple(denied_tools or [])
        self.allowed_risk_levels = frozenset(allowed_risk_levels or ALL_RISK_LEVELS)
        self.denied_risk_levels = frozenset(denied_risk_levels or [])
        self.approval_required_tools = tuple(approval_required_tools or [])
        self.approval_required_risk_levels = frozenset(approval_required_risk_levels or [])

    @classmethod
    def allow_all(cls) -> "ToolPermissionPolicy":
        return cls(enabled=True)

    @classmethod
    def from_config(cls, config: Any) -> "ToolPermissionPolicy":
        if config is None:
            return cls.allow_all()

        def get(name: str, default: Any) -> Any:
            if isinstance(config, dict):
                return config.get(name, default)
            return getattr(config, name, default)

        return cls(
            enabled=bool(get("enabled", True)),
            allowed_tools=list(get("allowed_tools", ["*"]) or ["*"]),
            denied_tools=list(get("denied_tools", []) or []),
            allowed_risk_levels=list(get("allowed_risk_levels", list(ALL_RISK_LEVELS)) or []),
            denied_risk_levels=list(get("denied_risk_levels", []) or []),
            approval_required_tools=list(get("approval_required_tools", []) or []),
            approval_required_risk_levels=list(get("approval_required_risk_levels", []) or []),
        )

    @staticmethod
    def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)

    @staticmethod
    def risk_levels_for_tool(tool_name: str) -> frozenset[str]:
        if tool_name.startswith("mcp_"):
            return frozenset({"mcp", "external_side_effect"})
        return DEFAULT_TOOL_RISKS.get(tool_name, frozenset({"external_side_effect"}))

    def is_tool_exposed(self, tool_name: str) -> bool:
        decision = self.check(tool_name, {})
        return decision.allowed

    def check(self, tool_name: str, params: Any) -> PermissionDecision:
        if not self.enabled:
            return PermissionDecision(True)

        risks = self.risk_levels_for_tool(tool_name)
        if not self._matches_any(tool_name, self.allowed_tools):
            return PermissionDecision(False, f"tool '{tool_name}' is not in allowed_tools")
        if self._matches_any(tool_name, self.denied_tools):
            return PermissionDecision(False, f"tool '{tool_name}' is listed in denied_tools")
        denied_risks = sorted(risks & self.denied_risk_levels)
        if denied_risks:
            return PermissionDecision(False, f"risk level(s) denied: {', '.join(denied_risks)}")
        disallowed_risks = sorted(risk for risk in risks if risk not in self.allowed_risk_levels)
        if disallowed_risks:
            return PermissionDecision(False, f"risk level(s) not allowed: {', '.join(disallowed_risks)}")
        if self._matches_any(tool_name, self.approval_required_tools):
            return PermissionDecision(False, f"tool '{tool_name}' requires user approval")
        approval_risks = sorted(risks & self.approval_required_risk_levels)
        if approval_risks:
            return PermissionDecision(False, f"risk level(s) require user approval: {', '.join(approval_risks)}")

        return PermissionDecision(True)


class CompositeToolPermissionPolicy(ToolPermissionPolicy):
    """Apply multiple permission policies in order."""

    def __init__(self, *policies: ToolPermissionPolicy):
        self.policies = tuple(policy for policy in policies if policy is not None)

    def is_tool_exposed(self, tool_name: str) -> bool:
        return all(policy.is_tool_exposed(tool_name) for policy in self.policies)

    def check(self, tool_name: str, params: Any) -> PermissionDecision:
        for policy in self.policies:
            decision = policy.check(tool_name, params)
            if not decision.allowed:
                return decision
        return PermissionDecision(True)
