"""Runtime tool and approval policy derived from a harness profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..tools.permissions import ALL_RISK_LEVELS, ToolPermissionPolicy
from .harness_profile import HarnessProfile


_READ_ONLY_TOOLS = (
    "read_file",
    "list_dir",
    "glob_files",
    "grep_files",
    "code_navigation",
    "read_skill",
    "search_history",
    "search_knowledge",
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
    allowed_risk_levels: tuple[str, ...] = tuple(sorted(ALL_RISK_LEVELS))
    denied_risk_levels: tuple[str, ...] = ()
    approval_required_tools: tuple[str, ...] = ()
    approval_required_risk_levels: tuple[str, ...] = ()
    max_tool_iterations: int | None = None
    reason: str = ""

    def to_permission_policy(self) -> ToolPermissionPolicy:
        """Build the executable tool permission policy for this harness turn."""
        approval_mode = "ask" if self.approval_required_tools or self.approval_required_risk_levels else None
        return ToolPermissionPolicy(
            allowed_tools=list(self.allowed_tools),
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
            "allowed_risk_levels": list(self.allowed_risk_levels),
            "denied_risk_levels": list(self.denied_risk_levels),
            "approval_required_tools": list(self.approval_required_tools),
            "approval_required_risk_levels": list(self.approval_required_risk_levels),
            "reason": self.reason,
        }
        if self.max_tool_iterations is not None:
            payload["max_tool_iterations"] = self.max_tool_iterations
        return payload


class HarnessPolicyService:
    """Translate harness profiles into concrete tool and approval policy."""

    def select(self, harness_profile: HarnessProfile) -> HarnessPolicy:
        """Return the runtime policy for one selected harness profile."""
        profile_name = harness_profile.name
        if profile_name == "research":
            return HarnessPolicy(
                name="research_source_policy",
                harness_profile_name=profile_name,
                allowed_tools=_WEB_RESEARCH_TOOLS,
                allowed_risk_levels=_RESEARCH_RISKS,
                denied_risk_levels=("write", "execute", "external_side_effect", "configuration", "delegation", "memory", "mcp"),
                max_tool_iterations=8,
                reason="research turns may inspect local context and web sources but cannot mutate workspace or external state",
            )
        if profile_name == "coding":
            if harness_profile.task_type == "workspace_analysis":
                return HarnessPolicy(
                    name="workspace_analysis_policy",
                    harness_profile_name=profile_name,
                    allowed_tools=("*",),
                    allowed_risk_levels=_WORKSPACE_ANALYSIS_RISKS,
                    denied_risk_levels=("write", "execute", "external_side_effect", "configuration", "memory", "mcp"),
                    max_tool_iterations=8,
                    reason="workspace analysis turns can inspect and delegate review but should not mutate or execute",
                )
            return HarnessPolicy(
                name="workspace_change_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=tuple(sorted(ALL_RISK_LEVELS - {"mcp"})),
                denied_risk_levels=("mcp",),
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                max_tool_iterations=12,
                reason="workspace change turns may edit and verify but require approval for configuration or external side effects",
            )
        if profile_name == "media":
            return HarnessPolicy(
                name="media_artifact_policy",
                harness_profile_name=profile_name,
                allowed_tools=_MEDIA_TOOLS,
                allowed_risk_levels=_MEDIA_RISKS,
                max_tool_iterations=6,
                reason="media turns use media extraction tools and may send produced artifacts without broad workspace mutation",
            )
        if profile_name == "ops":
            return HarnessPolicy(
                name="operations_approval_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=tuple(sorted(ALL_RISK_LEVELS)),
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                max_tool_iterations=8,
                reason="operations turns must ask approval before configuration, MCP, or external side effects",
            )
        return HarnessPolicy(
            name="chat_read_policy",
            harness_profile_name=profile_name,
            allowed_tools=_READ_ONLY_TOOLS,
            allowed_risk_levels=_CHAT_RISKS,
            denied_risk_levels=("write", "execute", "network", "external_side_effect", "configuration", "delegation", "memory", "mcp"),
            max_tool_iterations=3,
            reason="chat turns default to read-only local context and avoid external side effects",
        )
