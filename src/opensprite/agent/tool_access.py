"""Tool access, harness policy, permission resolution, and loop guardrails."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from ..config import AgentConfig, ToolsConfig
from ..context.message_history import HISTORY_SEARCH_TOOL_NAME
from ..media import count_media_artifacts
from ..permission_constants import (
    ALL_RISK_LEVELS,
    ALL_RISK_LEVELS_ORDER,
    APPROVAL_MODE_ASK,
    APPROVAL_MODE_AUTO,
    RISK_LEVEL_DELEGATION,
    RISK_LEVEL_EXTERNAL_SIDE_EFFECT,
    RISK_LEVEL_MCP,
    RISK_LEVEL_NETWORK,
    RISK_LEVEL_READ,
    denied_risks_except,
)
from ..tool_names import (
    ANALYZE_IMAGE_TOOL_NAME,
    ANALYZE_VIDEO_TOOL_NAME,
    APPLY_PATCH_TOOL_NAME,
    BATCH_TOOL_NAME,
    CONFIGURE_MCP_TOOL_NAME,
    CONFIGURE_SKILL_TOOL_NAME,
    CONFIGURE_SUBAGENT_TOOL_NAME,
    CREDENTIAL_STORE_TOOL_NAME,
    CRON_TOOL_NAME,
    DELEGATED_EXECUTION_TOOL_NAMES,
    DELEGATE_TOOL_NAME,
    EXEC_TOOL_NAME,
    EXECUTION_TOOL_NAMES,
    LIST_RUN_FILE_CHANGES_TOOL_NAME,
    MEDIA_ANALYSIS_TOOL_NAMES,
    MEDIA_TOOL_NAMES,
    OCR_IMAGE_TOOL_NAME,
    PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
    SEND_MEDIA_TOOL_NAME,
    TASK_UPDATE_TOOL_NAME,
    TRANSCRIBE_AUDIO_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    WORKSPACE_DISCOVERY_TOOL_NAMES,
    WORKSPACE_WRITE_TOOL_NAMES,
)
from ..tools.approval import DEFAULT_PERMISSION_DENIAL_REASON, PermissionRequest, PermissionRequestManager
from ..tools.batch import BatchTool
from ..tools.evidence import (
    VERIFICATION_TOOL_NAME,
    WEB_HARNESS_RESEARCH_TOOLS,
    WEB_RESEARCH_TASK_TYPE,
    WEB_RESEARCH_TOOL_GROUP,
    WEB_SOURCE_ARTIFACT_TOOLS,
    WEB_SOURCE_EVIDENCE_TOOLS,
    WEB_SOURCE_REQUIRED_EVIDENCE,
    is_web_source_artifact_kind,
    is_web_source_evidence_tool,
)
from ..tools.loop_guardrail import (
    IDEMPOTENT_TOOL_NAMES,
    MUTATING_TOOL_NAMES,
    ToolCallSignature,
    ToolLoopGuardrail,
    ToolLoopGuardrailConfig,
    ToolLoopGuardrailDecision,
    append_toolguard_guidance,
    build_toolguard_synthetic_result,
)
from ..tools.memory import SaveMemoryTool
from ..tools.permissions import (
    CompositeToolPermissionPolicy,
    PermissionApprovalResult,
    PermissionDecision,
    ToolPermissionPolicy,
)
from ..tools.registry import ToolRegistry
from ..tools.registration import (
    BROWSER_TOOL_NAMES,
    register_batch_tools,
    register_browser_tools,
    register_config_tools,
    register_cron_tools,
    register_default_tools,
    register_delegate_tools,
    register_filesystem_tools,
    register_media_tools,
    register_memory_tool,
    register_run_trace_tools,
    register_search_tools,
    register_shell_tools,
    register_skill_tools,
    register_task_tools,
    register_verify_tools,
    register_web_tools,
    register_workflow_tools,
)
from ..utils import json_safe_value

if TYPE_CHECKING:
    from .completion_gate import CompletionGateResult
    from .execution import ExecutionResult

OPS_PROFILE_NAME = "ops"
MEDIA_PROFILE_NAME = "media"
CODING_PROFILE_NAME = "coding"
RESEARCH_PROFILE_NAME = "research"
CHAT_PROFILE_NAME = "chat"
OPERATIONS_TASK_TYPE = "operations"
MEDIA_EXTRACTION_TASK_TYPE = "media_extraction"
CODE_CHANGE_TASK_TYPE = "code_change"
WORKSPACE_READ_TASK_TYPE = "workspace_read"
WORKSPACE_CHANGE_TASK_TYPE = "workspace_change"
WORKSPACE_ANALYSIS_TASK_TYPE = "workspace_analysis"
PURE_ANSWER_TASK_TYPE = "pure_answer"
PLANNING_TASK_TYPE = "planning"
HISTORY_RETRIEVAL_TASK_TYPE = "history_retrieval"
GENERIC_TASK_TYPE = "task"
ANALYSIS_TASK_TYPE = "analysis"
MEDIA_TOOL_GROUP = "media"
EXECUTION_TOOL_GROUP = "execution"
SCHEDULING_TOOL_GROUP = "scheduling"
WORKSPACE_WRITE_TOOL_GROUP = "workspace_write"
WORKSPACE_READ_TOOL_GROUP = "workspace_read"
VERIFICATION_TOOL_GROUP = "verification"
HISTORY_RETRIEVAL_TOOL_GROUP = "history_retrieval"
OPERATION_TOOL_GROUPS = frozenset({EXECUTION_TOOL_GROUP, SCHEDULING_TOOL_GROUP})
WORKSPACE_DISCOVERY_TOOLS = WORKSPACE_DISCOVERY_TOOL_NAMES
TOOL_GROUPS: dict[str, frozenset[str]] = {
    "image_text": frozenset({OCR_IMAGE_TOOL_NAME, ANALYZE_IMAGE_TOOL_NAME}),
    "image_understanding": frozenset({ANALYZE_IMAGE_TOOL_NAME}),
    "audio_text": frozenset({TRANSCRIBE_AUDIO_TOOL_NAME}),
    EXECUTION_TOOL_GROUP: EXECUTION_TOOL_NAMES,
    MEDIA_TOOL_GROUP: MEDIA_ANALYSIS_TOOL_NAMES,
    SCHEDULING_TOOL_GROUP: frozenset({"cron"}),
    "video_understanding": frozenset({ANALYZE_VIDEO_TOOL_NAME}),
    WEB_RESEARCH_TOOL_GROUP: WEB_SOURCE_ARTIFACT_TOOLS,
    HISTORY_RETRIEVAL_TOOL_GROUP: frozenset({HISTORY_SEARCH_TOOL_NAME, LIST_RUN_FILE_CHANGES_TOOL_NAME}),
    WORKSPACE_READ_TOOL_GROUP: frozenset(
        {
            *WORKSPACE_DISCOVERY_TOOLS,
            LIST_RUN_FILE_CHANGES_TOOL_NAME,
            PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
        }
    ),
    WORKSPACE_WRITE_TOOL_GROUP: WORKSPACE_WRITE_TOOL_NAMES,
    VERIFICATION_TOOL_GROUP: frozenset({VERIFICATION_TOOL_NAME, EXEC_TOOL_NAME}),
}
TOOL_GROUP_BY_TOOL_NAME: dict[str, str] = {
    tool_name: tool_group
    for tool_group, tool_names in TOOL_GROUPS.items()
    for tool_name in tool_names
}
TASK_TYPE_BY_TOOL_GROUP: dict[str, str] = {
    "audio_text": MEDIA_EXTRACTION_TASK_TYPE,
    EXECUTION_TOOL_GROUP: OPERATIONS_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP: HISTORY_RETRIEVAL_TASK_TYPE,
    "image_text": MEDIA_EXTRACTION_TASK_TYPE,
    "image_understanding": MEDIA_EXTRACTION_TASK_TYPE,
    MEDIA_TOOL_GROUP: MEDIA_EXTRACTION_TASK_TYPE,
    SCHEDULING_TOOL_GROUP: OPERATIONS_TASK_TYPE,
    VERIFICATION_TOOL_GROUP: GENERIC_TASK_TYPE,
    "video_understanding": MEDIA_EXTRACTION_TASK_TYPE,
    WEB_RESEARCH_TOOL_GROUP: WEB_RESEARCH_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP: WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP: CODE_CHANGE_TASK_TYPE,
}
FILE_CHANGE_REQUIREMENT_KIND = "file_change"
VERIFICATION_REQUIREMENT_KIND = "verification"
_PROFILE_PRIORITY_ORDER = (OPS_PROFILE_NAME, MEDIA_PROFILE_NAME, CODING_PROFILE_NAME, RESEARCH_PROFILE_NAME, CHAT_PROFILE_NAME)
CONTRACT_OPERATIONS_PROFILE_REASON = "task contract selected operations profile"
CONTRACT_WEB_RESEARCH_PROFILE_REASON = "task contract requires web research evidence"
CONTRACT_MEDIA_PROFILE_REASON = "task contract requires media evidence"
CONTRACT_WORKSPACE_CHANGE_PROFILE_REASON = "task contract requires workspace changes"
CONTRACT_WORKSPACE_EVIDENCE_PROFILE_REASON = "task contract requires workspace evidence"
CONTRACT_PLANNING_PROFILE_REASON = "task contract selected planning mode"
CONTRACT_PURE_ANSWER_PROFILE_REASON = "task contract does not require tool-backed evidence"
DEFAULT_CHAT_PROFILE_REASON = "no task contract available; defaulting to neutral chat profile"
PREVIEW_CHAT_PROFILE_REASON = "preview profile for low-risk chat turns"
PREVIEW_WEB_RESEARCH_PROFILE_REASON = "preview profile for source-grounded web research turns"
PREVIEW_WORKSPACE_ANALYSIS_PROFILE_REASON = "preview profile for workspace analysis turns"
PREVIEW_WORKSPACE_CHANGE_PROFILE_REASON = "preview profile for workspace change turns"
PREVIEW_MEDIA_PROFILE_REASON = "preview profile for media extraction turns"
PREVIEW_OPERATIONS_PROFILE_REASON = "preview profile for operations turns"


@dataclass(frozen=True)
class HarnessProfile:
    """Selected harness strategy for one task."""

    name: str
    task_type: str
    required_tool_groups: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    verification_policy: str = "none"
    continuation_policy: str = "bounded"
    approval_required_risk_levels: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    reason: str = ""
    selection_signals: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe run event payload."""
        return {
            "schema_version": 1,
            "name": self.name,
            "task_type": self.task_type,
            "required_tool_groups": list(self.required_tool_groups),
            "required_evidence": list(self.required_evidence),
            "verification_policy": self.verification_policy,
            "continuation_policy": self.continuation_policy,
            "approval_required_risk_levels": list(self.approval_required_risk_levels),
            "denied_tools": list(self.denied_tools),
            "reason": self.reason,
            "selection": {
                "priority_order": list(_PROFILE_PRIORITY_ORDER),
                "matched_signals": list(self.selection_signals),
                "selected_by": self.reason,
            },
        }


