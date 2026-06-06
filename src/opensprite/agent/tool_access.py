"""Tool access, harness policy, permission resolution, and loop guardrails."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..config import AgentConfig, ToolsConfig
from ..context.message_history import HISTORY_SEARCH_TOOL_NAME
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

from ..harness import (
    ANALYSIS_TASK_TYPE,
    CHAT_HARNESS_POLICY_REASON,
    CHAT_PROFILE_NAME,
    CODE_CHANGE_TASK_TYPE,
    CODING_PROFILE_NAME,
    CONTRACT_MEDIA_PROFILE_REASON,
    CONTRACT_OPERATIONS_PROFILE_REASON,
    CONTRACT_PLANNING_PROFILE_REASON,
    CONTRACT_PURE_ANSWER_PROFILE_REASON,
    CONTRACT_WEB_RESEARCH_PROFILE_REASON,
    CONTRACT_WORKSPACE_CHANGE_PROFILE_REASON,
    CONTRACT_WORKSPACE_EVIDENCE_PROFILE_REASON,
    DEFAULT_CHAT_PROFILE_REASON,
    EXECUTION_TOOL_GROUP,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    HARNESS_APPROVAL_REQUIREMENT_PROTECTED_REASON,
    HARNESS_SENSOR_FAIL_STATUS,
    HARNESS_SENSOR_NOT_APPLICABLE_STATUS,
    HARNESS_SENSOR_PASS_STATUS,
    HARNESS_SENSOR_WARN_STATUS,
    HISTORY_RETRIEVAL_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP,
    MEDIA_EXTRACTION_TASK_TYPE,
    MEDIA_HARNESS_POLICY_REASON,
    MEDIA_PROFILE_NAME,
    MEDIA_TOOL_GROUP,
    OPERATIONS_HARNESS_POLICY_REASON,
    OPERATIONS_TASK_TYPE,
    OPERATION_TOOL_GROUPS,
    PLANNING_TASK_TYPE,
    POLICY_RESOLUTION_METADATA_REASON,
    PREVIEW_CHAT_PROFILE_REASON,
    PREVIEW_MEDIA_PROFILE_REASON,
    PREVIEW_OPERATIONS_PROFILE_REASON,
    PREVIEW_WEB_RESEARCH_PROFILE_REASON,
    PREVIEW_WORKSPACE_ANALYSIS_PROFILE_REASON,
    PREVIEW_WORKSPACE_CHANGE_PROFILE_REASON,
    PURE_ANSWER_TASK_TYPE,
    RESEARCH_HARNESS_POLICY_REASON,
    RESEARCH_PROFILE_NAME,
    SCHEDULING_TOOL_GROUP,
    SENSOR_CHAT_NO_UNEXPECTED_TOOLS,
    SENSOR_CODING_FILE_CHANGE,
    SENSOR_CODING_VERIFICATION,
    SENSOR_CODING_WORKSPACE_EVIDENCE,
    SENSOR_COMPLETION_CHANGE_SUMMARY,
    SENSOR_COMPLETION_FINAL_ANSWER,
    SENSOR_COMPLETION_MEDIA_SUMMARY,
    SENSOR_COMPLETION_OPERATION_REPORT,
    SENSOR_COMPLETION_SOURCE_GROUNDING,
    SENSOR_COMPLETION_VERIFICATION_OR_GAP,
    SENSOR_IDS_BY_TASK_TYPE,
    SENSOR_MEDIA_ARTIFACT,
    SENSOR_OPS_APPROVAL_BOUNDARY,
    SENSOR_OPS_AUDIT_TRACE,
    SENSOR_RESEARCH_FRESHNESS,
    SENSOR_RESEARCH_SOURCE_COVERAGE,
    TASK_TYPE_BY_TOOL_GROUP,
    TOOL_GROUPS,
    TOOL_GROUP_BY_TOOL_NAME,
    VERIFICATION_REQUIREMENT_KIND,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_ANALYSIS_HARNESS_POLICY_REASON,
    WORKSPACE_ANALYSIS_TASK_TYPE,
    WORKSPACE_CHANGE_TASK_TYPE,
    WORKSPACE_DISCOVERY_TOOLS,
    WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP,
    WORKSPACE_WRITE_TOOL_GROUP,
    HarnessCheckStatus,
    HarnessInventoryItem,
    HarnessPolicy,
    HarnessPolicyService,
    HarnessProfile,
    HarnessProfileService,
    HarnessScorecard,
    HarnessSensorResult,
    build_harness_inventory,
    evaluate_harness_sensors,
    expected_sensor_ids_for_task_type,
    harness_inventory_payload,
    harness_profile_follow_up_instruction,
    is_chat_profile_name,
    is_coding_profile_name,
    is_media_profile_name,
    is_ops_profile_name,
    is_planning_task_type,
    is_research_profile_name,
    normalize_profile_name,
    preview_harness_profiles,
)



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
