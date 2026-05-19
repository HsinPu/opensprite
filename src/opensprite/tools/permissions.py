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

APPROVAL_MODES = frozenset({"auto", "ask", "block"})

DEFAULT_TOOL_RISKS: dict[str, frozenset[str]] = {
    "read_file": frozenset({"read"}),
    "batch": frozenset({"read"}),
    "list_dir": frozenset({"read"}),
    "glob_files": frozenset({"read"}),
    "grep_files": frozenset({"read"}),
    "code_navigation": frozenset({"read"}),
    "read_skill": frozenset({"read"}),
    "search_history": frozenset({"read"}),
    "search_knowledge": frozenset({"read"}),
    "list_run_file_changes": frozenset({"read"}),
    "preview_run_file_change_revert": frozenset({"read"}),
    "analyze_image": frozenset({"read", "network"}),
    "ocr_image": frozenset({"read", "network"}),
    "transcribe_audio": frozenset({"read", "network"}),
    "analyze_video": frozenset({"read", "network"}),
    "write_file": frozenset({"write"}),
    "edit_file": frozenset({"write"}),
    "apply_patch": frozenset({"write"}),
    "configure_skill": frozenset({"write", "configuration"}),
    "configure_subagent": frozenset({"write", "configuration"}),
    "credential_store": frozenset({"write", "configuration"}),
    "save_memory": frozenset({"write", "memory"}),
    "task_update": frozenset({"write", "memory"}),
    "exec": frozenset({"execute"}),
    "process": frozenset({"execute"}),
    "verify": frozenset({"execute"}),
    "delegate": frozenset({"delegation"}),
    "delegate_many": frozenset({"delegation"}),
    "run_workflow": frozenset({"delegation"}),
    "web_search": frozenset({"network"}),
    "web_fetch": frozenset({"network"}),
    "web_research": frozenset({"network"}),
    "browser_navigate": frozenset({"network", "external_side_effect"}),
    "browser_snapshot": frozenset({"network"}),
    "browser_click": frozenset({"network", "external_side_effect"}),
    "browser_type": frozenset({"network", "external_side_effect"}),
    "browser_press": frozenset({"network", "external_side_effect"}),
    "browser_scroll": frozenset({"network"}),
    "browser_back": frozenset({"network", "external_side_effect"}),
    "browser_console": frozenset({"network", "external_side_effect"}),
    "send_media": frozenset({"external_side_effect"}),
    "cron": frozenset({"external_side_effect"}),
    "configure_mcp": frozenset({"configuration"}),
}


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    risk_levels: tuple[str, ...] = ()
    approval_mode: str | None = None
    matched_allowed_tools: tuple[str, ...] = ()
    matched_denied_tools: tuple[str, ...] = ()
    matched_allowed_risk_levels: tuple[str, ...] = ()
    matched_denied_risk_levels: tuple[str, ...] = ()
    matched_approval_required_tools: tuple[str, ...] = ()
    matched_approval_required_risk_levels: tuple[str, ...] = ()