class HarnessProfileService:
    """Derive the executable harness profile from an already-planned task contract."""

    def from_contract(self, task_contract: Any) -> HarnessProfile:
        """Select a profile from the authoritative task contract, not from user text."""
        task_type = str(getattr(task_contract, "task_type", "") or "")
        tool_groups = _contract_tool_groups(task_contract)
        requirement_kinds = _contract_requirement_kinds(task_contract)
        if is_planning_task_type(task_type):
            return HarnessProfile(
                name=CHAT_PROFILE_NAME,
                task_type=PLANNING_TASK_TYPE,
                verification_policy="none",
                continuation_policy="minimal",
                reason=CONTRACT_PLANNING_PROFILE_REASON,
                selection_signals=("contract:planning",),
            )
        if task_type == OPERATIONS_TASK_TYPE:
            required_tool_groups = tuple(sorted({WORKSPACE_READ_TOOL_GROUP, *tool_groups}))
            return HarnessProfile(
                name=OPS_PROFILE_NAME,
                task_type=OPERATIONS_TASK_TYPE,
                required_tool_groups=required_tool_groups,
                required_evidence=("audit_trace",),
                verification_policy="validate_or_report",
                continuation_policy="approval_bounded",
                approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
                reason=CONTRACT_OPERATIONS_PROFILE_REASON,
                selection_signals=("contract:operations",),
            )
        if WEB_RESEARCH_TOOL_GROUP in tool_groups or task_type == WEB_RESEARCH_TASK_TYPE:
            return HarnessProfile(
                name=RESEARCH_PROFILE_NAME,
                task_type=WEB_RESEARCH_TASK_TYPE,
                required_tool_groups=(WEB_RESEARCH_TOOL_GROUP,),
                required_evidence=WEB_SOURCE_REQUIRED_EVIDENCE,
                verification_policy="source_grounded",
                continuation_policy="bounded_with_source_fetch",
                approval_required_risk_levels=("external_side_effect",),
                reason=CONTRACT_WEB_RESEARCH_PROFILE_REASON,
                selection_signals=("contract:web_research",),
            )
        if task_type == MEDIA_EXTRACTION_TASK_TYPE or MEDIA_TOOL_GROUP in tool_groups:
            return HarnessProfile(
                name=MEDIA_PROFILE_NAME,
                task_type=MEDIA_EXTRACTION_TASK_TYPE,
                required_tool_groups=(MEDIA_TOOL_GROUP,),
                required_evidence=("media_artifact",),
                verification_policy="artifact_required",
                continuation_policy="bounded",
                reason=CONTRACT_MEDIA_PROFILE_REASON,
                selection_signals=("contract:media",),
            )
        if (
            WORKSPACE_WRITE_TOOL_GROUP in tool_groups
            or FILE_CHANGE_REQUIREMENT_KIND in requirement_kinds
            or task_type == CODE_CHANGE_TASK_TYPE
        ):
            required_tool_groups = (WORKSPACE_READ_TOOL_GROUP, WORKSPACE_WRITE_TOOL_GROUP)
            required_evidence = (FILE_CHANGE_REQUIREMENT_KIND,)
            if VERIFICATION_TOOL_GROUP in tool_groups or VERIFICATION_REQUIREMENT_KIND in requirement_kinds:
                required_tool_groups = (*required_tool_groups, VERIFICATION_TOOL_GROUP)
                required_evidence = (*required_evidence, VERIFICATION_REQUIREMENT_KIND)
            return HarnessProfile(
                name=CODING_PROFILE_NAME,
                task_type=WORKSPACE_CHANGE_TASK_TYPE,
                required_tool_groups=required_tool_groups,
                required_evidence=required_evidence,
                verification_policy="focused_if_possible",
                continuation_policy="bounded_with_verification",
                approval_required_risk_levels=("external_side_effect", "configuration"),
                reason=CONTRACT_WORKSPACE_CHANGE_PROFILE_REASON,
                selection_signals=("contract:workspace_write",),
            )
        if WORKSPACE_READ_TOOL_GROUP in tool_groups or task_type == WORKSPACE_READ_TASK_TYPE:
            required_tool_groups = (WORKSPACE_READ_TOOL_GROUP,)
            required_evidence = ("workspace_evidence",)
            if VERIFICATION_TOOL_GROUP in tool_groups or VERIFICATION_REQUIREMENT_KIND in requirement_kinds:
                required_tool_groups = (*required_tool_groups, VERIFICATION_TOOL_GROUP)
                required_evidence = (*required_evidence, VERIFICATION_REQUIREMENT_KIND)
            return HarnessProfile(
                name=CODING_PROFILE_NAME,
                task_type=WORKSPACE_ANALYSIS_TASK_TYPE,
                required_tool_groups=required_tool_groups,
                required_evidence=required_evidence,
                verification_policy="focused_if_possible",
                continuation_policy="bounded_with_verification",
                approval_required_risk_levels=("external_side_effect", "configuration"),
                reason=CONTRACT_WORKSPACE_EVIDENCE_PROFILE_REASON,
                selection_signals=("contract:workspace_read",),
            )
        return HarnessProfile(
            name=CHAT_PROFILE_NAME,
            task_type=task_type or PURE_ANSWER_TASK_TYPE,
            verification_policy="none",
            continuation_policy="minimal",
            reason=CONTRACT_PURE_ANSWER_PROFILE_REASON,
            selection_signals=("contract:pure_answer",),
        )

    def default_chat_profile(self) -> HarnessProfile:
        """Return the neutral chat profile when no task contract is available."""
        return HarnessProfile(
            name=CHAT_PROFILE_NAME,
            task_type="pure_answer",
            verification_policy="none",
            continuation_policy="minimal",
            reason=DEFAULT_CHAT_PROFILE_REASON,
            selection_signals=("default:chat",),
        )


