"""Runtime tool and approval policy derived from a harness profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..tools import BatchTool, ToolRegistry
from ..tools.permissions import ALL_RISK_LEVELS, CompositeToolPermissionPolicy, ToolPermissionPolicy
from .harness_profile import HarnessProfile


_READ_ONLY_TOOLS = (
    "read_file",
    "list_dir",
    "glob_files",
    "grep_files",
    "code_navigation",
    "read_skill",
    "search_history",
    "list_run_file_changes",
    "preview_run_file_change_revert",
    "batch",
)
_WEB_RESEARCH_TOOLS = (*_READ_ONLY_TOOLS, "web_search", "web_fetch", "web_research", "browser_snapshot", "browser_scroll")
_MEDIA_TOOLS = (*_READ_ONLY_TOOLS, "analyze_image", "ocr_image", "transcribe_audio", "analyze_video", "send_media")
_CHAT_RISKS = ("read",)
_RESEARCH_RISKS = ("read", "network")
_MEDIA_RISKS = ("read", "network", "external_side_effect")
_WORKSPACE_ANALYSIS_RISKS = ("read", "network", "delegation")


@dataclass(frozen=True)
class HarnessPolicy:
    """Concrete per-turn runtime policy chosen from a harness profile."""

    name: str
    harness_profile_name: str
    allowed_tools: tuple[str, ...] = ("*",)
    denied_tools: tuple[str, ...] = ()
    allowed_risk_levels: tuple[str, ...] = tuple(sorted(ALL_RISK_LEVELS))
    denied_risk_levels: tuple[str, ...] = ()
    approval_required_tools: tuple[str, ...] = ()
    approval_required_risk_levels: tuple[str, ...] = ()
    reason: str = ""

    def to_permission_policy(self) -> ToolPermissionPolicy:
        """Build the executable tool permission policy for this harness turn."""
        approval_mode = "ask" if self.approval_required_tools or self.approval_required_risk_levels else None
        return ToolPermissionPolicy(
            allowed_tools=list(self.allowed_tools),
            denied_tools=list(self.denied_tools),
            allowed_risk_levels=list(self.allowed_risk_levels),
            denied_risk_levels=list(self.denied_risk_levels),
            approval_mode=approval_mode,
            approval_required_tools=list(self.approval_required_tools),
            approval_required_risk_levels=list(self.approval_required_risk_levels),
        )

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe run event payload."""
        payload: dict[str, Any] = {
            "schema_version": 1,
            "name": self.name,
            "harness_profile": self.harness_profile_name,
            "allowed_tools": list(self.allowed_tools),
            "denied_tools": list(self.denied_tools),
            "allowed_risk_levels": list(self.allowed_risk_levels),
            "denied_risk_levels": list(self.denied_risk_levels),
            "approval_required_tools": list(self.approval_required_tools),
            "approval_required_risk_levels": list(self.approval_required_risk_levels),
            "reason": self.reason,
        }
        return payload


