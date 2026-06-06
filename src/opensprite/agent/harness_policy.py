"""Runtime tool and approval policy derived from a harness profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal

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
    preview_harness_profiles,
)
from .retrieval import HISTORY_SEARCH_TOOL_NAME
from .tool_groups import WORKSPACE_DISCOVERY_TOOLS
from ..tools.evidence import WEB_HARNESS_RESEARCH_TOOLS, is_web_source_artifact_kind, is_web_source_evidence_tool
from .completion_status import is_complete_completion_status
from .media import count_media_artifacts

if TYPE_CHECKING:
    from .completion_gate import CompletionGateResult
    from .execution import ExecutionResult


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