def preview_harness_profiles() -> tuple[HarnessProfile, ...]:
    """Return representative profiles for settings policy previews."""
    return (
        HarnessProfile(
            name=CHAT_PROFILE_NAME,
            task_type="conversation",
            verification_policy="none",
            continuation_policy="minimal",
            reason=PREVIEW_CHAT_PROFILE_REASON,
        ),
        HarnessProfile(
            name=RESEARCH_PROFILE_NAME,
            task_type=WEB_RESEARCH_TASK_TYPE,
            required_tool_groups=(WEB_RESEARCH_TOOL_GROUP,),
            required_evidence=WEB_SOURCE_REQUIRED_EVIDENCE,
            verification_policy="source_grounded",
            continuation_policy="bounded_with_source_fetch",
            approval_required_risk_levels=("external_side_effect",),
            reason=PREVIEW_WEB_RESEARCH_PROFILE_REASON,
        ),
        HarnessProfile(
            name=CODING_PROFILE_NAME,
            task_type=WORKSPACE_ANALYSIS_TASK_TYPE,
            required_tool_groups=(WORKSPACE_READ_TOOL_GROUP,),
            required_evidence=("workspace_evidence",),
            verification_policy="focused_if_possible",
            continuation_policy="bounded_with_verification",
            approval_required_risk_levels=("external_side_effect", "configuration"),
            reason=PREVIEW_WORKSPACE_ANALYSIS_PROFILE_REASON,
        ),
        HarnessProfile(
            name=CODING_PROFILE_NAME,
            task_type=WORKSPACE_CHANGE_TASK_TYPE,
            required_tool_groups=(WORKSPACE_READ_TOOL_GROUP, WORKSPACE_WRITE_TOOL_GROUP),
            required_evidence=(FILE_CHANGE_REQUIREMENT_KIND,),
            verification_policy="focused_if_possible",
            continuation_policy="bounded_with_verification",
            approval_required_risk_levels=("external_side_effect", "configuration"),
            reason=PREVIEW_WORKSPACE_CHANGE_PROFILE_REASON,
        ),
        HarnessProfile(
            name=MEDIA_PROFILE_NAME,
            task_type=MEDIA_EXTRACTION_TASK_TYPE,
            required_tool_groups=(MEDIA_TOOL_GROUP,),
            required_evidence=("media_artifact",),
            verification_policy="artifact_required",
            continuation_policy="bounded",
            reason=PREVIEW_MEDIA_PROFILE_REASON,
        ),
        HarnessProfile(
            name=OPS_PROFILE_NAME,
            task_type=OPERATIONS_TASK_TYPE,
            required_tool_groups=("scheduling",),
            required_evidence=("audit_trace",),
            verification_policy="validate_or_report",
            continuation_policy="approval_bounded",
            approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
            reason=PREVIEW_OPERATIONS_PROFILE_REASON,
        ),
    )


