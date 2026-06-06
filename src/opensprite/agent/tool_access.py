"""Tool access, permission resolution, and loop guardrail policies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from ..config import CronMessagesConfig, SearchConfig, ToolsConfig
from ..config.defaults import DEFAULT_BROWSER_COMMAND_TIMEOUT, DEFAULT_BROWSER_SESSION_TIMEOUT
from ..cron import CronManager
from ..documents.memory import MemoryStore
from ..documents.safety import DurableMemorySafetyError
from ..media import MediaRouter
from ..permission_constants import ALL_RISK_LEVELS, ALL_RISK_LEVELS_ORDER, denied_risks_except
from ..search.base import SearchStore
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
from ..tools.active_task import TaskUpdateTool
from ..tools.approval import DEFAULT_PERMISSION_DENIAL_REASON, PermissionRequest, PermissionRequestManager
from ..tools.audio import TranscribeAudioTool
from ..tools.base import Tool
from ..tools.batch import BatchTool
from ..tools.browser import (
    BrowserBackTool,
    BrowserClickTool,
    BrowserConsoleTool,
    BrowserNavigateTool,
    BrowserPressTool,
    BrowserScrollTool,
    BrowserSnapshotTool,
    BrowserTypeTool,
)
from ..tools.browser_runtime import AgentBrowserRuntime, cloud_provider_from_config
from ..tools.code_navigation import CodeNavigationTool
from ..tools.credential_store import CredentialStoreTool
from ..tools.cron import CronTool
from ..tools.evidence import VERIFICATION_TOOL_NAME, WEB_SOURCE_EVIDENCE_TOOLS
from ..tools.filesystem import (
    ApplyPatchTool,
    EditFileTool,
    GlobFilesTool,
    GrepFilesTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from ..tools.image import AnalyzeImageTool, OCRImageTool
from ..tools.mcp_config import ConfigureMCPTool
from ..tools.outbound_media import SendMediaTool
from ..tools.permissions import (
    CompositeToolPermissionPolicy,
    PermissionApprovalResult,
    PermissionDecision,
    ToolPermissionPolicy,
)
from ..tools.process import ProcessTool
from ..tools.process_runtime import BackgroundProcessManager
from ..tools.registry import ToolRegistry
from ..tools.result_status import tool_error_result
from ..tools.run_trace import ListRunFileChangesTool, PreviewRunFileChangeRevertTool
from ..tools.search import SearchHistoryTool
from ..tools.shell import ExecTool
from ..tools.skill import ReadSkillTool
from ..tools.skill_config import ConfigureSkillTool
from ..tools.verify import VerifyTool
from ..tools.video import AnalyzeVideoTool
from ..tools.web_fetch import WebFetchTool
from ..tools.web_research import WebResearchTool
from ..tools.web_search import WebSearchTool
from ..tools.workflow import RunWorkflowTool
from ..utils import json_safe_value
from .harness_policy import HarnessPolicy, HarnessPolicyService, WORKSPACE_DISCOVERY_TOOLS
from ..media import outbound_media_error_result
from ..context.message_history import HISTORY_SEARCH_TOOL_NAME


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


BROWSER_TOOL_NAMES = (
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_scroll",
    "browser_back",
    "browser_console",
)


def _save_memory_error_result(
    message: str,
    *,
    category: str,
    invalid_arguments: bool = False,
) -> str:
    error = str(message or "").strip()
    return tool_error_result(
        error,
        error_type="SaveMemoryToolError",
        category=category,
        repeated_error_key=error if invalid_arguments else None,
        invalid_arguments=invalid_arguments,
        metadata={"tool_name": "save_memory"},
    )


class SaveMemoryTool(Tool):
    name = "save_memory"
    description = (
        "Save durable chat-continuity information to session MEMORY.md. Include all existing durable facts plus "
        "new decisions, important session facts, and open issues. Keep entries concise and deduplicated. Do not "
        "store one-off tasks, raw logs, secrets, credentials, prompt-injection text, or details better kept in "
        "USER.md, ACTIVE_TASK.md, RECENT_SUMMARY.md, or search history."
    )
    parameters = {
        "type": "object",
        "properties": {
            "memory_update": {
                "type": "string",
                "description": (
                    "Full replacement MEMORY.md markdown. Preserve existing durable chat continuity, add only "
                    "stable session facts, decisions, and open issues, and remove resolved or unsafe content."
                ),
            }
        },
        "required": ["memory_update"],
    }

    def __init__(self, memory_store: MemoryStore, get_session_id: Callable[[], str | None]):
        self.memory_store = memory_store
        self.get_session_id = get_session_id

    async def _execute(self, memory_update: str, **kwargs: Any) -> str:
        session_id = self.get_session_id()
        if not session_id:
            return _save_memory_error_result(
                "current session_id is unavailable. save_memory requires an active session context.",
                category="missing_session_context",
            )
        current = self.memory_store.read(session_id)
        if memory_update != current:
            try:
                self.memory_store.write(session_id, memory_update)
            except DurableMemorySafetyError as exc:
                return _save_memory_error_result(
                    str(exc),
                    category="unsafe_memory_content",
                    invalid_arguments=True,
                )
            return f"Memory saved ({len(memory_update):,} chars; delta {len(memory_update) - len(current):+,} chars)"
        return f"Memory unchanged ({len(current):,} chars)"


def register_memory_tool(
    registry: ToolRegistry,
    memory_store: MemoryStore,
    get_session_id: Callable[[], str | None],
) -> None:
    """Register the long-term memory update tool."""
    registry.register(SaveMemoryTool(memory_store, get_session_id))


def register_task_tools(
    registry: ToolRegistry,
    *,
    get_session_id: Callable[[], str | None],
    active_task_store_factory: Callable[[str], Any | None] | None = None,
    get_message_count: Callable[[str], Awaitable[int]] | None = None,
) -> None:
    """Register explicit active-task state management tools."""
    registry.register(
        TaskUpdateTool(
            get_session_id=get_session_id,
            active_task_store_factory=active_task_store_factory,
            get_message_count=get_message_count,
        )
    )


def register_run_trace_tools(
    registry: ToolRegistry,
    *,
    storage: Any,
    get_session_id: Callable[[], str | None],
    preview_run_file_change_revert: Callable[[str, str, int], Awaitable[dict[str, Any]]],
) -> None:
    """Register read-only run trace inspection tools."""
    registry.register(ListRunFileChangesTool(storage=storage, get_session_id=get_session_id))
    registry.register(
        PreviewRunFileChangeRevertTool(
            get_session_id=get_session_id,
            preview_revert=preview_run_file_change_revert,
        )
    )


def register_filesystem_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    skills_loader: Any = None,
    config_path_resolver: Callable[[], Path | None] | None = None,
    file_change_recorder: Callable[[str, list[dict[str, Any]]], Awaitable[None]] | None = None,
) -> None:
    """Register filesystem-oriented tools."""
    registry.register(ReadFileTool(workspace_resolver=workspace_resolver, skills_loader=skills_loader))
    registry.register(GlobFilesTool(workspace_resolver=workspace_resolver))
    registry.register(GrepFilesTool(workspace_resolver=workspace_resolver))
    registry.register(CodeNavigationTool(workspace_resolver=workspace_resolver))
    registry.register(
        ApplyPatchTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
            file_change_recorder=file_change_recorder,
        )
    )
    registry.register(
        WriteFileTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
            file_change_recorder=file_change_recorder,
        )
    )
    registry.register(
        EditFileTool(
            workspace_resolver=workspace_resolver,
            config_path_resolver=config_path_resolver,
            file_change_recorder=file_change_recorder,
        )
    )
    registry.register(ListDirTool(workspace_resolver=workspace_resolver))


def register_skill_tools(
    registry: ToolRegistry,
    *,
    skills_loader: Any = None,
    workspace_resolver: Callable[[], Path],
) -> None:
    """Register optional skill-loading tools."""
    if skills_loader:
        registry.register(
            ReadSkillTool(
                skills_loader=skills_loader,
                personal_skills_dir_resolver=lambda: workspace_resolver() / "skills",
            )
        )
        registry.register(
            ConfigureSkillTool(
                skills_loader=skills_loader,
                workspace_resolver=workspace_resolver,
            )
        )


def register_shell_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    tools_config: ToolsConfig | None = None,
    background_notification_factory: Callable[[], Any | None] | None = None,
    background_session_owner_factory: Callable[[], dict[str, str | None] | None] | None = None,
    process_manager_callback: Callable[[Any], None] | None = None,
    storage: Any = None,
) -> None:
    """Register shell execution tools."""
    current_tools_config = tools_config or ToolsConfig()
    process_tool = ProcessTool(manager=BackgroundProcessManager(storage=storage))
    if process_manager_callback is not None:
        process_manager_callback(process_tool.manager)
    registry.register(
        ExecTool(
            workspace_resolver=workspace_resolver,
            timeout=current_tools_config.exec_tool.timeout,
            process_manager=process_tool.manager,
            background_notification_factory=background_notification_factory,
            background_session_owner_factory=background_session_owner_factory,
            notify_on_exit=current_tools_config.exec_tool.notify_on_exit,
            notify_on_exit_empty_success=current_tools_config.exec_tool.notify_on_exit_empty_success,
        )
    )
    registry.register(process_tool)


def register_verify_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
) -> None:
    """Register fixed project verification checks."""
    registry.register(VerifyTool(workspace_resolver=workspace_resolver))


def register_config_tools(
    registry: ToolRegistry,
    *,
    config_path_resolver: Callable[[], Path | None],
    reload_mcp: Callable[[], Awaitable[str]],
    app_home: Path | None = None,
    workspace_resolver: Callable[[], Path] | None = None,
) -> None:
    """Register tools that safely update application configuration."""
    from ..tools.subagent_config import ConfigureSubagentTool

    registry.register(
        ConfigureMCPTool(
            config_path_resolver=config_path_resolver,
            reload_callback=reload_mcp,
        )
    )
    registry.register(
        ConfigureSubagentTool(
            app_home=app_home,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(CredentialStoreTool(app_home=app_home))


def register_web_tools(
    registry: ToolRegistry,
    *,
    tools_config: ToolsConfig | None = None,
    get_session_id: Callable[[], str | None] | None = None,
) -> None:
    """Register web search and fetch tools."""
    current_tools_config = tools_config or ToolsConfig()
    web_search_config = current_tools_config.web_search
    web_fetch_config = current_tools_config.web_fetch

    registry.register(WebSearchTool(config=web_search_config))
    registry.register(
        WebFetchTool(
            max_chars=web_fetch_config.max_chars,
            max_response_size=web_fetch_config.max_response_size,
            timeout=web_fetch_config.timeout,
            prefer_trafilatura=web_fetch_config.prefer_trafilatura,
            firecrawl_api_key=web_fetch_config.firecrawl_api_key,
        )
    )
    registry.register(
        WebResearchTool(
            search_config=web_search_config,
            fetch_config=web_fetch_config,
        )
    )


def register_browser_tools(
    registry: ToolRegistry,
    *,
    get_session_id: Callable[[], str | None],
    tools_config: ToolsConfig | None = None,
) -> None:
    """Register local browser automation tools."""
    browser_config = getattr(tools_config, "browser", None) if tools_config is not None else None
    if browser_config is not None and not browser_config.enabled:
        return
    runtime = AgentBrowserRuntime(
        command_timeout=getattr(browser_config, "command_timeout", DEFAULT_BROWSER_COMMAND_TIMEOUT),
        session_timeout=getattr(browser_config, "session_timeout", DEFAULT_BROWSER_SESSION_TIMEOUT),
        cdp_url=getattr(browser_config, "cdp_url", ""),
        launch_args=getattr(browser_config, "launch_args", ""),
        cloud_provider=cloud_provider_from_config(browser_config) if browser_config is not None else None,
    )
    kwargs = {"get_session_id": get_session_id, "runtime": runtime, "browser_config": browser_config}
    registry.register(BrowserNavigateTool(**kwargs))
    registry.register(BrowserSnapshotTool(**kwargs))
    registry.register(BrowserClickTool(**kwargs))
    registry.register(BrowserTypeTool(**kwargs))
    registry.register(BrowserPressTool(**kwargs))
    registry.register(BrowserScrollTool(**kwargs))
    registry.register(BrowserBackTool(**kwargs))
    registry.register(BrowserConsoleTool(**kwargs))


def register_media_tools(
    registry: ToolRegistry,
    *,
    media_router: MediaRouter | None = None,
    get_current_images: Callable[[], list[str] | None],
    get_current_audios: Callable[[], list[str] | None],
    get_current_videos: Callable[[], list[str] | None],
    workspace_resolver: Callable[[], Path] | None = None,
    queue_outbound_media: Callable[[str, str], str | None] | None = None,
) -> None:
    """Register media-analysis tools."""
    registry.register(
        AnalyzeImageTool(
            media_router or MediaRouter(),
            get_current_images=get_current_images,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        OCRImageTool(
            media_router or MediaRouter(),
            get_current_images=get_current_images,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        TranscribeAudioTool(
            media_router or MediaRouter(),
            get_current_audios=get_current_audios,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        AnalyzeVideoTool(
            media_router or MediaRouter(),
            get_current_videos=get_current_videos,
            workspace_resolver=workspace_resolver,
        )
    )
    registry.register(
        SendMediaTool(
            queue_media=queue_outbound_media
            or (
                lambda kind, payload: outbound_media_error_result(
                    "outbound media is unavailable.",
                    category="missing_turn_context",
                )
            ),
            get_current_images=get_current_images,
            get_current_audios=get_current_audios,
            get_current_videos=get_current_videos,
        )
    )


def register_delegate_tools(
    registry: ToolRegistry,
    *,
    run_subagent: Callable[[str, str | None, str | None], Awaitable[str]],
    run_subagents_many: Callable[[list[dict[str, Any]], int | None], Awaitable[str]] | None = None,
    app_home: Path | None = None,
    workspace_resolver: Callable[[], Path] | None = None,
) -> None:
    """Register delegated subagent execution tools."""
    from ..tools.delegate import DelegateTool

    registry.register(
        DelegateTool(
            run_subagent=run_subagent,
            app_home=app_home,
            workspace_resolver=workspace_resolver,
        )
    )
    if run_subagents_many is not None:
        from ..tools.delegate_many import DelegateManyTool

        registry.register(
            DelegateManyTool(
                run_subagents_many=run_subagents_many,
                app_home=app_home,
                workspace_resolver=workspace_resolver,
            )
        )


def register_workflow_tools(
    registry: ToolRegistry,
    *,
    run_workflow: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    workflow_catalog_getter: Callable[[], dict[str, str]] | None = None,
) -> None:
    """Register fixed orchestration workflow tools."""
    if run_workflow is None or workflow_catalog_getter is None:
        return
    registry.register(
        RunWorkflowTool(
            run_workflow=run_workflow,
            workflow_catalog_getter=workflow_catalog_getter,
        )
    )


def register_search_tools(
    registry: ToolRegistry,
    *,
    search_store: SearchStore | None = None,
    search_config: SearchConfig | None = None,
    get_session_id: Callable[[], str | None],
) -> None:
    """Register per-session search tools when search is enabled."""
    if search_store is None:
        return

    current_search_config = search_config or SearchConfig()
    registry.register(
        SearchHistoryTool(
            store=search_store,
            get_session_id=get_session_id,
            default_limit=current_search_config.history_top_k,
        )
    )


def register_cron_tools(
    registry: ToolRegistry,
    *,
    cron_manager: CronManager | None = None,
    tools_config: ToolsConfig | None = None,
    messages_config: CronMessagesConfig | None = None,
    get_session_id: Callable[[], str | None],
) -> None:
    """Register per-session cron scheduling tools when cron is enabled."""
    current_tools_config = tools_config or ToolsConfig()
    registry.register(
        CronTool(
            cron_manager,
            get_session_id=get_session_id,
            default_timezone=current_tools_config.cron.default_timezone,
            messages_config=messages_config,
        )
    )


def register_batch_tools(registry: ToolRegistry) -> None:
    """Register safe parallel read-only batch execution."""
    registry.register(BatchTool(registry_resolver=lambda: registry))


def register_default_tools(
    registry: ToolRegistry,
    *,
    workspace_resolver: Callable[[], Path],
    get_session_id: Callable[[], str | None],
    run_subagent: Callable[[str, str | None, str | None], Awaitable[str]],
    run_subagents_many: Callable[[list[dict[str, Any]], int | None], Awaitable[str]] | None = None,
    run_workflow: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    workflow_catalog_getter: Callable[[], dict[str, str]] | None = None,
    config_path_resolver: Callable[[], Path | None],
    reload_mcp: Callable[[], Awaitable[str]],
    app_home: Path | None = None,
    skills_loader: Any = None,
    tools_config: ToolsConfig | None = None,
    search_store: SearchStore | None = None,
    search_config: SearchConfig | None = None,
    cron_manager: CronManager | None = None,
    cron_messages_config: CronMessagesConfig | None = None,
    media_router: MediaRouter | None = None,
    get_current_images: Callable[[], list[str] | None] | None = None,
    get_current_audios: Callable[[], list[str] | None] | None = None,
    get_current_videos: Callable[[], list[str] | None] | None = None,
    queue_outbound_media: Callable[[str, str], str | None] | None = None,
    background_notification_factory: Callable[[], Any | None] | None = None,
    background_session_owner_factory: Callable[[], dict[str, str | None] | None] | None = None,
    process_manager_callback: Callable[[Any], None] | None = None,
    active_task_store_factory: Callable[[str], Any | None] | None = None,
    get_message_count: Callable[[str], Awaitable[int]] | None = None,
    file_change_recorder: Callable[[str, list[dict[str, Any]]], Awaitable[None]] | None = None,
    storage: Any = None,
    preview_run_file_change_revert: Callable[[str, str, int], Awaitable[dict[str, Any]]] | None = None,
) -> None:
    """Register the built-in tools used by AgentLoop."""
    current_tools_config = tools_config or ToolsConfig()
    registry.set_permission_policy(ToolPermissionPolicy.from_config(current_tools_config.permissions))
    register_filesystem_tools(
        registry,
        workspace_resolver=workspace_resolver,
        skills_loader=skills_loader,
        config_path_resolver=config_path_resolver,
        file_change_recorder=file_change_recorder,
    )
    register_skill_tools(
        registry,
        skills_loader=skills_loader,
        workspace_resolver=workspace_resolver,
    )
    register_task_tools(
        registry,
        get_session_id=get_session_id,
        active_task_store_factory=active_task_store_factory,
        get_message_count=get_message_count,
    )
    if storage is not None and preview_run_file_change_revert is not None:
        register_run_trace_tools(
            registry,
            storage=storage,
            get_session_id=get_session_id,
            preview_run_file_change_revert=preview_run_file_change_revert,
        )
    register_config_tools(
        registry,
        config_path_resolver=config_path_resolver,
        reload_mcp=reload_mcp,
        app_home=app_home,
        workspace_resolver=workspace_resolver,
    )
    register_shell_tools(
        registry,
        workspace_resolver=workspace_resolver,
        tools_config=current_tools_config,
        background_notification_factory=background_notification_factory,
        background_session_owner_factory=background_session_owner_factory,
        process_manager_callback=process_manager_callback,
        storage=storage,
    )
    register_verify_tools(registry, workspace_resolver=workspace_resolver)
    register_web_tools(
        registry,
        tools_config=current_tools_config,
        get_session_id=get_session_id,
    )
    register_browser_tools(registry, get_session_id=get_session_id, tools_config=current_tools_config)
    register_media_tools(
        registry,
        media_router=media_router,
        get_current_images=get_current_images or (lambda: None),
        get_current_audios=get_current_audios or (lambda: None),
        get_current_videos=get_current_videos or (lambda: None),
        workspace_resolver=workspace_resolver,
        queue_outbound_media=queue_outbound_media,
    )
    register_delegate_tools(
        registry,
        run_subagent=run_subagent,
        run_subagents_many=run_subagents_many,
        app_home=app_home,
        workspace_resolver=workspace_resolver,
    )
    register_workflow_tools(
        registry,
        run_workflow=run_workflow,
        workflow_catalog_getter=workflow_catalog_getter,
    )
    register_search_tools(
        registry,
        search_store=search_store,
        search_config=search_config,
        get_session_id=get_session_id,
    )
    register_cron_tools(
        registry,
        cron_manager=cron_manager,
        tools_config=current_tools_config,
        messages_config=cron_messages_config,
        get_session_id=get_session_id,
    )
    register_batch_tools(registry)
