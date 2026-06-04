"""Runtime tool and approval policy derived from a harness profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..permission_constants import ALL_RISK_LEVELS, ALL_RISK_LEVELS_ORDER, APPROVAL_MODE_ASK, APPROVAL_MODE_AUTO, denied_risks_except
from ..tool_names import (
    BATCH_TOOL_NAME,
    LIST_RUN_FILE_CHANGES_TOOL_NAME,
    MEDIA_TOOL_NAMES,
    PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
)
from ..tools import ToolRegistry
from ..tools.permissions import ToolPermissionPolicy
from .harness_profile import (
    WORKSPACE_ANALYSIS_TASK_TYPE,
    HarnessProfile,
    is_coding_profile_name,
    is_media_profile_name,
    is_ops_profile_name,
    is_research_profile_name,
)
from .history_retrieval_policy import HISTORY_SEARCH_TOOL_NAME
from .tool_groups import WORKSPACE_DISCOVERY_TOOLS
from .web_source_policy import WEB_HARNESS_RESEARCH_TOOLS


_READ_ONLY_TOOLS = (
    *WORKSPACE_DISCOVERY_TOOLS,
    READ_SKILL_TOOL_NAME,
    HISTORY_SEARCH_TOOL_NAME,
    LIST_RUN_FILE_CHANGES_TOOL_NAME,
    PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
    BATCH_TOOL_NAME,
)
_WEB_RESEARCH_TOOLS = (*_READ_ONLY_TOOLS, *WEB_HARNESS_RESEARCH_TOOLS)
_MEDIA_TOOLS = (*_READ_ONLY_TOOLS, *MEDIA_TOOL_NAMES)
_CHAT_RISKS = ("read",)
_RESEARCH_RISKS = ("read", "network")
_MEDIA_RISKS = ("read", "network", "external_side_effect")
_WORKSPACE_ANALYSIS_RISKS = ("read", "network", "delegation")
RESEARCH_HARNESS_POLICY_REASON = (
    "research turns may inspect local context and web sources but cannot mutate workspace or external state"
)
WORKSPACE_ANALYSIS_HARNESS_POLICY_REASON = (
    "workspace analysis turns can inspect and delegate review but should not mutate or execute"
)
WORKSPACE_CHANGE_HARNESS_POLICY_REASON = (
    "workspace change turns may edit and verify but require approval for configuration or external side effects"
)
MEDIA_HARNESS_POLICY_REASON = (
    "media turns use media extraction tools and may send produced artifacts without broad workspace mutation"
)
OPERATIONS_HARNESS_POLICY_REASON = (
    "operations turns must ask approval before configuration, MCP, or external side effects"
)
CHAT_HARNESS_POLICY_REASON = "chat turns default to read-only local context and avoid external side effects"
POLICY_RESOLUTION_METADATA_REASON = (
    "effective policy is the ordered intersection of global permissions, profile override, and harness hard policy"
)
HARNESS_APPROVAL_RELAXATION_BLOCKED_REASON = (
    "harness approval requirements cannot be relaxed by user settings"
)


@dataclass(frozen=True)
class HarnessPolicy:
    """Concrete per-turn runtime policy chosen from a harness profile."""

    name: str
    harness_profile_name: str
    allowed_tools: tuple[str, ...] = ("*",)
    denied_tools: tuple[str, ...] = ()
    allowed_risk_levels: tuple[str, ...] = ALL_RISK_LEVELS_ORDER
    denied_risk_levels: tuple[str, ...] = ()
    approval_required_tools: tuple[str, ...] = ()
    approval_required_risk_levels: tuple[str, ...] = ()
    reason: str = ""

    def to_permission_policy(self) -> ToolPermissionPolicy:
        """Build the executable tool permission policy for this harness turn."""
        approval_mode = APPROVAL_MODE_ASK if self.approval_required_tools or self.approval_required_risk_levels else None
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
        if is_research_profile_name(profile_name):
            return _with_profile_denied_tools(HarnessPolicy(
                name="research_source_policy",
                harness_profile_name=profile_name,
                allowed_tools=_WEB_RESEARCH_TOOLS,
                allowed_risk_levels=_RESEARCH_RISKS,
                denied_risk_levels=denied_risks_except(_RESEARCH_RISKS),
                reason=RESEARCH_HARNESS_POLICY_REASON,
            ), harness_profile)
        if is_coding_profile_name(profile_name):
            if harness_profile.task_type == WORKSPACE_ANALYSIS_TASK_TYPE:
                return _with_profile_denied_tools(HarnessPolicy(
                    name="workspace_analysis_policy",
                    harness_profile_name=profile_name,
                    allowed_tools=("*",),
                    allowed_risk_levels=_WORKSPACE_ANALYSIS_RISKS,
                    denied_risk_levels=denied_risks_except(_WORKSPACE_ANALYSIS_RISKS),
                    reason=WORKSPACE_ANALYSIS_HARNESS_POLICY_REASON,
                ), harness_profile)
            return _with_profile_denied_tools(HarnessPolicy(
                name="workspace_change_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=tuple(risk for risk in ALL_RISK_LEVELS_ORDER if risk != "mcp"),
                denied_risk_levels=("mcp",),
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                reason=WORKSPACE_CHANGE_HARNESS_POLICY_REASON,
            ), harness_profile)
        if is_media_profile_name(profile_name):
            return _with_profile_denied_tools(HarnessPolicy(
                name="media_artifact_policy",
                harness_profile_name=profile_name,
                allowed_tools=_MEDIA_TOOLS,
                allowed_risk_levels=_MEDIA_RISKS,
                reason=MEDIA_HARNESS_POLICY_REASON,
            ), harness_profile)
        if is_ops_profile_name(profile_name):
            return _with_profile_denied_tools(HarnessPolicy(
                name="operations_approval_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=ALL_RISK_LEVELS_ORDER,
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                reason=OPERATIONS_HARNESS_POLICY_REASON,
            ), harness_profile)
        return _with_profile_denied_tools(HarnessPolicy(
            name="chat_read_policy",
            harness_profile_name=profile_name,
            allowed_tools=("*",),
            allowed_risk_levels=_CHAT_RISKS,
            denied_risk_levels=denied_risks_except(_CHAT_RISKS),
            reason=CHAT_HARNESS_POLICY_REASON,
        ), harness_profile)

    def build_tool_registry(self, base_registry: ToolRegistry, harness_policy: HarnessPolicy, profile_permission_policy: ToolPermissionPolicy | None = None) -> ToolRegistry:
        """Return a registry constrained by the selected harness policy."""
        from .tool_access import ToolAccessResolver

        return ToolAccessResolver(harness_policies=self).resolve(
            base_registry,
            harness_policy,
            profile_permission_policy,
        ).registry

    def build_tool_registry_for_profile(
        self,
        base_registry: ToolRegistry,
        harness_profile: HarnessProfile,
        harness_policy: HarnessPolicy,
        permissions_config: Any,
    ) -> ToolRegistry:
        """Return a registry constrained by global, profile, and harness config."""
        profile_overrides = getattr(permissions_config, "profile_overrides", {}) or {}
        profile_config = profile_overrides.get(harness_profile.name)
        profile_permission_policy = (
            ToolPermissionPolicy.from_config(profile_config)
            if profile_config is not None
            else None
        )
        return self.build_tool_registry(base_registry, harness_policy, profile_permission_policy)

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
            "reason": POLICY_RESOLUTION_METADATA_REASON,
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
        if policy.approval_mode == APPROVAL_MODE_AUTO and (harness_policy.approval_required_tools or harness_policy.approval_required_risk_levels):
            blocked.append(
                {
                    "source": source,
                    "field": "approval_mode",
                    "value": APPROVAL_MODE_AUTO,
                    "blocked_by": "harness_policy",
                    "reason": HARNESS_APPROVAL_RELAXATION_BLOCKED_REASON,
                }
            )
    return blocked