def _contract_tool_groups(task_contract: Any) -> set[str]:
    return {
        str(getattr(requirement, "tool_group", "") or "")
        for requirement in getattr(task_contract, "requirements", ()) or ()
        if str(getattr(requirement, "tool_group", "") or "")
    }


def _contract_requirement_kinds(task_contract: Any) -> set[str]:
    return {
        str(getattr(requirement, "kind", "") or "")
        for requirement in getattr(task_contract, "requirements", ()) or ()
        if str(getattr(requirement, "kind", "") or "")
    }


def normalize_profile_name(profile_name: str | None) -> str:
    return str(profile_name or "").strip()


def is_chat_profile_name(profile_name: str | None) -> bool:
    return normalize_profile_name(profile_name) == CHAT_PROFILE_NAME


def is_research_profile_name(profile_name: str | None) -> bool:
    return normalize_profile_name(profile_name) == RESEARCH_PROFILE_NAME


def is_planning_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == PLANNING_TASK_TYPE


def is_coding_profile_name(profile_name: str | None) -> bool:
    return normalize_profile_name(profile_name) == CODING_PROFILE_NAME


def is_media_profile_name(profile_name: str | None) -> bool:
    return normalize_profile_name(profile_name) == MEDIA_PROFILE_NAME


def is_ops_profile_name(profile_name: str | None) -> bool:
    return normalize_profile_name(profile_name) == OPS_PROFILE_NAME


def harness_profile_follow_up_instruction(profile_name: str | None) -> str:
    if is_research_profile_name(profile_name):
        return (
            "\n- Harness profile: research. Gather source evidence first, fetch or inspect at least one substantive source, "
            "and reference gathered sources in the final answer."
        )
    if is_coding_profile_name(profile_name):
        return (
            "\n- Harness profile: coding. Inspect workspace context before changing files, make the smallest safe change, "
            "and run focused verification when possible."
        )
    if is_media_profile_name(profile_name):
        return "\n- Harness profile: media. Use the relevant media tool to produce the required artifact before finalizing."
    if is_ops_profile_name(profile_name):
        return (
            "\n- Harness profile: ops. Do not perform external side effects without required approval; report validation or blockers explicitly."
        )
    return ""


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
_CHAT_RISKS = (RISK_LEVEL_READ,)
_RESEARCH_RISKS = (RISK_LEVEL_READ, RISK_LEVEL_NETWORK)
_MEDIA_RISKS = (RISK_LEVEL_READ, RISK_LEVEL_NETWORK, RISK_LEVEL_EXTERNAL_SIDE_EFFECT)
_WORKSPACE_ANALYSIS_RISKS = (RISK_LEVEL_READ, RISK_LEVEL_NETWORK, RISK_LEVEL_DELEGATION)
RESEARCH_HARNESS_POLICY_REASON = (
    "research turns should gather source evidence first while using any tools allowed by user permissions"
)
WORKSPACE_ANALYSIS_HARNESS_POLICY_REASON = (
    "workspace analysis turns should inspect context first and avoid unnecessary mutation"
)
WORKSPACE_CHANGE_HARNESS_POLICY_REASON = (
    "workspace change turns should edit carefully and verify while preserving configured approval gates"
)
MEDIA_HARNESS_POLICY_REASON = (
    "media turns should use relevant media extraction tools before finalizing"
)
OPERATIONS_HARNESS_POLICY_REASON = (
    "operations turns should preserve approval gates for configuration, MCP, or external side effects"
)
CHAT_HARNESS_POLICY_REASON = "chat turns should answer directly unless tools are useful and allowed"
POLICY_RESOLUTION_METADATA_REASON = (
    "effective policy is the ordered intersection of global permissions, profile override, and harness executable policy"
)
HARNESS_APPROVAL_REQUIREMENT_PROTECTED_REASON = (
    "harness approval requirements remain active in the executable policy"
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
        """Build the guidance permission policy described by this harness turn."""
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

    def to_executable_permission_policy(self) -> ToolPermissionPolicy:
        """Build the executable harness policy without profile scope restrictions."""
        approval_mode = APPROVAL_MODE_ASK if self.approval_required_tools or self.approval_required_risk_levels else None
        return ToolPermissionPolicy(
            allowed_tools=["*"],
            denied_tools=list(self.denied_tools),
            allowed_risk_levels=list(ALL_RISK_LEVELS_ORDER),
            denied_risk_levels=[],
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
                name="research_source_guidance_policy",
                harness_profile_name=profile_name,
                allowed_tools=_WEB_RESEARCH_TOOLS,
                allowed_risk_levels=_RESEARCH_RISKS,
                denied_risk_levels=denied_risks_except(_RESEARCH_RISKS),
                reason=RESEARCH_HARNESS_POLICY_REASON,
            ), harness_profile)
        if is_coding_profile_name(profile_name):
            if harness_profile.task_type == WORKSPACE_ANALYSIS_TASK_TYPE:
                return _with_profile_denied_tools(HarnessPolicy(
                    name="workspace_analysis_guidance_policy",
                    harness_profile_name=profile_name,
                    allowed_tools=("*",),
                    allowed_risk_levels=_WORKSPACE_ANALYSIS_RISKS,
                    denied_risk_levels=denied_risks_except(_WORKSPACE_ANALYSIS_RISKS),
                    reason=WORKSPACE_ANALYSIS_HARNESS_POLICY_REASON,
                ), harness_profile)
            return _with_profile_denied_tools(HarnessPolicy(
                name="workspace_change_guidance_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=tuple(risk for risk in ALL_RISK_LEVELS_ORDER if risk != RISK_LEVEL_MCP),
                denied_risk_levels=(RISK_LEVEL_MCP,),
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                reason=WORKSPACE_CHANGE_HARNESS_POLICY_REASON,
            ), harness_profile)
        if is_media_profile_name(profile_name):
            return _with_profile_denied_tools(HarnessPolicy(
                name="media_artifact_guidance_policy",
                harness_profile_name=profile_name,
                allowed_tools=_MEDIA_TOOLS,
                allowed_risk_levels=_MEDIA_RISKS,
                reason=MEDIA_HARNESS_POLICY_REASON,
            ), harness_profile)
        if is_ops_profile_name(profile_name):
            return _with_profile_denied_tools(HarnessPolicy(
                name="operations_approval_guidance_policy",
                harness_profile_name=profile_name,
                allowed_tools=("*",),
                allowed_risk_levels=ALL_RISK_LEVELS_ORDER,
                approval_required_risk_levels=tuple(harness_profile.approval_required_risk_levels),
                reason=OPERATIONS_HARNESS_POLICY_REASON,
            ), harness_profile)
        return _with_profile_denied_tools(HarnessPolicy(
            name="chat_guidance_policy",
            harness_profile_name=profile_name,
            allowed_tools=("*",),
            allowed_risk_levels=_CHAT_RISKS,
            denied_risk_levels=denied_risks_except(_CHAT_RISKS),
            reason=CHAT_HARNESS_POLICY_REASON,
        ), harness_profile)

    def build_tool_registry(self, base_registry: ToolRegistry, harness_policy: HarnessPolicy, profile_permission_policy: ToolPermissionPolicy | None = None) -> ToolRegistry:
        """Return a registry constrained by the selected harness policy."""

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
        harness_guidance_policy = harness_policy.to_permission_policy()
        harness_executable_policy = harness_policy.to_executable_permission_policy()
        return {
            "schema_version": 1,
            "global_policy": global_policy.to_metadata(),
            "profile_override": profile_metadata,
            "harness_policy": harness_policy.to_metadata(),
            "harness_guidance_policy": harness_guidance_policy.to_metadata(),
            "harness_executable_policy": harness_executable_policy.to_metadata(),
            "effective_policy": effective_policy.to_metadata(),
            "constraints_applied": _constraints_applied(profile_permission_policy, harness_policy),
            "protected_approval_requirements": _protected_approval_requirements(global_policy, profile_permission_policy, harness_executable_policy),
            "reason": POLICY_RESOLUTION_METADATA_REASON,
        }