class HarnessPolicyService:
    """Translate harness profiles into concrete tool and approval policy."""

    def select(self, harness_profile: HarnessProfile) -> HarnessPolicy:
        """Return the runtime policy for one selected harness profile."""
        profile_name = harness_profile.name
        if profile_name == "research":
            return _with_profile_denied_tools(HarnessPolicy(
                name="research_source_policy",
                harness_profile_name=profile_name,
                allowed_tools=_WEB_RESEARCH_TOOLS,
                allowed_risk_levels=_RESEARCH_RISKS,
                denied_risk_levels=("write", "execute", "external_side_effect", "configuration", "delegation", "memory", "mcp"),
                reason="research turns may inspect local context and web sources but cannot mutate workspace or external state",
            ), harness_profile)
        if profile_name == "coding":
            if harness_profile.task_type == "workspace_analysis":
                return _with_profile_denied_tools(HarnessPolicy(
                    name="workspace_analysis_policy",
                    harness_profile_name=profile_name,
                    allowed_tools=("*",),
                    allowed_risk_levels=_WORKSPACE_ANALYSIS_RISKS,
                    denied_risk_levels=("write", "execute", "external_side_effect", "configuration", "memory", "mcp"),
                    reason="workspace analysis turns can inspect and delegate review but should not mutate or execute",
                ), harness_profile)
            return _with_profile_denied_tools(HarnessPolicy(
                name="workspace_change_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=tuple(sorted(ALL_RISK_LEVELS - {"mcp"})),
                denied_risk_levels=("mcp",),
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                reason="workspace change turns may edit and verify but require approval for configuration or external side effects",
            ), harness_profile)
        if profile_name == "media":
            return _with_profile_denied_tools(HarnessPolicy(
                name="media_artifact_policy",
                harness_profile_name=profile_name,
                allowed_tools=_MEDIA_TOOLS,
                allowed_risk_levels=_MEDIA_RISKS,
                reason="media turns use media extraction tools and may send produced artifacts without broad workspace mutation",
            ), harness_profile)
        if profile_name == "ops":
            return _with_profile_denied_tools(HarnessPolicy(
                name="operations_approval_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=tuple(sorted(ALL_RISK_LEVELS)),
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                reason="operations turns must ask approval before configuration, MCP, or external side effects",
            ), harness_profile)
        return _with_profile_denied_tools(HarnessPolicy(
            name="chat_read_policy",
            harness_profile_name=profile_name,
            allowed_tools=("*",),
            allowed_risk_levels=_CHAT_RISKS,
            denied_risk_levels=("write", "execute", "network", "external_side_effect", "configuration", "delegation", "memory", "mcp"),
            reason="chat turns default to read-only local context and avoid external side effects",
        ), harness_profile)

    def build_tool_registry(self, base_registry: ToolRegistry, harness_policy: HarnessPolicy, profile_permission_policy: ToolPermissionPolicy | None = None) -> ToolRegistry:
        """Return a registry constrained by the selected harness policy."""
        policies = [base_registry.permission_policy]
        if profile_permission_policy is not None:
            policies.append(profile_permission_policy)
        policies.append(harness_policy.to_permission_policy())
        composite_policy = CompositeToolPermissionPolicy(*policies)
        registry = base_registry.filtered(
            permission_policy=composite_policy
        )
        registry.permission_resolution_metadata = self.policy_resolution_metadata(
            base_registry.permission_policy,
            profile_permission_policy,
            harness_policy,
            composite_policy,
        )
        if "batch" in registry.tool_names:
            registry.register(BatchTool(registry_resolver=lambda: registry))
        return registry

    def policy_resolution_metadata(
        self,
        global_policy: ToolPermissionPolicy,
        profile_permission_policy: ToolPermissionPolicy | None,
        harness_policy: HarnessPolicy,
        effective_policy: ToolPermissionPolicy,
    ) -> dict[str, Any]:
        """Explain how the final executable permission policy was resolved."""
        profile_metadata = profile_permission_policy.to_metadata() if profile_permission_policy is not None else None
        harness_permission_policy = harness_policy.to_permission_policy()
        return {
            "schema_version": 1,
            "global_policy": global_policy.to_metadata(),
            "profile_override": profile_metadata,
            "harness_policy": harness_policy.to_metadata(),
            "harness_permission_policy": harness_permission_policy.to_metadata(),
            "effective_policy": effective_policy.to_metadata(),
            "constraints_applied": _constraints_applied(profile_permission_policy, harness_policy),
            "blocked_relaxations": _blocked_relaxations(global_policy, profile_permission_policy, harness_policy),
            "reason": "effective policy is the ordered intersection of global permissions, profile override, and harness hard policy",
        }


def _constraints_applied(profile_permission_policy: ToolPermissionPolicy | None, harness_policy: HarnessPolicy) -> list[str]:
    constraints = [
        "global permission policy",
        f"harness policy: {harness_policy.name}",
    ]
    if profile_permission_policy is not None:
        constraints.insert(1, "profile permission override")
    if harness_policy.denied_risk_levels:
        constraints.append("harness denied risk levels")
    if harness_policy.denied_tools:
        constraints.append("harness denied tools")
    if harness_policy.approval_required_tools or harness_policy.approval_required_risk_levels:
        constraints.append("harness approval requirements")
    return constraints


def _with_profile_denied_tools(policy: HarnessPolicy, harness_profile: HarnessProfile) -> HarnessPolicy:
    denied_tools = tuple(dict.fromkeys((*policy.denied_tools, *harness_profile.denied_tools)))
    if denied_tools == policy.denied_tools:
        return policy
    return HarnessPolicy(
        name=policy.name,
        harness_profile_name=policy.harness_profile_name,
        allowed_tools=policy.allowed_tools,
        denied_tools=denied_tools,
        allowed_risk_levels=policy.allowed_risk_levels,
        denied_risk_levels=policy.denied_risk_levels,
        approval_required_tools=policy.approval_required_tools,
        approval_required_risk_levels=policy.approval_required_risk_levels,
        reason=policy.reason,
    )


def _blocked_relaxations(
    global_policy: ToolPermissionPolicy,
    profile_permission_policy: ToolPermissionPolicy | None,
    harness_policy: HarnessPolicy,
) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    harness_allowed_risks = set(harness_policy.allowed_risk_levels)
    harness_denied_risks = set(harness_policy.denied_risk_levels)
    for source, policy in (("global_policy", global_policy), ("profile_override", profile_permission_policy)):
        if policy is None:
            continue
        policy_allowed = set(policy.allowed_risk_levels)
        relaxed_risks = sorted((policy_allowed - harness_allowed_risks) | (policy_allowed & harness_denied_risks))
        if relaxed_risks:
            blocked.append(
                {
                    "source": source,
                    "field": "allowed_risk_levels",
                    "blocked_by": "harness_policy",
                    "risk_levels": relaxed_risks,
                }
            )
        if policy.approval_mode == "auto" and (harness_policy.approval_required_tools or harness_policy.approval_required_risk_levels):
            blocked.append(
                {
                    "source": source,
                    "field": "approval_mode",
                    "value": "auto",
                    "blocked_by": "harness_policy",
                    "reason": "harness approval requirements cannot be relaxed by user settings",
                }
            )
    return blocked
