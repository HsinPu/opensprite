"""Tool access, permission resolution, and loop guardrail policies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from ..permission_constants import ALL_RISK_LEVELS, ALL_RISK_LEVELS_ORDER, denied_risks_except
from ..tool_names import (
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
    READ_SKILL_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    SEND_MEDIA_TOOL_NAME,
    TASK_UPDATE_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    WORKSPACE_WRITE_TOOL_NAMES,
)
from ..tools import BatchTool, ToolRegistry
from ..tools.approval import DEFAULT_PERMISSION_DENIAL_REASON, PermissionRequest, PermissionRequestManager
from ..tools.evidence import VERIFICATION_TOOL_NAME, WEB_SOURCE_EVIDENCE_TOOLS
from ..tools.permissions import (
    CompositeToolPermissionPolicy,
    PermissionApprovalResult,
    PermissionDecision,
    ToolPermissionPolicy,
)
from ..utils import json_safe_value
from .harness_policy import HarnessPolicy, HarnessPolicyService
from .retrieval import HISTORY_SEARCH_TOOL_NAME
from .tool_groups import WORKSPACE_DISCOVERY_TOOLS


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


IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        *WORKSPACE_DISCOVERY_TOOLS,
        BATCH_TOOL_NAME,
        READ_SKILL_TOOL_NAME,
        HISTORY_SEARCH_TOOL_NAME,
        *WEB_SOURCE_EVIDENCE_TOOLS,
    }
)

MUTATING_TOOL_NAMES = frozenset(
    {
        *WORKSPACE_WRITE_TOOL_NAMES,
        *EXECUTION_TOOL_NAMES,
        VERIFICATION_TOOL_NAME,
        *DELEGATED_EXECUTION_TOOL_NAMES,
        "workflow",
        CONFIGURE_SKILL_TOOL_NAME,
        CONFIGURE_SUBAGENT_TOOL_NAME,
        CONFIGURE_MCP_TOOL_NAME,
        CREDENTIAL_STORE_TOOL_NAME,
        CRON_TOOL_NAME,
        SEND_MEDIA_TOOL_NAME,
    }
)


@dataclass(frozen=True)
class ToolLoopGuardrailConfig:
    """Thresholds for one execution loop's repeated tool-call detection."""

    repeated_failure_warn_after: int = 2
    repeated_failure_block_after: int = 3
    same_result_warn_after: int = 2
    same_result_block_after: int = 3
    idempotent_tools: frozenset[str] = field(default_factory=lambda: IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: MUTATING_TOOL_NAMES)


@dataclass(frozen=True)
class ToolCallSignature:
    """Stable non-reversible identity for a tool name plus canonical args."""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        return cls(tool_name=tool_name, args_hash=_sha256(_canonical_args(args or {})))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolLoopGuardrailDecision:
    """Decision returned for a tool-call loop observation."""

    action: str = "allow"  # allow | warn | block
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            payload["signature"] = self.signature.to_metadata()
        return payload


class ToolLoopGuardrail:
    """Track repeated failed or non-progressing tool calls within one run."""

    def __init__(self, config: ToolLoopGuardrailConfig | None = None):
        self.config = config or ToolLoopGuardrailConfig()
        self._failure_counts: dict[ToolCallSignature, int] = {}
        self._same_result_counts: dict[ToolCallSignature, tuple[str, int]] = {}

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolLoopGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        failure_count = self._failure_counts.get(signature, 0)
        if failure_count >= self.config.repeated_failure_block_after:
            return ToolLoopGuardrailDecision(
                action="block",
                code="repeated_failure_block",
                message=(
                    f"Blocked {tool_name}: the same tool call failed {failure_count} times. "
                    "Stop retrying it unchanged; inspect the error and change strategy."
                ),
                tool_name=tool_name,
                count=failure_count,
                signature=signature,
            )

        if self._is_idempotent(tool_name):
            previous = self._same_result_counts.get(signature)
            if previous is not None and previous[1] >= self.config.same_result_block_after:
                return ToolLoopGuardrailDecision(
                    action="block",
                    code="same_result_block",
                    message=(
                        f"Blocked {tool_name}: this read-only call returned the same result "
                        f"{previous[1]} times. Use the result already provided or change the query."
                    ),
                    tool_name=tool_name,
                    count=previous[1],
                    signature=signature,
                )

        return ToolLoopGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str,
        *,
        failed: bool,
    ) -> ToolLoopGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        if failed:
            failure_count = self._failure_counts.get(signature, 0) + 1
            self._failure_counts[signature] = failure_count
            self._same_result_counts.pop(signature, None)
            if failure_count >= self.config.repeated_failure_warn_after:
                return ToolLoopGuardrailDecision(
                    action="warn",
                    code="repeated_failure_warning",
                    message=(
                        f"{tool_name} has failed {failure_count} times with identical arguments. "
                        "This looks like a loop; change strategy before retrying."
                    ),
                    tool_name=tool_name,
                    count=failure_count,
                    signature=signature,
                )
            return ToolLoopGuardrailDecision(tool_name=tool_name, count=failure_count, signature=signature)

        self._failure_counts.pop(signature, None)
        if not self._is_idempotent(tool_name):
            self._same_result_counts.pop(signature, None)
            return ToolLoopGuardrailDecision(tool_name=tool_name, signature=signature)

        result_hash = _result_hash(result)
        previous = self._same_result_counts.get(signature)
        repeat_count = 1
        if previous is not None and previous[0] == result_hash:
            repeat_count = previous[1] + 1
        self._same_result_counts[signature] = (result_hash, repeat_count)
        if repeat_count >= self.config.same_result_warn_after:
            return ToolLoopGuardrailDecision(
                action="warn",
                code="same_result_warning",
                message=(
                    f"{tool_name} returned the same result {repeat_count} times. "
                    "Use the result already provided or change the query instead of repeating it."
                ),
                tool_name=tool_name,
                count=repeat_count,
                signature=signature,
            )
        return ToolLoopGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools:
            return False
        return tool_name in self.config.idempotent_tools


def build_toolguard_synthetic_result(decision: ToolLoopGuardrailDecision) -> str:
    """Build a synthetic tool result for a blocked call."""

    return json.dumps(
        {
            "ok": False,
            "error": decision.message,
            "error_type": "ToolGuardrailError",
            "category": "tool_guardrail",
            "guardrail": decision.to_metadata(),
        },
        ensure_ascii=False,
    )


def append_toolguard_guidance(result: str, decision: ToolLoopGuardrailDecision) -> str:
    """Append warning guidance to a tool result when useful."""

    if decision.action != "warn" or not decision.message:
        return result
    return f"{result}\n\n[Tool loop warning: {decision.code}; count={decision.count}; {decision.message}]"


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _canonical_args(args: Mapping[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _result_hash(result: str) -> str:
    text = str(result or "")
    try:
        parsed = json.loads(text)
    except Exception:
        return _sha256(text)
    try:
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        canonical = str(parsed)
    return _sha256(canonical)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