def _constraints_applied(profile_permission_policy: ToolPermissionPolicy | None, harness_policy: HarnessPolicy) -> list[str]:
    constraints = [
        "global permission policy",
        f"harness guidance: {harness_policy.name}",
        "harness executable policy",
    ]
    if profile_permission_policy is not None:
        constraints.insert(1, "profile permission override")
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


def _protected_approval_requirements(
    global_policy: ToolPermissionPolicy,
    profile_permission_policy: ToolPermissionPolicy | None,
    harness_policy: HarnessPolicy,
) -> list[dict[str, Any]]:
    protected: list[dict[str, Any]] = []
    for source, policy in (("global_policy", global_policy), ("profile_override", profile_permission_policy)):
        if policy is None:
            continue
        if policy.approval_mode == APPROVAL_MODE_AUTO and (harness_policy.approval_required_tools or harness_policy.approval_required_risk_levels):
            protected.append(
                {
                    "source": source,
                    "field": "approval_mode",
                    "value": APPROVAL_MODE_AUTO,
                    "preserved_by": "harness_executable_policy",
                    "reason": HARNESS_APPROVAL_REQUIREMENT_PROTECTED_REASON,
                }
            )
    return protected


SENSOR_CHAT_NO_UNEXPECTED_TOOLS = "chat.no_unexpected_tools"
SENSOR_COMPLETION_FINAL_ANSWER = "completion.final_answer"
SENSOR_RESEARCH_SOURCE_COVERAGE = "research.source_coverage"
SENSOR_RESEARCH_FRESHNESS = "research.freshness"
SENSOR_COMPLETION_SOURCE_GROUNDING = "completion.source_grounding"
SENSOR_CODING_WORKSPACE_EVIDENCE = "coding.workspace_evidence"
SENSOR_CODING_FILE_CHANGE = "coding.file_change"
SENSOR_CODING_VERIFICATION = "coding.verification"
SENSOR_COMPLETION_CHANGE_SUMMARY = "completion.change_summary"
SENSOR_COMPLETION_VERIFICATION_OR_GAP = "completion.verification_or_gap"
SENSOR_MEDIA_ARTIFACT = "media.artifact"
SENSOR_COMPLETION_MEDIA_SUMMARY = "completion.media_summary"
SENSOR_OPS_AUDIT_TRACE = "ops.audit_trace"
SENSOR_OPS_APPROVAL_BOUNDARY = "ops.approval_boundary"
SENSOR_COMPLETION_OPERATION_REPORT = "completion.operation_report"

SENSOR_IDS_BY_TASK_TYPE: dict[str, tuple[str, ...]] = {
    "conversation": (SENSOR_CHAT_NO_UNEXPECTED_TOOLS, SENSOR_COMPLETION_FINAL_ANSWER),
    "question": (SENSOR_CHAT_NO_UNEXPECTED_TOOLS, SENSOR_COMPLETION_FINAL_ANSWER),
    "pure_answer": (SENSOR_CHAT_NO_UNEXPECTED_TOOLS, SENSOR_COMPLETION_FINAL_ANSWER),
    "web_research": (SENSOR_RESEARCH_SOURCE_COVERAGE, SENSOR_RESEARCH_FRESHNESS, SENSOR_COMPLETION_SOURCE_GROUNDING),
    "workspace_analysis": (SENSOR_CODING_WORKSPACE_EVIDENCE, SENSOR_COMPLETION_VERIFICATION_OR_GAP),
    "workspace_change": (SENSOR_CODING_FILE_CHANGE, SENSOR_CODING_VERIFICATION, SENSOR_COMPLETION_CHANGE_SUMMARY),
    "media_extraction": (SENSOR_MEDIA_ARTIFACT, SENSOR_COMPLETION_MEDIA_SUMMARY),
    "operations": (SENSOR_OPS_AUDIT_TRACE, SENSOR_OPS_APPROVAL_BOUNDARY, SENSOR_COMPLETION_OPERATION_REPORT),
}


