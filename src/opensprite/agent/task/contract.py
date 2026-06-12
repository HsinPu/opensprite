"""Task contracts and evidence requirements for completion checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ...config.schema import DocumentLlmConfig
from ...llms import ChatMessage, is_unconfigured_llm
from ...tool_names import (
    ANALYZE_IMAGE_TOOL_NAME,
    ANALYZE_VIDEO_TOOL_NAME,
    CONFIGURE_MCP_TOOL_NAME,
    CRON_TOOL_NAME,
    DELEGATE_MANY_TOOL_NAME,
    DELEGATE_TOOL_NAME,
    EXEC_TOOL_NAME,
    EXECUTION_TOOL_NAMES,
    LIST_RUN_FILE_CHANGES_TOOL_NAME,
    OCR_IMAGE_TOOL_NAME,
    PROCESS_TOOL_NAME,
    PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
    RUN_WORKFLOW_TOOL_NAME,
    TRANSCRIBE_AUDIO_TOOL_NAME,
    WORKSPACE_DISCOVERY_TOOL_NAMES,
    WORKSPACE_WRITE_TOOL_NAMES,
)
from .capabilities import (
    ANALYSIS_TASK_TYPE,
    CODE_CHANGE_TASK_TYPE,
    FILE_CHANGE_REQUIREMENT_KIND,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    MEDIA_EXTRACTION_TASK_TYPE,
    OPERATIONS_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
    VERIFICATION_REQUIREMENT_KIND,
    WORKSPACE_READ_TASK_TYPE,
)
from .planning_mode import PlannerCapabilityCatalog, build_planner_capability_catalog
from .resources import ResourceIndex, ResourceRef
from .intent import TaskIntent as _TaskIntent
from .resolution import (
    TaskContextDecision,
    _chat_json_planning_llm,
    _json_object_text,
    _llm_response_preview,
    _llm_response_text,
)
from .value_utils import (
    _DEFAULT_TRUE_VALUES,
    _allowed_policy_value,
    _coerce_policy_bool,
    _coerce_policy_confidence,
    _compact_text,
    _policy_value,
    _truncate_middle_text,
    _truncate_text,
)
from ...context.message_history import HISTORY_SEARCH_TOOL_NAME
from ...tools.evidence import (
    SOURCE_ARTIFACT_CRITERION_KIND,
    SOURCE_DETAIL_CRITERION_KIND,
    SOURCE_REFERENCE_CRITERION_KIND,
    WEB_RESEARCH_TASK_TYPE,
    WEB_SOURCE_ARTIFACT_TOOLS,
    VERIFICATION_TOOL_NAME,
    is_web_research_task_type,
)
from ...tools.registry import ToolRegistry

_PLANNER_TRUE_VALUES = _DEFAULT_TRUE_VALUES | frozenset({"是"})


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
REQUIRED_TOOL_EVIDENCE_KIND = "required_tool"
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
        CODE_CHANGE_TASK_TYPE,
        MEDIA_EXTRACTION_TASK_TYPE,
        PLANNING_TASK_TYPE,
        HISTORY_RETRIEVAL_TASK_TYPE,
        OPERATIONS_TASK_TYPE,
        GENERIC_TASK_TYPE,
        ANALYSIS_TASK_TYPE,
    }
)
FILE_CHANGE_TASK_TYPES = frozenset({CODE_CHANGE_TASK_TYPE})
TOOL_BACKED_TASK_TYPES = frozenset(
    {
        WEB_RESEARCH_TASK_TYPE,
        WORKSPACE_READ_TASK_TYPE,
        CODE_CHANGE_TASK_TYPE,
        MEDIA_EXTRACTION_TASK_TYPE,
        HISTORY_RETRIEVAL_TASK_TYPE,
        OPERATIONS_TASK_TYPE,
    }
)
PLANNER_MISSING_REQUIRED_TOOLS_REASON = "task planner returned a tool-backed task without required_tools"
_TASK_PLANNER_SYSTEM_PROMPT = (
    "You are the OpenSprite task planner. Decide what tool evidence the latest user turn needs "
    "before the main assistant sees tools. Return only one JSON object. Do not include markdown. "
    "Choose the smallest necessary set of concrete tool names from the available runtime tools supplied in the user prompt. "
    "If no tool-backed evidence is needed, use pure_answer and an empty required_tools array. "
    "The JSON keys are: objective, task_type, required_tools, quality_checks, final_answer_required, allow_no_tool_final, reason."
)
_PLANNER_REPAIR_SYSTEM_PROMPT = (
    "You repair OpenSprite task planner output. Convert the invalid planner response into exactly one "
    "valid JSON object for the same schema. Return JSON only, no markdown, no explanation."
)


@dataclass(frozen=True)
class EvidenceRequirement:
    """Evidence needed before the task can be treated as complete."""

    kind: str
    tools: tuple[str, ...] = ()
    resource_ids: tuple[str, ...] = ()
    coverage: str = "any"
    min_count: int = 1
    description: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tools": list(self.tools),
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
    required_tools: tuple[str, ...] = ()
    blocked_tools: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    quality_checks: tuple[str, ...] = ()
    final_answer_required: bool = True
    allow_no_tool_final: bool = True
    contract_sources: tuple[str, ...] = DETERMINISTIC_CONTRACT_SOURCES
    planner_metadata: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "objective": self.objective,
            "task_type": self.task_type,
            "requirements": [item.to_metadata() for item in self.requirements],
            "acceptance_criteria": [item.to_metadata() for item in self.acceptance_criteria],
            "selected_resources": [item.to_metadata() for item in self.selected_resources],
            "required_tools": list(self.required_tools),
            "blocked_tools": list(self.blocked_tools),
            "required_evidence": list(self.required_evidence),
            "quality_checks": list(self.quality_checks),
            "final_answer_required": self.final_answer_required,
            "allow_no_tool_final": self.allow_no_tool_final,
            "contract_sources": list(self.contract_sources),
        }
        if self.planner_metadata:
            payload["planner_metadata"] = dict(self.planner_metadata)
        return payload


def task_planner_status(task_contract: Any) -> str:
    """Return the normalized planner status from a task contract."""
    return _planner_metadata_value(task_contract, PLANNER_METADATA_STATUS_FIELD)


def task_planner_reason(task_contract: Any) -> str:
    """Return the normalized planner reason from a task contract."""
    return _planner_metadata_value(task_contract, PLANNER_METADATA_REASON_FIELD)


def _planner_metadata_value(task_contract: Any, field: str) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return _policy_value(metadata.get(field))
    return ""


def neutral_task_contract(task_intent: _TaskIntent, *, current_message: str | None = None) -> TaskContract:
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


class TaskPlannerError(RuntimeError):
    """Raised when the LLM planner cannot produce a valid task contract."""

    def __init__(self, reason: str, *, raw_response_preview: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.raw_response_preview = raw_response_preview


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
        fallback_objective: str = "",
        current_message: str,
        history: list[dict[str, Any]] | None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
        task_context_decision: TaskContextDecision | None = None,
    ) -> TaskContract:
        if is_unconfigured_llm(provider, model):
            raise TaskPlannerError(PLANNER_UNAVAILABLE_REASON)
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
            response = await _chat_json_planning_llm(
                provider=provider,
                messages=[
                    ChatMessage(role="system", content=_TASK_PLANNER_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=planner_prompt),
                ],
                model=model,
                llm_config=self.llm_config,
            )
        except Exception as exc:
            raise TaskPlannerError(_planner_exception_reason(exc)) from exc
        response_text = _llm_response_text(response)
        payload = _parse_json_object(response_text)
        raw_response_preview = _llm_response_preview(response, text=response_text, max_chars=400)
        if not payload:
            try:
                repair_response = await _chat_json_planning_llm(
                    provider=provider,
                    messages=[
                        ChatMessage(role="system", content=_PLANNER_REPAIR_SYSTEM_PROMPT),
                        ChatMessage(
                            role="user",
                            content=(
                                "Original planner prompt:\n"
                                f"{planner_prompt}\n\n"
                                "Invalid planner response:\n"
                                f"{raw_response_preview}\n\n"
                                "Return only the corrected JSON object."
                            ),
                        ),
                    ],
                    model=model,
                    llm_config=self.llm_config,
                )
            except Exception as exc:
                raise TaskPlannerError(
                    _planner_exception_reason(exc),
                    raw_response_preview=raw_response_preview,
                ) from exc
            repair_text = _llm_response_text(repair_response)
            payload = _parse_json_object(repair_text)
            if not payload:
                raw_response_preview = _llm_response_preview(repair_response, text=repair_text, max_chars=400)
        if not payload:
            raise TaskPlannerError(
                PLANNER_INVALID_JSON_REASON,
                raw_response_preview=_truncate_text(raw_response_preview, max_chars=240),
            )
        return _contract_from_task_planner_payload(
            payload,
            fallback_objective=fallback_objective,
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
    tools: tuple[str, ...] | frozenset[str] = (),
) -> bool:
    expected_tools = frozenset(_policy_value(tool) for tool in tools if _policy_value(tool))
    for item in requirements:
        if item.kind != kind:
            continue
        if not expected_tools or frozenset(_requirement_tools(item)) == expected_tools:
            return True
    return False


def _criterion_kind(criterion: Any) -> str:
    return str(getattr(criterion, "kind", "") or "")


def _requirement_attr(requirement: Any, attr: str) -> str:
    return str(getattr(requirement, attr, "") or "")


def _requirement_tools(requirement: Any) -> tuple[str, ...]:
    raw_tools = getattr(requirement, "tools", ()) or ()
    if isinstance(raw_tools, str):
        raw_tools = (raw_tools,)
    tools: list[str] = []
    for value in raw_tools:
        tool_name = _policy_value(value)
        if tool_name and tool_name not in tools:
            tools.append(tool_name)
    return tuple(tools)


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
        "required_tools from capability_catalog.available_tools that is necessary to finish the task with evidence. "
        "Do not invent unavailable tool names. If no tool-backed evidence is needed, choose pure_answer with an empty "
        "required_tools array. Use quality_checks only when the final answer needs extra verification beyond the selected tools.\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "objective": "short task objective in the user language",\n'
        f'  "task_type": "{_schema_union(catalog.task_types)}",\n'
        f'  "required_tools": ["{_schema_union(catalog.available_tool_names)}"],\n'
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


def _planner_exception_reason(exc: Exception) -> str:
    error_type = exc.__class__.__name__
    message = str(exc).strip()
    if message:
        return f"task planner LLM call failed: {error_type}: {message}"
    return f"task planner LLM call failed: {error_type}"


def _fallback_objective(fallback_objective: str | None, current_message: str | None) -> str:
    return str(fallback_objective or current_message or "").strip()


def _planner_objective(
    payload: dict[str, Any],
    fallback_objective: str | None,
    current_message: str | None,
) -> str:
    objective = _compact(payload.get("objective"))
    return objective or _fallback_objective(fallback_objective, current_message)


def _contract_from_task_planner_payload(
    payload: dict[str, Any],
    *,
    fallback_objective: str = "",
    current_message: str,
    history: list[dict[str, Any]] | None,
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
    capability_catalog: PlannerCapabilityCatalog | None = None,
) -> TaskContract:
    catalog = capability_catalog or build_planner_capability_catalog()
    objective = _planner_objective(payload, fallback_objective, current_message)
    resource_index = ResourceIndex.from_turn_and_history(
        current_message=current_message,
        history=history,
        current_image_files=current_image_files,
        current_audio_files=current_audio_files,
        current_video_files=current_video_files,
    )
    raw_task_type = _allowed_string(payload.get("task_type"), _ALLOWED_PLANNER_TASK_TYPES)
    if not raw_task_type:
        raise TaskPlannerError(
            PLANNER_UNSUPPORTED_TASK_TYPE_REASON,
            raw_response_preview=_truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), max_chars=240),
        )
    required_tools = _normalize_planner_required_tools(
        payload.get("required_tools"),
        allowed_tools=catalog.available_tool_names,
    )
    quality_checks = _normalize_planner_quality_checks(payload.get("quality_checks"))
    task_type = raw_task_type
    if task_type in TOOL_BACKED_TASK_TYPES and not required_tools:
        raise TaskPlannerError(
            PLANNER_MISSING_REQUIRED_TOOLS_REASON,
            raw_response_preview=_truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), max_chars=240),
        )

    requirements: list[EvidenceRequirement] = []
    acceptance_criteria: list[AcceptanceCriterion] = []
    selected: list[ResourceRef] = []

    acceptance_criteria = _append_required_tool_contract(
        task_type,
        required_tools,
        requirements=requirements,
        acceptance_criteria=acceptance_criteria,
        resource_index=resource_index,
        selected=selected,
    )

    if WORKSPACE_LOCATION_QUALITY_CHECK in quality_checks:
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (_acceptance_criterion(_CRITERION_WORKSPACE_LOCATION),),
        )

    planner_reason = _truncate(str(payload.get("reason") or PLANNER_VALIDATED_REASON), max_chars=240)
    metadata = {
        PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS,
        "raw_task_type": raw_task_type,
        "required_tools": list(required_tools),
        "quality_checks": list(quality_checks),
        PLANNER_METADATA_REASON_FIELD: planner_reason,
    }
    return TaskContract(
        objective=objective,
        task_type=task_type,
        requirements=tuple(requirements),
        acceptance_criteria=tuple(acceptance_criteria),
        selected_resources=tuple(dict.fromkeys(selected)),
        required_tools=tuple(required_tools),
        required_evidence=_required_evidence_from_requirements(requirements, required_tools=required_tools),
        quality_checks=tuple(quality_checks),
        final_answer_required=_coerce_bool(payload.get("final_answer_required", True)),
        allow_no_tool_final=(
            _coerce_bool(payload.get("allow_no_tool_final", not requirements and not required_tools))
            and not requirements
            and not required_tools
        ),
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata=metadata,
    )


def _normalize_planner_required_tools(
    value: Any,
    *,
    allowed_tools: tuple[str, ...] | frozenset[str] | None = None,
) -> list[str]:
    allowed = set(allowed_tools or ())
    raw_values = value if isinstance(value, list) else []
    tools: list[str] = []
    for item in raw_values:
        text = _policy_value(item)
        if text in allowed and text not in tools:
            tools.append(text)
    return tools


def _required_evidence_from_requirements(
    requirements: list[EvidenceRequirement],
    *,
    required_tools: list[str] | tuple[str, ...] = (),
) -> tuple[str, ...]:
    evidence: list[str] = []
    if required_tools:
        evidence.append(REQUIRED_TOOL_EVIDENCE_KIND)
    for requirement in requirements:
        kind = _requirement_attr(requirement, "kind")
        if kind and kind not in evidence:
            evidence.append(kind)
    return tuple(evidence)


def _normalize_planner_quality_checks(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else []
    checks: list[str] = []
    for item in raw_values:
        text = _policy_value(item).lower()
        if text in _ALLOWED_PLANNER_QUALITY_CHECKS and text not in checks:
            checks.append(text)
    return checks


_CRITERION_MEDIA_FINAL_ANSWER = "media_final_answer"
_CRITERION_MEDIA_ARTIFACT = "media_artifact"
_CRITERION_WEB_FINAL_ANSWER = "web_final_answer"
_CRITERION_WEB_SOURCE_REFERENCE = "web_source_reference"
_CRITERION_WORKSPACE_FINAL_ANSWER = "workspace_final_answer"
_CRITERION_TOOL_BACKED_FINAL_ANSWER = "tool_backed_final_answer"
_CRITERION_WORKSPACE_LOCATION = "workspace_location"
_CRITERION_VERIFICATION_OR_GAP = "verification_or_gap"
_CRITERION_OPERATION_REPORT = "operation_report"
_CRITERION_HISTORY_FINAL_ANSWER = "history_final_answer"
_ACCEPTANCE_CRITERION_SPECS: dict[str, dict[str, Any]] = {
    _CRITERION_MEDIA_FINAL_ANSWER: {
        "kind": SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
        "min_response_chars": 80,
        "description": "Provide a substantive final answer that uses the inspected media results.",
    },
    _CRITERION_MEDIA_ARTIFACT: {
        "kind": MEDIA_ARTIFACT_CRITERION_KIND,
        "min_count": 1,
        "description": "Produce a media artifact for the selected image, audio, or video before finalizing.",
    },
    _CRITERION_WEB_FINAL_ANSWER: {
        "kind": SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
        "min_response_chars": 100,
        "description": "Provide a substantive final answer that uses the gathered web source results.",
    },
    _CRITERION_WEB_SOURCE_REFERENCE: {
        "kind": SOURCE_REFERENCE_CRITERION_KIND,
        "min_count": 1,
        "description": "Reference at least one gathered web source by URL, domain, or title.",
    },
    _CRITERION_WORKSPACE_FINAL_ANSWER: {
        "kind": SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
        "min_response_chars": 80,
        "description": "Provide a substantive final answer that uses the inspected workspace context.",
    },
    _CRITERION_TOOL_BACKED_FINAL_ANSWER: {
        "kind": SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
        "min_response_chars": 80,
        "description": "Provide a substantive final answer that uses the gathered tool evidence.",
    },
    _CRITERION_WORKSPACE_LOCATION: {
        "kind": WORKSPACE_LOCATION_CRITERION_KIND,
        "min_count": 1,
        "description": "Identify the relevant workspace file path, symbol, or configuration location in the final answer.",
    },
    _CRITERION_VERIFICATION_OR_GAP: {
        "kind": VERIFICATION_OR_GAP_CRITERION_KIND,
        "description": "After code changes, either record a focused verification attempt or state the verification gap clearly.",
    },
    _CRITERION_OPERATION_REPORT: {
        "kind": OPERATION_REPORT_CRITERION_KIND,
        "description": "Report validation, rollback, blocker, or residual risk for the operation.",
    },
    _CRITERION_HISTORY_FINAL_ANSWER: {
        "kind": SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
        "min_response_chars": 80,
        "description": "Provide a substantive final answer that uses the retrieved prior context.",
    },
}


def _acceptance_criterion(name: str) -> AcceptanceCriterion:
    return AcceptanceCriterion(**_ACCEPTANCE_CRITERION_SPECS[name])


def _append_required_tool_contract(
    task_type: str,
    required_tools: list[str],
    *,
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    resource_index: ResourceIndex,
    selected: list[ResourceRef],
) -> list[AcceptanceCriterion]:
    tool_names = frozenset(required_tools)
    if is_web_research_task_type(task_type) or tool_names & WEB_SOURCE_ARTIFACT_TOOLS:
        _append_web_contract(requirements, acceptance_criteria, min_source_count=2)

    if task_type == WORKSPACE_READ_TASK_TYPE or tool_names & WORKSPACE_DISCOVERY_TOOL_NAMES:
        _append_workspace_contract(requirements, acceptance_criteria)

    if task_type in FILE_CHANGE_TASK_TYPES or tool_names & WORKSPACE_WRITE_TOOL_NAMES:
        _append_workspace_contract(requirements, acceptance_criteria)
        if not _has_requirement(requirements, kind=FILE_CHANGE_REQUIREMENT_KIND):
            requirements.append(
                EvidenceRequirement(
                    kind=FILE_CHANGE_REQUIREMENT_KIND,
                    min_count=1,
                    description="Record at least one workspace file change.",
                )
            )
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (_acceptance_criterion(_CRITERION_VERIFICATION_OR_GAP),),
        )

    if task_type == MEDIA_EXTRACTION_TASK_TYPE or _is_media_tool_selected(tool_names):
        acceptance_criteria = _append_media_contract(
            requirements,
            acceptance_criteria,
            resource_index=resource_index,
            selected=selected,
            required_tools=tool_names,
        )

    if task_type == HISTORY_RETRIEVAL_TASK_TYPE or _is_history_tool_selected(tool_names):
        _append_tool_evidence_requirement(
            requirements,
            tools=_history_evidence_tools(tool_names),
            description="Search prior chat history before answering this recall request.",
        )
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (_acceptance_criterion(_CRITERION_HISTORY_FINAL_ANSWER),),
        )

    if task_type == OPERATIONS_TASK_TYPE or _is_operation_tool_selected(tool_names):
        _append_tool_evidence_requirement(
            requirements,
            tools=_operation_evidence_tools(tool_names),
            description="Use the selected operation tool before finalizing the answer.",
        )
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (_acceptance_criterion(_CRITERION_OPERATION_REPORT),),
        )

    if VERIFICATION_TOOL_NAME in tool_names:
        requirements.append(
            EvidenceRequirement(
                kind=VERIFICATION_REQUIREMENT_KIND,
                tools=(VERIFICATION_TOOL_NAME,),
                min_count=1,
                description="Record verification evidence before finalizing.",
            )
        )

    if required_tools and not acceptance_criteria:
        _append_tool_evidence_requirement(
            requirements,
            tools=tuple(required_tools),
            description="Use at least one selected tool before finalizing the answer.",
        )
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (_acceptance_criterion(_CRITERION_TOOL_BACKED_FINAL_ANSWER),),
        )
    return acceptance_criteria


def _append_media_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    resource_index: ResourceIndex,
    selected: list[ResourceRef],
    required_tools: frozenset[str],
) -> list[AcceptanceCriterion]:
    image_resources = resource_index.by_kind("image")
    audio_resources = resource_index.by_kind("audio")
    video_resources = resource_index.by_kind("video")
    selected.extend(image_resources + audio_resources + video_resources)
    if image_resources:
        requirements.append(
            EvidenceRequirement(
                kind="resource_coverage",
                tools=_coverage_tools(required_tools, (OCR_IMAGE_TOOL_NAME, ANALYZE_IMAGE_TOOL_NAME)),
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
                tools=_coverage_tools(required_tools, (TRANSCRIBE_AUDIO_TOOL_NAME,)),
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
                tools=_coverage_tools(required_tools, (ANALYZE_VIDEO_TOOL_NAME,)),
                resource_ids=tuple(item.id for item in video_resources),
                coverage="all",
                min_count=len(video_resources),
                description="Analyze each referenced video before finalizing the answer.",
            )
        )
    return _append_acceptance_criteria(
        acceptance_criteria,
        (
            _acceptance_criterion(_CRITERION_MEDIA_ARTIFACT),
            _acceptance_criterion(_CRITERION_MEDIA_FINAL_ANSWER),
        ),
    )


def _append_web_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    min_source_count: int,
) -> None:
    _append_tool_evidence_requirement(
        requirements,
        tools=tuple(sorted(WEB_SOURCE_ARTIFACT_TOOLS)),
        description="Use web research tools before answering this external information request.",
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
            _acceptance_criterion(_CRITERION_WEB_FINAL_ANSWER),
            _acceptance_criterion(_CRITERION_WEB_SOURCE_REFERENCE),
        ),
    )


def _append_workspace_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
) -> None:
    _append_tool_evidence_requirement(
        requirements,
        tools=_workspace_evidence_tools(),
        description="Inspect the relevant workspace files or code context before answering.",
    )
    acceptance_criteria[:] = _append_acceptance_criteria(
        acceptance_criteria,
        (_acceptance_criterion(_CRITERION_WORKSPACE_FINAL_ANSWER),),
    )


def _append_tool_evidence_requirement(
    requirements: list[EvidenceRequirement],
    *,
    tools: tuple[str, ...],
    description: str,
) -> None:
    normalized_tools = tuple(dict.fromkeys(_policy_value(tool) for tool in tools if _policy_value(tool)))
    if not normalized_tools or _has_requirement(requirements, kind=REQUIRED_TOOL_EVIDENCE_KIND, tools=normalized_tools):
        return
    requirements.append(
        EvidenceRequirement(
            kind=REQUIRED_TOOL_EVIDENCE_KIND,
            tools=normalized_tools,
            min_count=1,
            description=description,
        )
    )


def _coverage_tools(required_tools: frozenset[str], candidates: tuple[str, ...]) -> tuple[str, ...]:
    selected = tuple(tool_name for tool_name in candidates if tool_name in required_tools)
    return selected or candidates


def _workspace_evidence_tools() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *WORKSPACE_DISCOVERY_TOOL_NAMES,
                LIST_RUN_FILE_CHANGES_TOOL_NAME,
                PREVIEW_RUN_FILE_CHANGE_REVERT_TOOL_NAME,
            }
        )
    )


def _history_evidence_tools(tool_names: frozenset[str]) -> tuple[str, ...]:
    selected = tuple(tool_name for tool_name in (HISTORY_SEARCH_TOOL_NAME, LIST_RUN_FILE_CHANGES_TOOL_NAME) if tool_name in tool_names)
    return selected or (HISTORY_SEARCH_TOOL_NAME, LIST_RUN_FILE_CHANGES_TOOL_NAME)


def _operation_evidence_tools(tool_names: frozenset[str]) -> tuple[str, ...]:
    candidates = (
        EXEC_TOOL_NAME,
        PROCESS_TOOL_NAME,
        CRON_TOOL_NAME,
        CONFIGURE_MCP_TOOL_NAME,
        DELEGATE_TOOL_NAME,
        DELEGATE_MANY_TOOL_NAME,
        RUN_WORKFLOW_TOOL_NAME,
    )
    selected = tuple(tool_name for tool_name in candidates if tool_name in tool_names)
    return selected or candidates


def _is_media_tool_selected(tool_names: frozenset[str]) -> bool:
    return bool(tool_names & {ANALYZE_IMAGE_TOOL_NAME, OCR_IMAGE_TOOL_NAME, TRANSCRIBE_AUDIO_TOOL_NAME, ANALYZE_VIDEO_TOOL_NAME})


def _is_history_tool_selected(tool_names: frozenset[str]) -> bool:
    return bool(tool_names & {HISTORY_SEARCH_TOOL_NAME, LIST_RUN_FILE_CHANGES_TOOL_NAME})


def _is_operation_tool_selected(tool_names: frozenset[str]) -> bool:
    return bool(
        tool_names
        & {
            *EXECUTION_TOOL_NAMES,
            CONFIGURE_MCP_TOOL_NAME,
            CRON_TOOL_NAME,
            DELEGATE_MANY_TOOL_NAME,
            DELEGATE_TOOL_NAME,
            RUN_WORKFLOW_TOOL_NAME,
        }
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = _json_object_text(text) or text
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
    return _compact_text(text)


def _truncate(text: str, *, max_chars: int) -> str:
    return _truncate_text(text, max_chars=max_chars)


def _truncate_middle(text: str, *, max_chars: int) -> str:
    return _truncate_middle_text(text, max_chars=max_chars)


def _allowed_string(value: Any, allowed: frozenset[str]) -> str | None:
    return _allowed_policy_value(value, allowed)


def _coerce_bool(value: Any) -> bool:
    return _coerce_policy_bool(value, truthy_values=_PLANNER_TRUE_VALUES)


def _coerce_confidence(value: Any) -> float:
    return _coerce_policy_confidence(value)
