"""Harness profile selection for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .task_intent import TaskIntent


_NO_WEB_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\s+(?:use\s+)?(?:the\s+)?(?:web|internet|online|search|browse|sources?)\b"
    r"|\b(?:do not|don't|dont)\b[^.?!\n]{0,80}\b(?:use\s+)?(?:the\s+)?(?:web|internet|online|search|browse|sources?)\b"
    r"|\b(?:do not|don't|dont)\s+(?:search|browse|look\s+up|google)\b"
    r"|\b(?:offline|no\s+internet|no\s+web|no\s+search)\b",
    re.IGNORECASE,
)
_NO_WORKSPACE_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\s+(?:read|inspect|access|open|use)?\s*(?:files?|workspace|repo|repository|codebase)\b"
    r"|\b(?:do not|don't|dont)\s+(?:read|inspect|access|open)\s+(?:files?|workspace|repo|repository|codebase)\b"
    r"|\b(?:no\s+file\s+access|no\s+workspace\s+access)\b",
    re.IGNORECASE,
)
_NO_TOOL_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\s+(?:call|use|invoke|run)\s+(?:any\s+)?tools?\b",
    re.IGNORECASE,
)
_NO_WEB_LITERAL_PHRASES = (
    "\u4e0d\u8981\u4e0a\u7db2",
    "\u4e0d\u7528\u4e0a\u7db2",
    "\u5225\u4e0a\u7db2",
    "\u4e0d\u8981\u67e5\u7db2\u8def",
    "\u4e0d\u7528\u67e5\u7db2\u8def",
    "\u4e0d\u8981\u641c\u5c0b",
    "\u4e0d\u7528\u641c\u5c0b",
    "\u4e0d\u8981\u5916\u90e8\u4f86\u6e90",
    "\u4e0d\u7528\u5916\u90e8\u4f86\u6e90",
    "\u4e0d\u8981\u7528 web",
    "\u4e0d\u8981\u7528web",
    "\u4e0d\u8981 web_search",
    "\u4e0d\u8981 web_research",
)
_NO_WORKSPACE_LITERAL_PHRASES = (
    "\u4e0d\u8981\u8b80\u6a94",
    "\u4e0d\u7528\u8b80\u6a94",
    "\u4e0d\u8981\u8b80\u53d6\u6a94\u6848",
    "\u4e0d\u7528\u8b80\u53d6\u6a94\u6848",
    "\u4e0d\u8981\u8b80\u6587\u4ef6",
    "\u4e0d\u8981\u770b\u6a94\u6848",
    "\u4e0d\u8981\u770b\u5c08\u6848",
    "\u4e0d\u8981\u67e5 workspace",
    "\u4e0d\u8981\u8b80 AGENTS.md",
)
_NO_TOOL_LITERAL_PHRASES = (
    "\u4e0d\u8981\u7528\u5de5\u5177",
    "\u4e0d\u7528\u5de5\u5177",
    "\u4e0d\u8981\u547c\u53eb\u5de5\u5177",
)
_WEB_DENIED_TOOLS = (
    "web_search",
    "web_fetch",
    "web_research",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_scroll",
    "browser_back",
    "browser_console",
)
_WORKSPACE_DENIED_TOOLS = (
    "read_file",
    "list_dir",
    "glob_files",
    "grep_files",
    "code_navigation",
    "read_skill",
    "list_run_file_changes",
    "preview_run_file_change_revert",
    "verify",
)
_NO_TOOL_DENIED_TOOLS = tuple(
    dict.fromkeys(
        _WEB_DENIED_TOOLS
        + _WORKSPACE_DENIED_TOOLS
        + (
            "apply_patch",
            "write_file",
            "edit_file",
            "configure_skill",
            "task_update",
            "configure_mcp",
            "configure_subagent",
            "credential_store",
            "exec",
            "process",
            "analyze_image",
            "ocr_image",
            "transcribe_audio",
            "analyze_video",
            "send_media",
            "delegate",
            "delegate_many",
            "run_workflow",
            "search_history",
            "cron",
            "batch",
        )
    )
)
_PROFILE_PRIORITY_ORDER = ("ops", "media", "coding", "research", "chat")


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
        if task_type == "operations":
            return HarnessProfile(
                name="ops",
                task_type="operations",
                required_tool_groups=("workspace_read",),
                required_evidence=("audit_trace",),
                verification_policy="validate_or_report",
                continuation_policy="approval_bounded",
                approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
                reason="task contract selected operations profile",
                selection_signals=("contract:operations",),
            )
        if "web_research" in tool_groups or task_type == "web_research":
            return HarnessProfile(
                name="research",
                task_type="web_research",
                required_tool_groups=("web_research",),
                required_evidence=("web_source", "source_reference"),
                verification_policy="source_grounded",
                continuation_policy="bounded_with_source_fetch",
                approval_required_risk_levels=("external_side_effect",),
                reason="task contract requires web research evidence",
                selection_signals=("contract:web_research",),
            )
        if task_type == "media_extraction" or "media" in tool_groups:
            return HarnessProfile(
                name="media",
                task_type="media_extraction",
                required_tool_groups=("media",),
                required_evidence=("media_artifact",),
                verification_policy="artifact_required",
                continuation_policy="bounded",
                reason="task contract requires media evidence",
                selection_signals=("contract:media",),
            )
        if "workspace_write" in tool_groups or "file_change" in requirement_kinds or task_type == "code_change":
            return HarnessProfile(
                name="coding",
                task_type="workspace_change",
                required_tool_groups=("workspace_read", "workspace_write"),
                required_evidence=("file_change",),
                verification_policy="focused_if_possible",
                continuation_policy="bounded_with_verification",
                approval_required_risk_levels=("external_side_effect", "configuration"),
                reason="task contract requires workspace changes",
                selection_signals=("contract:workspace_write",),
            )
        if "workspace_read" in tool_groups or task_type == "workspace_read":
            return HarnessProfile(
                name="coding",
                task_type="workspace_analysis",
                required_tool_groups=("workspace_read",),
                required_evidence=("workspace_evidence",),
                verification_policy="focused_if_possible",
                continuation_policy="bounded_with_verification",
                approval_required_risk_levels=("external_side_effect", "configuration"),
                reason="task contract requires workspace evidence",
                selection_signals=("contract:workspace_read",),
            )
        return HarnessProfile(
            name="chat",
            task_type=task_type or "pure_answer",
            verification_policy="none",
            continuation_policy="minimal",
            reason="task contract does not require tool-backed evidence",
            selection_signals=("contract:pure_answer",),
        )

    def chat_fallback(self, task_intent: TaskIntent) -> HarnessProfile:
        """Return the no-tool chat profile when no task contract is available."""
        return HarnessProfile(
            name="chat",
            task_type=task_intent.kind,
            verification_policy="none",
            continuation_policy="minimal",
            reason="legacy pre-contract selection defaults to chat",
            selection_signals=("legacy:fallback:chat",),
        )


def preview_harness_profiles() -> tuple[HarnessProfile, ...]:
    """Return representative profiles for settings policy previews."""
    return (
        HarnessProfile(
            name="chat",
            task_type="conversation",
            verification_policy="none",
            continuation_policy="minimal",
            reason="preview profile for low-risk chat turns",
        ),
        HarnessProfile(
            name="research",
            task_type="web_research",
            required_tool_groups=("web_research",),
            required_evidence=("web_source", "source_reference"),
            verification_policy="source_grounded",
            continuation_policy="bounded_with_source_fetch",
            approval_required_risk_levels=("external_side_effect",),
            reason="preview profile for source-grounded web research turns",
        ),
        HarnessProfile(
            name="coding",
            task_type="workspace_analysis",
            required_tool_groups=("workspace_read",),
            required_evidence=("workspace_evidence",),
            verification_policy="focused_if_possible",
            continuation_policy="bounded_with_verification",
            approval_required_risk_levels=("external_side_effect", "configuration"),
            reason="preview profile for workspace analysis turns",
        ),
        HarnessProfile(
            name="coding",
            task_type="workspace_change",
            required_tool_groups=("workspace_read", "workspace_write"),
            required_evidence=("file_change",),
            verification_policy="focused_if_possible",
            continuation_policy="bounded_with_verification",
            approval_required_risk_levels=("external_side_effect", "configuration"),
            reason="preview profile for workspace change turns",
        ),
        HarnessProfile(
            name="media",
            task_type="media_extraction",
            required_tool_groups=("media",),
            required_evidence=("media_artifact",),
            verification_policy="artifact_required",
            continuation_policy="bounded",
            reason="preview profile for media extraction turns",
        ),
        HarnessProfile(
            name="ops",
            task_type="operations",
            required_tool_groups=("workspace_read",),
            required_evidence=("audit_trace",),
            verification_policy="validate_or_report",
            continuation_policy="approval_bounded",
            approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
            reason="preview profile for operations turns",
        ),
    )


def has_no_web_constraint(text: str) -> bool:
    """Return whether the user explicitly forbids web/search evidence."""
    text = text or ""
    lowered = text.lower()
    return bool(_NO_WEB_RE.search(text) or any(phrase.lower() in lowered for phrase in _NO_WEB_LITERAL_PHRASES))


def has_no_workspace_constraint(text: str) -> bool:
    """Return whether the user explicitly forbids file/workspace inspection."""
    text = text or ""
    lowered = text.lower()
    return bool(
        _NO_WORKSPACE_RE.search(text)
        or any(phrase.lower() in lowered for phrase in _NO_WORKSPACE_LITERAL_PHRASES)
    )


def has_no_tool_constraint(text: str) -> bool:
    """Return whether the user explicitly forbids tool calls."""
    text = text or ""
    lowered = text.lower()
    return bool(_NO_TOOL_RE.search(text) or any(phrase.lower() in lowered for phrase in _NO_TOOL_LITERAL_PHRASES))


def denied_tools_for_constraints(text: str) -> tuple[str, ...]:
    """Return exact tools that must not be exposed for explicit user constraints."""
    denied: list[str] = []
    if has_no_tool_constraint(text):
        denied.extend(_NO_TOOL_DENIED_TOOLS)
    if has_no_web_constraint(text):
        denied.extend(_WEB_DENIED_TOOLS)
    if has_no_workspace_constraint(text):
        denied.extend(_WORKSPACE_DENIED_TOOLS)
    return tuple(dict.fromkeys(denied))


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