@dataclass(frozen=True)
class HarnessInventoryItem:
    """One representative harness shape used for scoring, UI, and evals."""

    key: str
    profile: HarnessProfile
    policy_name: str
    expected_sensor_ids: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe inventory entry."""
        return {
            "key": self.key,
            "profile": self.profile.to_metadata(),
            "policy_name": self.policy_name,
            "expected_sensor_ids": list(self.expected_sensor_ids),
        }


def build_harness_inventory() -> tuple[HarnessInventoryItem, ...]:
    """Return the canonical harness inventory derived from preview profiles."""
    policy_service = HarnessPolicyService()
    items: list[HarnessInventoryItem] = []
    for profile in preview_harness_profiles():
        policy = policy_service.select(profile)
        items.append(
            HarnessInventoryItem(
                key=f"{profile.name}:{profile.task_type}",
                profile=profile,
                policy_name=policy.name,
                expected_sensor_ids=SENSOR_IDS_BY_TASK_TYPE[profile.task_type],
            )
        )
    return tuple(items)


def expected_sensor_ids_for_task_type(task_type: str) -> tuple[str, ...]:
    """Return the expected sensor ids for one harness task type."""
    return SENSOR_IDS_BY_TASK_TYPE.get(task_type, ())


def harness_inventory_payload() -> dict[str, Any]:
    """Return a stable payload for debug exports, evals, and future UI wiring."""
    items = build_harness_inventory()
    return {
        "schema_version": 1,
        "kind": "harness_inventory",
        "items": [item.to_metadata() for item in items],
    }


HARNESS_SENSOR_PASS_STATUS = "pass"
HARNESS_SENSOR_WARN_STATUS = "warn"
HARNESS_SENSOR_FAIL_STATUS = "fail"
HARNESS_SENSOR_NOT_APPLICABLE_STATUS = "not_applicable"
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


def evaluate_harness_sensors(
    *,
    task_type: str,
    execution_result: ExecutionResult,
    completion_result: CompletionGateResult,
) -> tuple[HarnessSensorResult, ...]:
    """Evaluate the expected sensors for a harness task type."""
    sensor_ids = expected_sensor_ids_for_task_type(task_type)
    return tuple(
        _evaluate_sensor(sensor_id, execution_result=execution_result, completion_result=completion_result)
        for sensor_id in sensor_ids
    )


def _evaluate_sensor(
    sensor_id: str,
    *,
    execution_result: ExecutionResult,
    completion_result: CompletionGateResult,
) -> HarnessSensorResult:
    if sensor_id == SENSOR_CHAT_NO_UNEXPECTED_TOOLS:
        count = execution_result.executed_tool_calls
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if count == 0 else HARNESS_SENSOR_WARN_STATUS,
            "No tools were needed." if count == 0 else "Conversation turn used tools.",
            {"executed_tool_calls": count},
        )
    if sensor_id == SENSOR_COMPLETION_FINAL_ANSWER:
        return _completion_sensor(sensor_id, completion_result)
    if sensor_id == SENSOR_RESEARCH_SOURCE_COVERAGE:
        count = _artifact_count_matching(execution_result, is_web_source_artifact_kind)
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if count > 0 else HARNESS_SENSOR_FAIL_STATUS,
            "Traceable web sources were recorded." if count > 0 else "No traceable web source artifact was recorded.",
            {"web_source_artifacts": count},
        )
    if sensor_id == SENSOR_RESEARCH_FRESHNESS:
        evidence_count = _web_tool_evidence_count(execution_result)
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if evidence_count > 0 else HARNESS_SENSOR_WARN_STATUS,
            "Live web evidence is present." if evidence_count > 0 else "No live web evidence was found.",
            {"web_tool_evidence": evidence_count},
        )
    if sensor_id == SENSOR_COMPLETION_SOURCE_GROUNDING:
        return _missing_evidence_sensor(sensor_id, completion_result)
    if sensor_id == SENSOR_CODING_WORKSPACE_EVIDENCE:
        evidence_count = len(execution_result.tool_evidence)
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if evidence_count > 0 else HARNESS_SENSOR_WARN_STATUS,
            "Workspace evidence was gathered." if evidence_count > 0 else "No workspace evidence was recorded.",
            {"tool_evidence": evidence_count},
        )
    if sensor_id == SENSOR_CODING_FILE_CHANGE:
        count = execution_result.file_change_count
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if count > 0 else HARNESS_SENSOR_FAIL_STATUS,
            "File changes were recorded." if count > 0 else "No file changes were recorded.",
            {"file_change_count": count},
        )
    if sensor_id == SENSOR_CODING_VERIFICATION:
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if execution_result.verification_passed else HARNESS_SENSOR_WARN_STATUS,
            "Verification passed." if execution_result.verification_passed else "Verification did not pass.",
            {
                "verification_attempted": execution_result.verification_attempted,
                "verification_passed": execution_result.verification_passed,
            },
        )
    if sensor_id == SENSOR_COMPLETION_CHANGE_SUMMARY:
        return _completion_sensor(sensor_id, completion_result)
    if sensor_id == SENSOR_COMPLETION_VERIFICATION_OR_GAP:
        return _missing_evidence_sensor(sensor_id, completion_result)
    if sensor_id == SENSOR_MEDIA_ARTIFACT:
        count = count_media_artifacts(execution_result.task_artifacts)
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if count > 0 else HARNESS_SENSOR_FAIL_STATUS,
            "Media artifacts were recorded." if count > 0 else "No media artifact was recorded.",
            {"media_artifacts": count},
        )
    if sensor_id == SENSOR_COMPLETION_MEDIA_SUMMARY:
        return _completion_sensor(sensor_id, completion_result)
    if sensor_id == SENSOR_OPS_AUDIT_TRACE:
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS if execution_result.executed_tool_calls > 0 else HARNESS_SENSOR_WARN_STATUS,
            "Operational tool activity was recorded." if execution_result.executed_tool_calls > 0 else "No operational tool activity was recorded.",
            {"executed_tool_calls": execution_result.executed_tool_calls},
        )
    if sensor_id == SENSOR_OPS_APPROVAL_BOUNDARY:
        return HarnessSensorResult(
            sensor_id,
            HARNESS_SENSOR_PASS_STATUS,
            "Approval policy metadata was recorded.",
            {"has_harness_policy": bool(execution_result.harness_policy)},
        )
    if sensor_id == SENSOR_COMPLETION_OPERATION_REPORT:
        return _completion_sensor(sensor_id, completion_result)
    return HarnessSensorResult(sensor_id, HARNESS_SENSOR_NOT_APPLICABLE_STATUS, "No deterministic check is defined.")


def _completion_sensor(sensor_id: str, completion_result: CompletionGateResult) -> HarnessSensorResult:
    from .completion_gate import is_complete_completion_status

    complete = is_complete_completion_status(completion_result.status)
    return HarnessSensorResult(
        sensor_id,
        HARNESS_SENSOR_PASS_STATUS if complete else HARNESS_SENSOR_FAIL_STATUS,
        completion_result.reason,
        {"status": completion_result.status},
    )


def _missing_evidence_sensor(sensor_id: str, completion_result: CompletionGateResult) -> HarnessSensorResult:
    missing = tuple(completion_result.missing_evidence)
    return HarnessSensorResult(
        sensor_id,
        HARNESS_SENSOR_PASS_STATUS if not missing else HARNESS_SENSOR_FAIL_STATUS,
        "No missing evidence." if not missing else "Completion gate reported missing evidence.",
        {"missing_evidence": list(missing)},
    )


def _artifact_count_matching(execution_result: ExecutionResult, matches_kind: Callable[[str | None], bool]) -> int:
    return sum(1 for artifact in execution_result.task_artifacts if matches_kind(artifact.kind))


def _web_tool_evidence_count(execution_result: ExecutionResult) -> int:
    return sum(1 for evidence in execution_result.tool_evidence if is_web_source_evidence_tool(evidence.name))


_RISK_PROBE_TOOLS = {
    "configuration": CONFIGURE_MCP_TOOL_NAME,
    "delegation": DELEGATE_TOOL_NAME,
    "execute": EXEC_TOOL_NAME,
    "external_side_effect": "browser_click",
    "mcp": "mcp_probe_tool",
    "memory": TASK_UPDATE_TOOL_NAME,
    "network": WEB_SEARCH_TOOL_NAME,
    "read": READ_FILE_TOOL_NAME,
    "write": APPLY_PATCH_TOOL_NAME,
}


@dataclass(frozen=True)
class ToolAccessResolution:
    """Resolved tool registry and metadata for one agent turn."""

    registry: ToolRegistry
    effective_policy: ToolPermissionPolicy
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EffectivePolicyResolution:
    """Resolved effective policy without requiring a concrete tool registry."""

    effective_policy: ToolPermissionPolicy
    metadata: dict[str, Any]


class PermissionEventRecorder:
    """Formats and emits permission request lifecycle events."""

    def __init__(
        self,
        *,
        emit_run_event: Callable[..., Awaitable[None]],
        format_log_preview: Callable[..., str],
    ):
        self._emit_run_event = emit_run_event
        self._format_log_preview = format_log_preview

    async def emit(self, event_type: str, request: PermissionRequest) -> None:
        """Persist and publish one permission approval lifecycle event for a run."""
        if not request.session_id or not request.run_id:
            return
        try:
            params_preview = json.dumps(
                json_safe_value(request.params),
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:
            params_preview = str(request.params)
        payload: dict[str, Any] = {
            "request_id": request.request_id,
            "tool_name": request.tool_name,
            "reason": request.reason,
            "status": request.status,
            "action_type": request.action_type,
            "risk_level": request.risk_level,
            "risk_levels": request.risk_levels,
            "resource": request.resource,
            "preview": request.preview,
            "recommended_decision": request.recommended_decision,
            "args_preview": self._format_log_preview(params_preview, max_chars=240),
            "created_at": request.created_at,
            "expires_at": request.expires_at,
        }
        if request.resolved_at is not None:
            payload.update(
                {
                    "resolved_at": request.resolved_at,
                    "resolution_reason": request.resolution_reason,
                    "timed_out": request.timed_out,
                }
            )
        await self._emit_run_event(
            request.session_id,
            request.run_id,
            event_type,
            payload,
            channel=request.channel,
            external_chat_id=request.external_chat_id,
        )


class AgentPermissionService:
    """Wraps ask-mode permission requests with current run context."""

    def __init__(
        self,
        *,
        requests: PermissionRequestManager,
        events: PermissionEventRecorder,
        current_session_id: Callable[[], str | None],
        current_run_id: Callable[[], str | None],
        current_channel: Callable[[], str | None],
        current_external_chat_id: Callable[[], str | None],
    ):
        self.requests = requests
        self.events = events
        self._current_session_id = current_session_id
        self._current_run_id = current_run_id
        self._current_channel = current_channel
        self._current_external_chat_id = current_external_chat_id

    def pending_requests(self) -> list[PermissionRequest]:
        """Return permission requests waiting for an external decision."""
        return self.requests.pending_requests()

    async def approve_request(self, request_id: str) -> PermissionRequest | None:
        """Approve one pending tool permission request."""
        return await self.requests.approve_once(request_id)

    async def deny_request(
        self,
        request_id: str,
        reason: str = DEFAULT_PERMISSION_DENIAL_REASON,
    ) -> PermissionRequest | None:
        """Deny one pending tool permission request."""
        return await self.requests.deny(request_id, reason=reason)

    async def handle_tool_permission_request(
        self,
        tool_name: str,
        params: Any,
        decision: PermissionDecision,
    ) -> PermissionApprovalResult:
        """Create an ask-mode approval request for the current run context."""
        return await self.requests.request(
            tool_name=tool_name,
            params=params,
            reason=decision.reason,
            risk_levels=decision.risk_levels,
            session_id=self._current_session_id(),
            run_id=self._current_run_id(),
            channel=self._current_channel(),
            external_chat_id=self._current_external_chat_id(),
        )

    async def emit_request_event(self, event_type: str, request: PermissionRequest) -> None:
        """Persist and publish permission approval lifecycle events for a run."""
        await self.events.emit(event_type, request)


class ToolAccessResolver:
    """Resolve global, profile, and harness permissions into executable tool access."""

    def __init__(self, *, harness_policies: HarnessPolicyService | None = None):
        self._harness_policies = harness_policies or HarnessPolicyService()

    def resolve(
        self,
        base_registry: ToolRegistry,
        harness_policy: HarnessPolicy,
        profile_permission_policy: ToolPermissionPolicy | None = None,
    ) -> ToolAccessResolution:
        """Return a registry constrained by the selected effective tool policy."""
        policy_resolution = self.resolve_policy(
            base_registry.permission_policy,
            harness_policy,
            profile_permission_policy,
        )
        effective_policy = policy_resolution.effective_policy
        metadata = policy_resolution.metadata
        registry = base_registry.filtered(permission_policy=effective_policy, exposed_only=True)
        if BATCH_TOOL_NAME in registry.tool_names:
            registry.register(BatchTool(registry_resolver=lambda: registry))
        metadata["tool_access"] = _tool_access_metadata(base_registry, registry, effective_policy)
        registry.permission_resolution_metadata = metadata
        return ToolAccessResolution(
            registry=registry,
            effective_policy=effective_policy,
            metadata=metadata,
        )

    def resolve_policy(
        self,
        global_policy: ToolPermissionPolicy,
        harness_policy: HarnessPolicy,
        profile_permission_policy: ToolPermissionPolicy | None = None,
    ) -> EffectivePolicyResolution:
        """Return the effective policy and metadata for one harness turn."""
        policies = [global_policy]
        if profile_permission_policy is not None:
            policies.append(profile_permission_policy)
        policies.append(harness_policy.to_executable_permission_policy())
        effective_policy = CompositeToolPermissionPolicy(*policies)
        metadata = self._harness_policies.policy_resolution_metadata(
            global_policy,
            profile_permission_policy,
            harness_policy,
            effective_policy,
        )
        metadata["effective_risks"] = summarize_effective_risks(effective_policy)
        return EffectivePolicyResolution(
            effective_policy=effective_policy,
            metadata=metadata,
        )

    def resolve_overlay(
        self,
        base_registry: ToolRegistry,
        *,
        overlay_policy: ToolPermissionPolicy,
        include_names: set[str] | frozenset[str] | None = None,
        extra_policies: tuple[ToolPermissionPolicy, ...] = (),
        metadata_kind: str,
    ) -> ToolAccessResolution:
        """Return a registry constrained by a non-harness overlay policy."""
        policy_resolution = self.resolve_overlay_policy(
            base_registry.permission_policy,
            overlay_policy=overlay_policy,
            extra_policies=extra_policies,
            metadata_kind=metadata_kind,
        )
        effective_policy = policy_resolution.effective_policy
        registry = base_registry.filtered(
            include_names=include_names,
            permission_policy=effective_policy,
            exposed_only=True,
        )
        if BATCH_TOOL_NAME in registry.tool_names:
            registry.register(BatchTool(registry_resolver=lambda: registry))
        metadata = policy_resolution.metadata
        metadata["tool_access"] = _tool_access_metadata(base_registry, registry, effective_policy)
        registry.permission_resolution_metadata = metadata
        return ToolAccessResolution(
            registry=registry,
            effective_policy=effective_policy,
            metadata=metadata,
        )

    def resolve_overlay_policy(
        self,
        base_policy: ToolPermissionPolicy,
        *,
        overlay_policy: ToolPermissionPolicy,
        extra_policies: tuple[ToolPermissionPolicy, ...] = (),
        metadata_kind: str,
    ) -> EffectivePolicyResolution:
        """Return the effective policy for a non-harness overlay."""
        policies: list[ToolPermissionPolicy] = [base_policy, overlay_policy]
        policies.extend(extra_policies)
        effective_policy = CompositeToolPermissionPolicy(*policies)
        metadata: dict[str, Any] = {
            "schema_version": 1,
            "kind": metadata_kind,
            "base_permission_policy": base_policy.to_metadata(),
            "overlay_permission_policy": overlay_policy.to_metadata(),
            "extra_permission_policies": [policy.to_metadata() for policy in extra_policies],
            "effective_policy": effective_policy.to_metadata(),
            "effective_risks": summarize_effective_risks(effective_policy),
        }
        return EffectivePolicyResolution(
            effective_policy=effective_policy,
            metadata=metadata,
        )


def planning_mode_permission_policy(allowed_tools: set[str] | frozenset[str]) -> ToolPermissionPolicy:
    """Return the read/network overlay policy for explicit plan-only turns."""
    allowed_risks = ("read", "network")
    return ToolPermissionPolicy(
        allowed_tools=sorted(allowed_tools),
        allowed_risk_levels=list(allowed_risks),
        denied_risk_levels=list(denied_risks_except(allowed_risks)),
    )


def summarize_effective_risks(policy: ToolPermissionPolicy) -> dict[str, list[str]]:
    """Summarize effective risk exposure and approval requirements for previews."""
    allowed: list[str] = []
    denied: list[str] = []
    approval_required: list[str] = []
    for risk in ALL_RISK_LEVELS_ORDER:
        tool_name = _RISK_PROBE_TOOLS.get(risk, f"__risk_probe_{risk}")
        tool_risks = frozenset({risk})
        if policy.is_tool_exposed(tool_name, tool_risk_levels=tool_risks):
            allowed.append(risk)
            decision = policy.check(tool_name, {}, tool_risk_levels=tool_risks)
            if decision.requires_approval:
                approval_required.append(risk)
        else:
            denied.append(risk)
    return {
        "allowed_risk_levels": allowed,
        "denied_risk_levels": denied,
        "approval_required_risk_levels": approval_required,
    }


def _tool_access_metadata(
    base_registry: ToolRegistry,
    resolved_registry: ToolRegistry,
    effective_policy: ToolPermissionPolicy,
) -> dict[str, Any]:
    registered = list(base_registry.registered_tools())
    exposed_tools = list(resolved_registry.tool_names)
    blocked_tools = []
    for tool in registered:
        if effective_policy.is_tool_exposed(tool.name, tool_risk_levels=tool.risk_levels):
            continue
        decision = effective_policy.check(tool.name, {}, tool_risk_levels=tool.risk_levels)
        blocked_tools.append({
            "name": tool.name,
            "reason": decision.reason,
            "risk_levels": list(decision.risk_levels),
            "requires_approval": decision.requires_approval,
        })
    return {
        "registered_tool_count": len(registered),
        "exposed_tool_count": len(exposed_tools),
        "blocked_tool_count": len(blocked_tools),
        "exposed_tools": exposed_tools,
        "blocked_tools": blocked_tools,
    }
