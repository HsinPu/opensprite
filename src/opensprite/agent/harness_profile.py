"""Harness profile selection for one agent turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..tools.evidence import WEB_RESEARCH_TASK_TYPE, WEB_RESEARCH_TOOL_GROUP, WEB_SOURCE_REQUIRED_EVIDENCE

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
WORKSPACE_WRITE_TOOL_GROUP = "workspace_write"
WORKSPACE_READ_TOOL_GROUP = "workspace_read"
VERIFICATION_TOOL_GROUP = "verification"
HISTORY_RETRIEVAL_TOOL_GROUP = "history_retrieval"
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
