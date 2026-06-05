"""Task contracts and evidence requirements for completion checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..llms import ChatMessage, is_unconfigured_llm
from .harness_profile import (
    ANALYSIS_TASK_TYPE,
    CODE_CHANGE_TASK_TYPE,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP,
    MEDIA_EXTRACTION_TASK_TYPE,
    MEDIA_TOOL_GROUP,
    OPERATIONS_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    VERIFICATION_REQUIREMENT_KIND,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_CHANGE_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP,
    WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP,
)
from .planner_capabilities import PlannerCapabilityCatalog, build_planner_capability_catalog
from .resource_index import ResourceIndex, ResourceRef
from .task_context_resolver import TaskContextDecision, TaskContextResolver
from .task_intent import TaskIntent
from .tool_groups import OPERATION_TOOL_GROUPS, TOOL_GROUPS
from .web_source_policy import (
    SOURCE_ARTIFACT_CRITERION_KIND,
    SOURCE_DETAIL_CRITERION_KIND,
    SOURCE_REFERENCE_CRITERION_KIND,
    WEB_RESEARCH_TASK_TYPE,
    WEB_RESEARCH_TOOL_GROUP,
    is_web_research_tool_group,
)
from ..tools.evidence import ToolEvidence

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
PLANNER_VALIDATED_STATUS = "validated"
PLANNER_BLOCKED_STATUS = "blocked"
PLANNER_INVALID_STATUS = "invalid"
PLANNER_MISSING_STATUS = "missing"
PLANNER_METADATA_STATUS_FIELD = "planner_status"
PLANNER_METADATA_REASON_FIELD = "reason"
PLANNER_METADATA_RAW_RESPONSE_PREVIEW_FIELD = "raw_response_preview"
DETERMINISTIC_CONTRACT_SOURCE = "deterministic"
DETERMINISTIC_CONTRACT_SOURCES = (DETERMINISTIC_CONTRACT_SOURCE,)
LLM_PLANNER_CONTRACT_SOURCE = "llm_planner"
LLM_PLANNER_CONTRACT_SOURCES = (LLM_PLANNER_CONTRACT_SOURCE,)
MISSING_RUNTIME_CONTRACT_SOURCE = "missing_runtime_contract"
MISSING_RUNTIME_CONTRACT_SOURCES = (MISSING_RUNTIME_CONTRACT_SOURCE,)
MISSING_RUNTIME_CONTRACT_REASON = "execution result did not include a task contract"
PLANNER_UNAVAILABLE_REASON = "task planner unavailable: llm not configured"
PLANNER_INVALID_JSON_REASON = "task planner returned invalid JSON"
PLANNER_UNSUPPORTED_TASK_TYPE_REASON = "task planner returned an unsupported or missing task_type"
PLANNER_VALIDATED_REASON = "llm planner returned a task contract"
PLANNER_MEDIA_ANALYSIS_TASK_TYPE = "media_analysis"
PLANNER_OPS_TASK_TYPE = "ops"
PLANNING_ERROR_TASK_TYPE = "planning_error"
TOOL_GROUP_REQUIREMENT_KIND = "tool_group"
RESOURCE_COVERAGE_REQUIREMENT_KIND = "resource_coverage"
ALL_RESOURCE_COVERAGE = "all"
ITEMIZED_OUTPUT_CRITERION_KIND = "itemized_output"
SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND = "substantive_final_answer"
WORKSPACE_LOCATION_CRITERION_KIND = "workspace_location"
MEDIA_ARTIFACT_CRITERION_KIND = "media_artifact"
VERIFICATION_OR_GAP_CRITERION_KIND = "verification_or_gap"
OPERATION_REPORT_CRITERION_KIND = "operation_report"
COMMAND_VERSION_QUALITY_CHECK = "command_version"
REPOSITORY_STATUS_QUALITY_CHECK = "repository_status"
WORKSPACE_LOCATION_QUALITY_CHECK = "workspace_location"
_ALLOWED_PLANNER_TOOL_GROUPS = frozenset(TOOL_GROUPS.keys())
_ALLOWED_PLANNER_QUALITY_CHECKS = frozenset(
    {
        COMMAND_VERSION_QUALITY_CHECK,
        REPOSITORY_STATUS_QUALITY_CHECK,
        WORKSPACE_LOCATION_QUALITY_CHECK,
    }
)
_ALLOWED_PLANNER_TASK_TYPES = frozenset(
    {
        PURE_ANSWER_TASK_TYPE,
        WEB_RESEARCH_TASK_TYPE,
        WORKSPACE_READ_TASK_TYPE,
        WORKSPACE_CHANGE_TASK_TYPE,
        CODE_CHANGE_TASK_TYPE,
        PLANNER_MEDIA_ANALYSIS_TASK_TYPE,
        MEDIA_EXTRACTION_TASK_TYPE,
        PLANNING_TASK_TYPE,
        HISTORY_RETRIEVAL_TASK_TYPE,
        PLANNER_OPS_TASK_TYPE,
        OPERATIONS_TASK_TYPE,
        GENERIC_TASK_TYPE,
        ANALYSIS_TASK_TYPE,
    }
)
_PLANNER_TASK_TYPE_ALIASES = {
    WORKSPACE_CHANGE_TASK_TYPE: CODE_CHANGE_TASK_TYPE,
    PLANNER_MEDIA_ANALYSIS_TASK_TYPE: MEDIA_EXTRACTION_TASK_TYPE,
    PLANNER_OPS_TASK_TYPE: OPERATIONS_TASK_TYPE,
}
_PLANNER_TOOL_GROUP_ALIASES = {
    WORKSPACE_CHANGE_TASK_TYPE: WORKSPACE_WRITE_TOOL_GROUP,
    PLANNER_MEDIA_ANALYSIS_TASK_TYPE: MEDIA_TOOL_GROUP,
    PLANNER_OPS_TASK_TYPE: VERIFICATION_TOOL_GROUP,
}
LEGACY_FILE_CHANGE_TASK_TYPE_ALIASES = frozenset({"implementation", "refactor"})
FILE_CHANGE_TASK_TYPES = frozenset({CODE_CHANGE_TASK_TYPE, *LEGACY_FILE_CHANGE_TASK_TYPE_ALIASES})
_TASK_TYPE_REQUIRED_TOOL_GROUPS = {
    WEB_RESEARCH_TASK_TYPE: (WEB_RESEARCH_TOOL_GROUP,),
    WORKSPACE_READ_TASK_TYPE: (WORKSPACE_READ_TOOL_GROUP,),
    CODE_CHANGE_TASK_TYPE: (WORKSPACE_READ_TOOL_GROUP, WORKSPACE_WRITE_TOOL_GROUP),
    MEDIA_EXTRACTION_TASK_TYPE: (MEDIA_TOOL_GROUP,),
    HISTORY_RETRIEVAL_TASK_TYPE: (HISTORY_RETRIEVAL_TOOL_GROUP,),
}
_TASK_PLANNER_SYSTEM_PROMPT = (
    "You are the OpenSprite task planner. Decide what tool evidence the latest user turn needs "
    "before the main assistant sees tools. Return only one JSON object. Do not include markdown. "
    "Choose the smallest necessary set from the available runtime capabilities supplied in the user prompt. "
    "If no tool-backed evidence is needed, use pure_answer and an empty required_tool_groups array. "
    "The JSON keys are: objective, task_type, required_tool_groups, final_answer_required, allow_no_tool_final, reason."
)
_PLANNER_REPAIR_SYSTEM_PROMPT = (
    "You repair OpenSprite task planner output. Convert the invalid planner response into exactly one "
    "valid JSON object for the same schema. Return JSON only, no markdown, no explanation."
)
@dataclass(frozen=True)
class EvidenceRequirement:
    """Evidence needed before the task can be treated as complete."""

    kind: str
    tool_group: str = ""
    resource_ids: tuple[str, ...] = ()
    coverage: str = "any"
    min_count: int = 1
    description: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tool_group": self.tool_group,
            "resource_ids": list(self.resource_ids),
            "coverage": self.coverage,
            "min_count": self.min_count,
            "description": self.description,
        }


@dataclass(frozen=True)
class AcceptanceCriterion:
    """Answer-shape expectations needed for a high-quality final response."""

    kind: str
    min_count: int = 1
    min_response_chars: int = 0
    max_response_chars: int = 0
    description: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "min_count": self.min_count,
            "min_response_chars": self.min_response_chars,
            "max_response_chars": self.max_response_chars,
            "description": self.description,
        }


@dataclass(frozen=True)
class TaskContract:
    """Language-independent completion contract for one turn."""

    objective: str
    task_type: str
    requirements: tuple[EvidenceRequirement, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    selected_resources: tuple[ResourceRef, ...] = ()
    final_answer_required: bool = True
    allow_no_tool_final: bool = True
    contract_sources: tuple[str, ...] = DETERMINISTIC_CONTRACT_SOURCES
    harness_profile: dict[str, Any] | None = None
    planner_metadata: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "objective": self.objective,
            "task_type": self.task_type,
            "requirements": [item.to_metadata() for item in self.requirements],
            "acceptance_criteria": [item.to_metadata() for item in self.acceptance_criteria],
            "selected_resources": [item.to_metadata() for item in self.selected_resources],
            "final_answer_required": self.final_answer_required,
            "allow_no_tool_final": self.allow_no_tool_final,
            "contract_sources": list(self.contract_sources),
        }
        if self.planner_metadata:
            payload["planner_metadata"] = dict(self.planner_metadata)
        if self.harness_profile:
            payload["harness_profile"] = dict(self.harness_profile)
        return payload


def neutral_task_contract(task_intent: TaskIntent, *, current_message: str | None = None) -> TaskContract:
    """Return a no-tool fallback when a caller bypasses the planner path."""
    objective = str(getattr(task_intent, "objective", "") or current_message or "").strip()
    return TaskContract(
        objective=objective,
        task_type=PURE_ANSWER_TASK_TYPE,
        final_answer_required=True,
        allow_no_tool_final=True,
        contract_sources=MISSING_RUNTIME_CONTRACT_SOURCES,
        planner_metadata={
            PLANNER_METADATA_STATUS_FIELD: PLANNER_MISSING_STATUS,
            PLANNER_METADATA_REASON_FIELD: MISSING_RUNTIME_CONTRACT_REASON,
        },
    )


class TaskPlanner:
    """LLM-backed planner that produces the authoritative per-turn task contract."""

    def __init__(self, llm_config: DocumentLlmConfig):
        self.llm_config = llm_config

    async def plan(
        self,
        *,
        provider: Any,
        model: str | None,
        tool_registry: Any | None = None,
        task_intent: TaskIntent | None,
        current_message: str,
        history: list[dict[str, Any]] | None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
        task_context_decision: TaskContextDecision | None = None,
    ) -> TaskContract:
        if is_unconfigured_llm(provider, model):
            return _planner_blocked_contract(
                objective=_fallback_objective(task_intent, current_message),
                reason=PLANNER_UNAVAILABLE_REASON,
            )
        capability_catalog = build_planner_capability_catalog(tool_registry)
        planner_prompt = _build_task_planner_prompt(
            current_message=current_message,
            history=history or [],
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
            capability_catalog=capability_catalog,
        )
        try:
            response = await provider.chat(
                [
                    ChatMessage(role="system", content=_TASK_PLANNER_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=planner_prompt),
                ],
                model=model,
                **self.llm_config.decoding_kwargs(),
            )
        except Exception as exc:
            return _planner_blocked_contract(
                objective=_fallback_objective(task_intent, current_message),
                reason=_planner_exception_reason(exc),
            )
        response_text = str(getattr(response, "content", "") or "")
        payload = _parse_json_object(response_text)
        if not payload and response_text.strip():
            try:
                repair_response = await provider.chat(
                    [
                        ChatMessage(role="system", content=_PLANNER_REPAIR_SYSTEM_PROMPT),
                        ChatMessage(
                            role="user",
                            content=(
                                "Original planner prompt:\n"
                                f"{planner_prompt}\n\n"
                                "Invalid planner response:\n"
                                f"{response_text}\n\n"
                                "Return only the corrected JSON object."
                            ),
                        ),
                    ],
                    model=model,
                    **self.llm_config.decoding_kwargs(),
                )
            except Exception as exc:
                return _planner_blocked_contract(
                    objective=_fallback_objective(task_intent, current_message),
                    reason=_planner_exception_reason(exc),
                    raw_response_preview=_truncate(response_text, max_chars=400),
                )
            repair_text = str(getattr(repair_response, "content", "") or "")
            payload = _parse_json_object(repair_text)
            if not payload:
                response_text = repair_text or response_text
        if not payload:
            return _planner_blocked_contract(
                objective=_fallback_objective(task_intent, current_message),
                status=PLANNER_INVALID_STATUS,
                reason=PLANNER_INVALID_JSON_REASON,
                raw_response_preview=_truncate(response_text, max_chars=240),
            )
        return _contract_from_task_planner_payload(
            payload,
            task_intent=task_intent,
            current_message=current_message,
            history=history,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
            capability_catalog=capability_catalog,
        )





def _has_requirement(
    requirements: list[EvidenceRequirement],
    *,
    kind: str,
    tool_group: str = "",
) -> bool:
    return any(
        item.kind == kind and (not tool_group or item.tool_group == tool_group)
        for item in requirements
    )


def is_tool_group_requirement(requirement: Any) -> bool:
    return str(getattr(requirement, "kind", "") or "") == TOOL_GROUP_REQUIREMENT_KIND


def contract_has_acceptance_criterion(task_contract: Any, *kinds: str) -> bool:
    """Return whether a task contract carries any of the requested acceptance criteria."""
    expected = {str(kind or "").strip() for kind in kinds if str(kind or "").strip()}
    if not expected:
        return False
    return any(
        str(getattr(criterion, "kind", "") or "") in expected
        for criterion in getattr(task_contract, "acceptance_criteria", ()) or ()
    )


def contract_requests_itemized_output(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(task_contract, ITEMIZED_OUTPUT_CRITERION_KIND)


def contract_requests_source_reference(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(task_contract, SOURCE_REFERENCE_CRITERION_KIND)


def contract_requests_source_material(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(
        task_contract,
        SOURCE_ARTIFACT_CRITERION_KIND,
        SOURCE_DETAIL_CRITERION_KIND,
    )


def contract_requests_substantive_final_answer(task_contract: Any) -> bool:
    return contract_has_acceptance_criterion(task_contract, SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND)


def is_itemized_output_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == ITEMIZED_OUTPUT_CRITERION_KIND


def is_substantive_final_answer_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND


def is_source_artifact_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SOURCE_ARTIFACT_CRITERION_KIND


def is_source_detail_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SOURCE_DETAIL_CRITERION_KIND


def is_source_reference_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == SOURCE_REFERENCE_CRITERION_KIND


def is_workspace_location_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == WORKSPACE_LOCATION_CRITERION_KIND


def is_media_artifact_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == MEDIA_ARTIFACT_CRITERION_KIND


def is_verification_or_gap_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == VERIFICATION_OR_GAP_CRITERION_KIND


def is_operation_report_criterion(criterion: Any) -> bool:
    return _criterion_kind(criterion) == OPERATION_REPORT_CRITERION_KIND


def _criterion_kind(criterion: Any) -> str:
    return str(getattr(criterion, "kind", "") or "")


def _is_resource_coverage_requirement(requirement: EvidenceRequirement) -> bool:
    return requirement.kind == RESOURCE_COVERAGE_REQUIREMENT_KIND


def _is_all_resource_coverage(requirement: EvidenceRequirement) -> bool:
    return requirement.coverage == ALL_RESOURCE_COVERAGE


def _is_file_change_requirement(requirement: Any) -> bool:
    return str(getattr(requirement, "kind", "") or "") == FILE_CHANGE_REQUIREMENT_KIND


def _is_verification_requirement(requirement: EvidenceRequirement) -> bool:
    return requirement.kind == VERIFICATION_REQUIREMENT_KIND


def _is_workspace_write_requirement(requirement: Any) -> bool:
    return str(getattr(requirement, "tool_group", "") or "") == WORKSPACE_WRITE_TOOL_GROUP


def missing_evidence(contract: TaskContract | None, evidence: tuple[ToolEvidence, ...], *, file_change_count: int, verification_passed: bool) -> tuple[str, ...]:
    """Return human-readable missing evidence items for a contract."""
    if contract is None:
        return ()
    missing: list[str] = []
    ok_evidence = [item for item in evidence if item.ok]
    aliases = ResourceIndex.aliases_for(contract.selected_resources)
    for requirement in contract.requirements:
        if is_tool_group_requirement(requirement):
            tools = _contract_tool_group_tools(contract, requirement.tool_group)
            count = sum(1 for item in ok_evidence if item.name in tools)
            if count < max(1, requirement.min_count):
                missing.append(requirement.description or f"Use one of: {', '.join(sorted(tools))}")
        elif _is_resource_coverage_requirement(requirement):
            tools = _contract_tool_group_tools(contract, requirement.tool_group)
            covered = {
                alias
                for item in ok_evidence
                if item.name in tools
                for resource_id in item.resource_ids
                for alias in aliases.get(resource_id, {resource_id})
            }
            required = set(requirement.resource_ids)
            if _is_all_resource_coverage(requirement):
                uncovered = tuple(resource_id for resource_id in requirement.resource_ids if resource_id not in covered)
                if uncovered:
                    missing.append(
                        f"Missing {requirement.tool_group} coverage for: {', '.join(uncovered)}"
                    )
            elif len(covered & required) < max(1, requirement.min_count):
                missing.append(requirement.description or f"Missing {requirement.tool_group} coverage")
        elif _is_file_change_requirement(requirement) and file_change_count < max(1, requirement.min_count):
            missing.append(requirement.description or "Record a workspace file change.")
        elif _is_verification_requirement(requirement) and not verification_passed:
            missing.append(requirement.description or "Record passing verification evidence.")
    return tuple(missing)


def _contract_tool_group_tools(contract: TaskContract, tool_group: str) -> frozenset[str]:
    metadata = getattr(contract, "planner_metadata", None) or {}
    capability_tools = metadata.get("capability_tools") if isinstance(metadata, dict) else None
    if isinstance(capability_tools, dict):
        tools = capability_tools.get(tool_group)
        if isinstance(tools, (list, tuple, set, frozenset)):
            return frozenset(str(tool or "").strip() for tool in tools if str(tool or "").strip())
    return TOOL_GROUPS.get(tool_group, frozenset())


def contract_expects_file_change(task_contract: Any) -> bool:
    """Return whether a task contract requires workspace file changes."""
    task_type = str(getattr(task_contract, "task_type", "") or "")
    if task_type in FILE_CHANGE_TASK_TYPES:
        return True
    for requirement in getattr(task_contract, "requirements", ()) or ():
        if _is_file_change_requirement(requirement):
            return True
        if _is_workspace_write_requirement(requirement):
            return True
    return False


def _tool_group_requirement(tool_group: str) -> EvidenceRequirement:
    if is_web_research_tool_group(tool_group):
        return EvidenceRequirement(
            kind="tool_group",
            tool_group=WEB_RESEARCH_TOOL_GROUP,
            coverage="any",
            min_count=1,
            description="Use web research tools before answering this external information request.",
        )
    return EvidenceRequirement(
        kind="tool_group",
        tool_group=tool_group,
        coverage="any",
        min_count=1,
        description=f"Use {tool_group} tools before finalizing the answer.",
    )


def _append_acceptance_criteria(
    existing: list[AcceptanceCriterion],
    additions: tuple[AcceptanceCriterion, ...],
) -> list[AcceptanceCriterion]:
    seen = {_criterion_kind(item) for item in existing}
    for criterion in additions:
        criterion_kind = _criterion_kind(criterion)
        if criterion_kind not in seen:
            existing.append(criterion)
            seen.add(criterion_kind)
    return existing


def _build_task_planner_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
    capability_catalog: PlannerCapabilityCatalog | None = None,
) -> str:
    catalog = capability_catalog or build_planner_capability_catalog()
    context = {
        "current_message": _truncate_middle(current_message, max_chars=1200),
        "recent_history": _recent_history(history),
        "attachments": {
            "image_files": list(current_image_files or []),
            "audio_files": list(current_audio_files or []),
            "video_files": list(current_video_files or []),
        },
        "task_context": task_context_decision.to_metadata() if task_context_decision is not None else None,
        "capability_catalog": catalog.to_prompt_metadata(),
        "quality_checks": _quality_check_catalog(),
    }
    return (
        "Create the task contract for the latest user turn. The contract controls which tools the main assistant can see.\n"
        "Use semantic judgment from the message and recent history, not string matching. Select the smallest set of "
        "required_tool_groups from capability_catalog.available_capabilities that is necessary to finish the task with "
        "evidence. Do not invent unavailable tool groups. If no tool-backed evidence is needed, choose pure_answer with "
        "an empty required_tool_groups array. Use quality_checks only when the final answer needs extra verification "
        "beyond the selected capabilities.\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "objective": "short task objective in the user language",\n'
        f'  "task_type": "{_schema_union(catalog.task_types)}",\n'
        f'  "required_tool_groups": ["{_schema_union(catalog.tool_group_ids)}"],\n'
        f'  "quality_checks": ["{_schema_union(_ALLOWED_PLANNER_QUALITY_CHECKS)}"],\n'
        '  "final_answer_required": true,\n'
        '  "allow_no_tool_final": true,\n'
        '  "reason": "short explanation for trace only"\n'
        "}\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _quality_check_catalog() -> list[dict[str, str]]:
    return [
        {
            "id": COMMAND_VERSION_QUALITY_CHECK,
            "description": "Use when the final answer must report an installed command version.",
        },
        {
            "id": REPOSITORY_STATUS_QUALITY_CHECK,
            "description": "Use when the final answer must report repository or worktree status.",
        },
        {
            "id": WORKSPACE_LOCATION_QUALITY_CHECK,
            "description": "Use when the final answer must identify a workspace file path, symbol, or config location.",
        },
    ]


def _schema_union(values: tuple[str, ...] | frozenset[str]) -> str:
    ordered = list(values) if isinstance(values, tuple) else sorted(values)
    return " | ".join(ordered) if ordered else "<none>"


def _planner_blocked_contract(
    *,
    objective: str,
    reason: str,
    status: str = PLANNER_BLOCKED_STATUS,
    raw_response_preview: str = "",
) -> TaskContract:
    metadata: dict[str, Any] = {
        PLANNER_METADATA_STATUS_FIELD: status,
        PLANNER_METADATA_REASON_FIELD: reason,
    }
    if raw_response_preview:
        metadata[PLANNER_METADATA_RAW_RESPONSE_PREVIEW_FIELD] = raw_response_preview
    return TaskContract(
        objective=objective,
        task_type=PLANNING_ERROR_TASK_TYPE,
        final_answer_required=True,
        allow_no_tool_final=False,
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        acceptance_criteria=(
            AcceptanceCriterion(
                kind="planner_error_report",
                description="Explain that task contract planning failed and a reliable tool profile could not be selected.",
            ),
        ),
        planner_metadata=metadata,
    )


def _planner_exception_reason(exc: Exception) -> str:
    error_type = exc.__class__.__name__
    message = str(exc).strip()
    if message:
        return f"task planner LLM call failed: {error_type}: {message}"
    return f"task planner LLM call failed: {error_type}"


def _fallback_objective(task_intent: TaskIntent | None, current_message: str | None) -> str:
    return str(getattr(task_intent, "objective", "") or current_message or "").strip()


def _planner_objective(
    payload: dict[str, Any],
    task_intent: TaskIntent | None,
    current_message: str | None,
) -> str:
    objective = _compact(payload.get("objective"))
    return objective or _fallback_objective(task_intent, current_message)


def _contract_from_task_planner_payload(
    payload: dict[str, Any],
    *,
    task_intent: TaskIntent | None,
    current_message: str,
    history: list[dict[str, Any]] | None,
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
    capability_catalog: PlannerCapabilityCatalog | None = None,
) -> TaskContract:
    catalog = capability_catalog or build_planner_capability_catalog()
    objective = _planner_objective(payload, task_intent, current_message)
    resource_index = ResourceIndex.from_turn_and_history(
        current_message=current_message,
        history=history,
        current_image_files=current_image_files,
        current_audio_files=current_audio_files,
        current_video_files=current_video_files,
    )
    raw_task_type = _allowed_string(payload.get("task_type"), _ALLOWED_PLANNER_TASK_TYPES)
    if not raw_task_type:
        return _planner_blocked_contract(
            objective=objective,
            status=PLANNER_INVALID_STATUS,
            reason=PLANNER_UNSUPPORTED_TASK_TYPE_REASON,
            raw_response_preview=_truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), max_chars=240),
        )
    raw_tool_groups = _normalize_planner_tool_groups(
        payload.get("required_tool_groups"),
        allowed_tool_groups=catalog.tool_group_ids,
    )
    quality_checks = _normalize_planner_quality_checks(payload.get("quality_checks"))
    task_type = _PLANNER_TASK_TYPE_ALIASES.get(raw_task_type, raw_task_type)
    tool_groups = raw_tool_groups
    if task_type == HISTORY_RETRIEVAL_TASK_TYPE:
        tool_groups = [tool_group for tool_group in tool_groups if tool_group == HISTORY_RETRIEVAL_TOOL_GROUP]
    inherited_tool_group = getattr(task_context_decision, "inherited_tool_group", "") or ""
    if (
        inherited_tool_group in catalog.tool_group_ids
        and inherited_tool_group not in tool_groups
    ):
        tool_groups.append(inherited_tool_group)
    _ensure_task_type_tool_groups(task_type, tool_groups)

    requirements: list[EvidenceRequirement] = []
    acceptance_criteria: list[AcceptanceCriterion] = []
    selected: list[ResourceRef] = []

    for tool_group in tool_groups:
        acceptance_criteria = _append_tool_group_contract(
            tool_group,
            requirements=requirements,
            acceptance_criteria=acceptance_criteria,
            resource_index=resource_index,
            selected=selected,
        )

    if WORKSPACE_LOCATION_QUALITY_CHECK in quality_checks:
        acceptance_criteria = _append_acceptance_criteria(acceptance_criteria, (_workspace_location_criterion(),))

    planner_reason = _truncate(str(payload.get("reason") or PLANNER_VALIDATED_REASON), max_chars=240)
    metadata = {
        PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS,
        "raw_task_type": raw_task_type,
        "required_tool_groups": list(tool_groups),
        "quality_checks": list(quality_checks),
        "capability_tools": {key: list(value) for key, value in catalog.capability_tools.items()},
        PLANNER_METADATA_REASON_FIELD: planner_reason,
    }
    return TaskContract(
        objective=objective,
        task_type=task_type,
        requirements=tuple(requirements),
        acceptance_criteria=tuple(acceptance_criteria),
        selected_resources=tuple(dict.fromkeys(selected)),
        final_answer_required=_coerce_bool(payload.get("final_answer_required", True)),
        allow_no_tool_final=_coerce_bool(payload.get("allow_no_tool_final", not requirements)) and not requirements,
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata=metadata,
    )


def _normalize_planner_tool_groups(
    value: Any,
    *,
    allowed_tool_groups: tuple[str, ...] | frozenset[str] | None = None,
) -> list[str]:
    allowed = set(allowed_tool_groups or _ALLOWED_PLANNER_TOOL_GROUPS)
    raw_values = value if isinstance(value, list) else []
    groups: list[str] = []
    for item in raw_values:
        text = str(item or "").strip()
        text = _PLANNER_TOOL_GROUP_ALIASES.get(text, text)
        if text in allowed and text not in groups:
            groups.append(text)
    return groups


def _normalize_planner_quality_checks(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else []
    checks: list[str] = []
    for item in raw_values:
        text = str(item or "").strip().lower()
        if text in _ALLOWED_PLANNER_QUALITY_CHECKS and text not in checks:
            checks.append(text)
    return checks


def _ensure_task_type_tool_groups(task_type: str, tool_groups: list[str]) -> None:
    for tool_group in _TASK_TYPE_REQUIRED_TOOL_GROUPS.get(task_type, ()):
        if tool_group not in tool_groups:
            tool_groups.append(tool_group)


def _append_tool_group_contract(
    tool_group: str,
    *,
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    resource_index: ResourceIndex,
    selected: list[ResourceRef],
) -> list[AcceptanceCriterion]:
    if is_web_research_tool_group(tool_group):
        _append_web_contract(requirements, acceptance_criteria, min_source_count=2)
        return acceptance_criteria
    if tool_group == WORKSPACE_READ_TOOL_GROUP:
        _append_workspace_contract(requirements, acceptance_criteria)
        return acceptance_criteria
    if tool_group == WORKSPACE_WRITE_TOOL_GROUP:
        _append_workspace_contract(requirements, acceptance_criteria)
        if not _has_requirement(requirements, kind=FILE_CHANGE_REQUIREMENT_KIND):
            requirements.append(
                EvidenceRequirement(
                    kind=FILE_CHANGE_REQUIREMENT_KIND,
                    min_count=1,
                    description="Record at least one workspace file change.",
                )
            )
        return _append_acceptance_criteria(acceptance_criteria, (_verification_or_gap_criterion(),))
    if tool_group == MEDIA_TOOL_GROUP:
        return _append_media_contract(
            requirements,
            acceptance_criteria,
            resource_index=resource_index,
            selected=selected,
        )
    if tool_group == HISTORY_RETRIEVAL_TOOL_GROUP:
        requirements.append(_tool_group_requirement(HISTORY_RETRIEVAL_TOOL_GROUP))
        return _append_acceptance_criteria(acceptance_criteria, (_history_final_answer_criterion(),))
    if tool_group in OPERATION_TOOL_GROUPS:
        requirements.append(_tool_group_requirement(tool_group))
        return _append_acceptance_criteria(acceptance_criteria, (_operation_report_criterion(),))
    if tool_group == VERIFICATION_TOOL_GROUP:
        requirements.append(
            EvidenceRequirement(
                kind=VERIFICATION_REQUIREMENT_KIND,
                tool_group=VERIFICATION_TOOL_GROUP,
                min_count=1,
                description="Record verification evidence before finalizing.",
            )
        )
        return acceptance_criteria
    requirements.append(_tool_group_requirement(tool_group))
    return _append_acceptance_criteria(acceptance_criteria, (_tool_backed_final_answer_criterion(),))


def _append_media_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    resource_index: ResourceIndex,
    selected: list[ResourceRef],
) -> list[AcceptanceCriterion]:
    image_resources = resource_index.by_kind("image")
    audio_resources = resource_index.by_kind("audio")
    video_resources = resource_index.by_kind("video")
    selected.extend(image_resources + audio_resources + video_resources)
    if image_resources:
        requirements.append(
            EvidenceRequirement(
                kind="resource_coverage",
                tool_group="image_text",
                resource_ids=tuple(item.id for item in image_resources),
                coverage="all",
                min_count=len(image_resources),
                description="Inspect each referenced image before finalizing the answer.",
            )
        )
    if audio_resources:
        requirements.append(
            EvidenceRequirement(
                kind="resource_coverage",
                tool_group="audio_text",
                resource_ids=tuple(item.id for item in audio_resources),
                coverage="all",
                min_count=len(audio_resources),
                description="Transcribe each referenced audio clip before finalizing the answer.",
            )
        )
    if video_resources:
        requirements.append(
            EvidenceRequirement(
                kind="resource_coverage",
                tool_group="video_understanding",
                resource_ids=tuple(item.id for item in video_resources),
                coverage="all",
                min_count=len(video_resources),
                description="Analyze each referenced video before finalizing the answer.",
            )
        )
    if not (image_resources or audio_resources or video_resources):
        requirements.append(_tool_group_requirement(MEDIA_TOOL_GROUP))
    return _append_acceptance_criteria(
        acceptance_criteria,
        (_media_artifact_criterion(), _media_final_answer_criterion()),
    )


def _append_web_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    min_source_count: int,
) -> None:
    if not _has_requirement(requirements, kind="tool_group", tool_group=WEB_RESEARCH_TOOL_GROUP):
        requirements.append(
            EvidenceRequirement(
                kind="tool_group",
                tool_group=WEB_RESEARCH_TOOL_GROUP,
                coverage="any",
                min_count=1,
                description="Use web research tools before answering this external information request.",
            )
        )
    acceptance_criteria[:] = _append_acceptance_criteria(
        acceptance_criteria,
        (
            AcceptanceCriterion(
                kind=SOURCE_ARTIFACT_CRITERION_KIND,
                min_count=min_source_count,
                description="Produce enough traceable web sources before finalizing the answer.",
            ),
            AcceptanceCriterion(
                kind=SOURCE_DETAIL_CRITERION_KIND,
                min_count=1,
                description="Fetch or inspect at least one source page before finalizing; search snippets alone are not enough.",
            ),
            _web_final_answer_criterion(),
            _web_source_reference_criterion(),
        ),
    )


def _append_workspace_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
) -> None:
    if not _has_requirement(requirements, kind="tool_group", tool_group=WORKSPACE_READ_TOOL_GROUP):
        requirements.append(
            EvidenceRequirement(
                kind="tool_group",
                tool_group=WORKSPACE_READ_TOOL_GROUP,
                coverage="any",
                min_count=1,
                description="Inspect the relevant workspace files or code context before answering.",
            )
        )
    acceptance_criteria[:] = _append_acceptance_criteria(acceptance_criteria, (_workspace_final_answer_criterion(),))


def _parse_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL)
    raw = fenced.group(1) if fenced else text
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end >= start:
        raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _recent_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in (history or [])[-6:]:
        role = str(item.get("role") or "").strip()
        content = _truncate(str(item.get("content") or ""), max_chars=500)
        if role and content:
            entries.append({"role": role, "content": content})
    return entries


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate(text: str, *, max_chars: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _truncate_middle(text: str, *, max_chars: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    if max_chars <= 20:
        return _truncate(compact, max_chars=max_chars)
    marker = "\n... [middle omitted] ...\n"
    remaining = max_chars - len(marker)
    head_chars = max(1, remaining // 2)
    tail_chars = max(1, remaining - head_chars)
    return f"{compact[:head_chars].rstrip()}{marker}{compact[-tail_chars:].lstrip()}"


def _allowed_string(value: Any, allowed: frozenset[str]) -> str | None:
    text = str(value or "").strip()
    return text if text in allowed else None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _media_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the inspected media results.",
    )


def _media_artifact_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="media_artifact",
        min_count=1,
        description="Produce a media artifact for the selected image, audio, or video before finalizing.",
    )


def _web_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=100,
        description="Provide a substantive final answer that uses the gathered web source results.",
    )


def _web_source_reference_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind=SOURCE_REFERENCE_CRITERION_KIND,
        min_count=1,
        description="Reference at least one gathered web source by URL, domain, or title.",
    )


def _workspace_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the inspected workspace context.",
    )


def _tool_backed_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind=SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
        min_response_chars=80,
        description="Provide a substantive final answer that uses the gathered tool evidence.",
    )


def _workspace_location_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="workspace_location",
        min_count=1,
        description="Identify the relevant workspace file path, symbol, or configuration location in the final answer.",
    )


def _verification_or_gap_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="verification_or_gap",
        description="After code changes, either record a focused verification attempt or state the verification gap clearly.",
    )


def _operation_report_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="operation_report",
        description="Report approval, validation, rollback, blocker, or residual risk for the operation.",
    )


def _history_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the retrieved prior context.",
    )