@dataclass(frozen=True)
class PermissionApprovalResult:
    approved: bool
    request_id: str | None = None
    reason: str = ""
    status: str = ""


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
        approval_mode: str | None = None,
        approval_required_tools: list[str] | None = None,
        approval_required_risk_levels: list[str] | None = None,
    ):
        self.enabled = enabled
        self.approval_mode = approval_mode if approval_mode in APPROVAL_MODES else None
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
            approval_mode=get("approval_mode", None),
            approval_required_tools=list(get("approval_required_tools", []) or []),
            approval_required_risk_levels=list(get("approval_required_risk_levels", []) or []),
        )

    @staticmethod
    def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)

    @staticmethod
    def _matching_patterns(value: str, patterns: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(pattern for pattern in patterns if fnmatch.fnmatch(value, pattern))

    @staticmethod
    def risk_levels_for_tool(tool_name: str, tool_risk_levels: Any = None) -> frozenset[str]:
        declared_risks = _normalize_tool_risk_levels(tool_risk_levels)
        if declared_risks is not None:
            return declared_risks
        if tool_name.startswith("mcp_"):
            return frozenset({"mcp", "external_side_effect"})
        return DEFAULT_TOOL_RISKS.get(tool_name, frozenset({"external_side_effect"}))

    def is_tool_exposed(self, tool_name: str, tool_risk_levels: Any = None) -> bool:
        decision = self._check(
            tool_name,
            {},
            include_approval=self.approval_mode in {None, "block"},
            tool_risk_levels=tool_risk_levels,
        )
        return decision.allowed

    def check(self, tool_name: str, params: Any, tool_risk_levels: Any = None) -> PermissionDecision:
        return self._check(
            tool_name,
            params,
            include_approval=self.approval_mode != "auto",
            approval_requires_callback=self.approval_mode == "ask",
            tool_risk_levels=tool_risk_levels,
        )

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of this policy."""
        return {
            "enabled": self.enabled,
            "approval_mode": self.approval_mode,
            "allowed_tools": list(self.allowed_tools),
            "denied_tools": list(self.denied_tools),
            "allowed_risk_levels": sorted(self.allowed_risk_levels),
            "denied_risk_levels": sorted(self.denied_risk_levels),
            "approval_required_tools": list(self.approval_required_tools),
            "approval_required_risk_levels": sorted(self.approval_required_risk_levels),
        }

    def _check(
        self,
        tool_name: str,
        params: Any,
        *,
        include_approval: bool,
        approval_requires_callback: bool = False,
        tool_risk_levels: Any = None,
    ) -> PermissionDecision:
        if not self.enabled:
            return self._decision(True, tool_name, frozenset(), reason="permission policy disabled")

        risks = self.risk_levels_for_tool(tool_name, tool_risk_levels=tool_risk_levels)
        matched_allowed_tools = self._matching_patterns(tool_name, self.allowed_tools)
        matched_denied_tools = self._matching_patterns(tool_name, self.denied_tools)
        matched_allowed_risks = tuple(sorted(risk for risk in risks if risk in self.allowed_risk_levels))
        matched_denied_risks = tuple(sorted(risks & self.denied_risk_levels))
        if not matched_allowed_tools:
            return self._decision(False, tool_name, risks, reason=f"tool '{tool_name}' is not in allowed_tools")
        if matched_denied_tools:
            return self._decision(
                False,
                tool_name,
                risks,
                reason=f"tool '{tool_name}' is listed in denied_tools",
                matched_allowed_tools=matched_allowed_tools,
                matched_denied_tools=matched_denied_tools,
                matched_allowed_risk_levels=matched_allowed_risks,
                matched_denied_risk_levels=matched_denied_risks,
            )
        denied_risks = sorted(risks & self.denied_risk_levels)
        if denied_risks:
            return self._decision(
                False,
                tool_name,
                risks,
                reason=f"risk level(s) denied: {', '.join(denied_risks)}",
                matched_allowed_tools=matched_allowed_tools,
                matched_allowed_risk_levels=matched_allowed_risks,
                matched_denied_risk_levels=matched_denied_risks,
            )
        disallowed_risks = sorted(risk for risk in risks if risk not in self.allowed_risk_levels)
        if disallowed_risks:
            return self._decision(
                False,
                tool_name,
                risks,
                reason=f"risk level(s) not allowed: {', '.join(disallowed_risks)}",
                matched_allowed_tools=matched_allowed_tools,
                matched_allowed_risk_levels=matched_allowed_risks,
            )
        if include_approval:
            matched_approval_tools = self._matching_patterns(tool_name, self.approval_required_tools)
            if matched_approval_tools:
                return self._decision(
                    False,
                    tool_name,
                    risks,
                    f"tool '{tool_name}' requires user approval",
                    requires_approval=approval_requires_callback,
                    matched_allowed_tools=matched_allowed_tools,
                    matched_allowed_risk_levels=matched_allowed_risks,
                    matched_approval_required_tools=matched_approval_tools,
                )
            approval_risks = sorted(risks & self.approval_required_risk_levels)
            if approval_risks:
                return self._decision(
                    False,
                    tool_name,
                    risks,
                    f"risk level(s) require user approval: {', '.join(approval_risks)}",
                    requires_approval=approval_requires_callback,
                    matched_allowed_tools=matched_allowed_tools,
                    matched_allowed_risk_levels=matched_allowed_risks,
                    matched_approval_required_risk_levels=tuple(approval_risks),
                )

        return self._decision(
            True,
            tool_name,
            risks,
            matched_allowed_tools=matched_allowed_tools,
            matched_allowed_risk_levels=matched_allowed_risks,
        )

    def _decision(
        self,
        allowed: bool,
        tool_name: str,
        risks: frozenset[str],
        reason: str = "",
        *,
        requires_approval: bool = False,
        matched_allowed_tools: tuple[str, ...] = (),
        matched_denied_tools: tuple[str, ...] = (),
        matched_allowed_risk_levels: tuple[str, ...] = (),
        matched_denied_risk_levels: tuple[str, ...] = (),
        matched_approval_required_tools: tuple[str, ...] = (),
        matched_approval_required_risk_levels: tuple[str, ...] = (),
    ) -> PermissionDecision:
        return PermissionDecision(
            allowed,
            reason,
            requires_approval=requires_approval,
            risk_levels=tuple(sorted(risks)),
            approval_mode=self.approval_mode,
            matched_allowed_tools=matched_allowed_tools,
            matched_denied_tools=matched_denied_tools,
            matched_allowed_risk_levels=matched_allowed_risk_levels,
            matched_denied_risk_levels=matched_denied_risk_levels,
            matched_approval_required_tools=matched_approval_required_tools,
            matched_approval_required_risk_levels=matched_approval_required_risk_levels,
        )


class CompositeToolPermissionPolicy(ToolPermissionPolicy):
    """Apply multiple permission policies in order."""

    def __init__(self, *policies: ToolPermissionPolicy):
        self.policies = tuple(policy for policy in policies if policy is not None)

    def is_tool_exposed(self, tool_name: str, tool_risk_levels: Any = None) -> bool:
        return all(policy.is_tool_exposed(tool_name, tool_risk_levels=tool_risk_levels) for policy in self.policies)

    def check(self, tool_name: str, params: Any, tool_risk_levels: Any = None) -> PermissionDecision:
        for policy in self.policies:
            decision = policy.check(tool_name, params, tool_risk_levels=tool_risk_levels)
            if not decision.allowed:
                return decision
        risks = self.risk_levels_for_tool(tool_name, tool_risk_levels=tool_risk_levels)
        return PermissionDecision(True, risk_levels=tuple(sorted(risks)))

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of the ordered composite policy."""
        return {
            "kind": "composite",
            "policy_count": len(self.policies),
            "policies": [policy.to_metadata() for policy in self.policies],
        }


def _normalize_tool_risk_levels(value: Any) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            return None
    risks = frozenset(str(item).strip() for item in values if str(item).strip())
    if not risks:
        return frozenset({"external_side_effect"})
    return risks
