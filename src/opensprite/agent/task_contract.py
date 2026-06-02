"""Task contracts and evidence requirements for completion checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..llms import ChatMessage
from .resource_index import ResourceIndex, ResourceRef
from .task_context_resolver import TaskContextDecision, TaskContextResolver
from .task_intent import TaskIntent
from .tool_groups import TOOL_GROUPS
from ..tools.evidence import ToolEvidence

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_ALLOWED_PLANNER_TOOL_GROUPS = frozenset(TOOL_GROUPS.keys())
_ALLOWED_PLANNER_TASK_TYPES = frozenset(
    {
        "pure_answer",
        "web_research",
        "workspace_read",
        "workspace_change",
        "code_change",
        "media_analysis",
        "media_extraction",
        "history_retrieval",
        "ops",
        "operations",
        "task",
        "analysis",
    }
)
_PLANNER_TASK_TYPE_ALIASES = {
    "workspace_change": "code_change",
    "media_analysis": "media_extraction",
    "ops": "operations",
}
_PLANNER_CONTRACT_SYSTEM_PROMPT = (
    "You are the OpenSprite task-contract planner. Decide what tool evidence the latest user turn needs "
    "before the main assistant sees tools. Return only one JSON object. Do not include markdown. "
    "Choose task_type from: pure_answer, web_research, workspace_read, workspace_change, media_analysis, "
    "history_retrieval, ops, task, analysis. Choose required_tool_groups only from: web_research, "
    "workspace_read, workspace_write, media, history_retrieval, scheduling, execution, verification. If no tool evidence is needed, "
    "use pure_answer and an empty required_tool_groups array. The JSON keys are: task_type, "
    "required_tool_groups, final_answer_required, allow_no_tool_final, reason."
)
_PLANNER_REPAIR_SYSTEM_PROMPT = (
    "You repair OpenSprite task-contract planner output. Convert the invalid planner response into exactly one "
    "valid JSON object for the same schema. Return JSON only, no markdown, no explanation."
)
_COMMAND_USAGE_DISCUSSION_RE = re.compile(
    r"\b(?:cli|command|commands?|usage|how to use|examples?)\b"
    r"|(?:指令|命令|怎麼用|如何用|用法|用途|範例)",
    re.IGNORECASE,
)
_EXPLICIT_WORKSPACE_EVIDENCE_RE = re.compile(
    r"\b(?:read|inspect|open|grep|search)\s+(?:the\s+)?(?:file|repo|repository|codebase|workspace)\b"
    r"|(?:讀檔|讀取|查看檔案|檢查檔案|搜尋檔案|打開檔案)",
    re.IGNORECASE,
)
_NO_WORKSPACE_EVIDENCE_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\b[^.?!\n]{0,48}"
    r"\b(?:read|inspect|open|grep|search)\b[^.?!\n]{0,48}"
    r"\b(?:file|files|repo|repository|codebase|workspace|project|source)\b"
    r"|(?:不要|別|不用|無需)[^。！？\n]{0,20}"
    r"(?:讀檔|讀取檔案|看檔案|查看檔案|檢查檔案|搜尋檔案|看專案|看工作區|讀工作區)",
    re.IGNORECASE,
)
_RECENT_CONTEXT_NO_TOOL_RE = re.compile(
    r"\b(?:previous answer|last answer|previous question|last question|above|just now)\b"
    r"|(?:\u5ef6\u7e8c|\u4e0a\u4e00\u984c|\u4e0a\u984c|\u4e0a\u4e00\u500b|\u525b\u525b|\u525b\u624d|\u524d\u9762|\u4e0a\u9762)",
    re.IGNORECASE,
)
_NO_NEW_EVIDENCE_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\b[^.?!\n]{0,48}"
    r"\b(?:web|internet|online|search|research|browse|fetch|look up)\b"
    r"|(?:\u4e0d\u8981|\u4e0d\u7528|\u5225|\u5148\u4e0d\u8981)[^\n\u3002\uff01\uff1f]{0,24}"
    r"(?:\u91cd\u65b0\u67e5|\u518d\u67e5|\u4e0a\u7db2|\u641c\u5c0b|\u641c|\u67e5\u8a62|\u6293\u8cc7\u6599)",
    re.IGNORECASE,
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
    contract_sources: tuple[str, ...] = ("deterministic",)
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
        task_type="pure_answer",
        final_answer_required=True,
        allow_no_tool_final=True,
        contract_sources=("missing_runtime_contract",),
        planner_metadata={
            "planner_status": "missing",
            "reason": "execution result did not include a task contract",
        },
    )


class TaskContractPlanner:
    """LLM-backed planner that produces the authoritative per-turn task contract."""

    def __init__(self, llm_config: DocumentLlmConfig):
        self.llm_config = llm_config

    async def plan(
        self,
        *,
        provider: Any,
        model: str | None,
        task_intent: TaskIntent,
        current_message: str,
        history: list[dict[str, Any]] | None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
        task_context_decision: TaskContextDecision | None = None,
    ) -> TaskContract:
        if provider is None or str(model or "").strip().lower() == "unconfigured":
            return _planner_blocked_contract(
                objective=str(task_intent.objective or current_message or "").strip(),
                reason="task contract planner unavailable: llm not configured",
            )
        planner_prompt = _build_planner_contract_prompt(
            current_message=current_message,
            history=history or [],
            task_intent=task_intent,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
        )
        try:
            response = await provider.chat(
                [
                    ChatMessage(role="system", content=_PLANNER_CONTRACT_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=planner_prompt),
                ],
                model=model,
                **self.llm_config.decoding_kwargs(),
            )
        except Exception as exc:
            return _fallback_contract_from_intent(
                task_intent=task_intent,
                current_message=current_message,
                history=history,
                current_image_files=current_image_files,
                current_audio_files=current_audio_files,
                current_video_files=current_video_files,
                task_context_decision=task_context_decision,
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
                return _fallback_contract_from_intent(
                    task_intent=task_intent,
                    current_message=current_message,
                    history=history,
                    current_image_files=current_image_files,
                    current_audio_files=current_audio_files,
                    current_video_files=current_video_files,
                    task_context_decision=task_context_decision,
                    reason=_planner_exception_reason(exc),
                    raw_response_preview=_truncate(response_text, max_chars=400),
                )
            repair_text = str(getattr(repair_response, "content", "") or "")
            payload = _parse_json_object(repair_text)
            if not payload:
                response_text = repair_text or response_text
        if not payload:
            return _fallback_contract_from_intent(
                task_intent=task_intent,
                current_message=current_message,
                history=history,
                current_image_files=current_image_files,
                current_audio_files=current_audio_files,
                current_video_files=current_video_files,
                task_context_decision=task_context_decision,
                reason="task contract planner returned invalid JSON",
                raw_response_preview=_truncate(response_text, max_chars=240),
            )
        return _contract_from_planner_payload(
            payload,
            task_intent=task_intent,
            current_message=current_message,
            history=history,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
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


def missing_evidence(contract: TaskContract | None, evidence: tuple[ToolEvidence, ...], *, file_change_count: int, verification_passed: bool) -> tuple[str, ...]:
    """Return human-readable missing evidence items for a contract."""
    if contract is None:
        return ()
    missing: list[str] = []
    ok_evidence = [item for item in evidence if item.ok]
    aliases = ResourceIndex.aliases_for(contract.selected_resources)
    for requirement in contract.requirements:
        if requirement.kind == "tool_group":
            tools = TOOL_GROUPS.get(requirement.tool_group, frozenset())
            count = sum(1 for item in ok_evidence if item.name in tools)
            if count < max(1, requirement.min_count):
                missing.append(requirement.description or f"Use one of: {', '.join(sorted(tools))}")
        elif requirement.kind == "resource_coverage":
            tools = TOOL_GROUPS.get(requirement.tool_group, frozenset())
            covered = {
                alias
                for item in ok_evidence
                if item.name in tools
                for resource_id in item.resource_ids
                for alias in aliases.get(resource_id, {resource_id})
            }
            required = set(requirement.resource_ids)
            if requirement.coverage == "all":
                uncovered = tuple(resource_id for resource_id in requirement.resource_ids if resource_id not in covered)
                if uncovered:
                    missing.append(
                        f"Missing {requirement.tool_group} coverage for: {', '.join(uncovered)}"
                    )
            elif len(covered & required) < max(1, requirement.min_count):
                missing.append(requirement.description or f"Missing {requirement.tool_group} coverage")
        elif requirement.kind == "file_change" and file_change_count < max(1, requirement.min_count):
            missing.append(requirement.description or "Record a workspace file change.")
        elif requirement.kind == "verification" and not verification_passed:
            missing.append(requirement.description or "Record passing verification evidence.")
    return tuple(missing)




def _tool_group_requirement(tool_group: str) -> EvidenceRequirement:
    if tool_group == "web_research":
        return EvidenceRequirement(
            kind="tool_group",
            tool_group="web_research",
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
    seen = {item.kind for item in existing}
    for criterion in additions:
        if criterion.kind not in seen:
            existing.append(criterion)
            seen.add(criterion.kind)
    return existing


def _build_planner_contract_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    task_intent: TaskIntent,
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
) -> str:
    context = {
        "current_message": _truncate(current_message, max_chars=1200),
        "task_intent": task_intent.to_metadata(),
        "recent_history": _recent_history(history),
        "attachments": {
            "image_files": list(current_image_files or []),
            "audio_files": list(current_audio_files or []),
            "video_files": list(current_video_files or []),
        },
        "task_context": task_context_decision.to_metadata() if task_context_decision is not None else None,
    }
    return (
        "Create the task contract for the latest user turn. The contract controls which tools the main assistant can see.\n"
        "Use semantic judgment from the message and recent history, not string matching. If the user asks for current, "
        "external, public, financial, weather, news, webpage, or source-grounded facts, choose web_research. "
        "If the user asks about local files, repo code, project state, or wants code changes, choose workspace_read or "
        "workspace_change. If the user asks about attached media, choose media_analysis. If the user asks about previous "
    "conversation state, choose history_retrieval. If the user asks to schedule, remind, pause, list, or run reminders "
    "or recurring jobs, choose ops with required_tool_groups ['scheduling']. If the user asks to inspect the local machine, "
    "installed commands, command versions, running processes, or local runtime state, choose ops with required_tool_groups "
    "['execution']. If no tool evidence is needed, choose pure_answer.\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "task_type": "pure_answer | web_research | workspace_read | workspace_change | media_analysis | history_retrieval | ops | task | analysis",\n'
        '  "required_tool_groups": ["web_research | workspace_read | workspace_write | media | history_retrieval | scheduling | execution | verification"],\n'
        '  "final_answer_required": true,\n'
        '  "allow_no_tool_final": true,\n'
        '  "reason": "short explanation for trace only"\n'
        "}\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _planner_blocked_contract(
    *,
    objective: str,
    reason: str,
    status: str = "blocked",
    raw_response_preview: str = "",
) -> TaskContract:
    metadata: dict[str, Any] = {
        "planner_status": status,
        "reason": reason,
    }
    if raw_response_preview:
        metadata["raw_response_preview"] = raw_response_preview
    return TaskContract(
        objective=objective,
        task_type="planning_error",
        final_answer_required=True,
        allow_no_tool_final=False,
        contract_sources=("llm_planner",),
        acceptance_criteria=(
            AcceptanceCriterion(
                kind="planner_error_report",
                description="Explain that task contract planning failed and a reliable tool profile could not be selected.",
            ),
        ),
        planner_metadata=metadata,
    )


def _fallback_contract_from_intent(
    *,
    task_intent: TaskIntent,
    current_message: str,
    history: list[dict[str, Any]] | None,
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
    reason: str,
    raw_response_preview: str = "",
) -> TaskContract:
    task_type = "pure_answer"
    tool_groups: list[str] = []

    if current_image_files or current_audio_files or current_video_files:
        task_type = "media_analysis"
        tool_groups = ["media"]

    contract = _contract_from_planner_payload(
        {
            "task_type": task_type,
            "required_tool_groups": tool_groups,
            "final_answer_required": True,
            "allow_no_tool_final": not tool_groups,
            "reason": reason,
        },
        task_intent=task_intent,
        current_message=current_message,
        history=history,
        current_image_files=current_image_files,
        current_audio_files=current_audio_files,
        current_video_files=current_video_files,
        task_context_decision=task_context_decision,
    )
    metadata = dict(contract.planner_metadata or {})
    metadata.update(
        {
            "planner_status": "fallback",
            "reason": reason,
        }
    )
    if raw_response_preview:
        metadata["raw_response_preview"] = raw_response_preview
    return TaskContract(
        objective=contract.objective,
        task_type=contract.task_type,
        requirements=contract.requirements,
        acceptance_criteria=contract.acceptance_criteria,
        selected_resources=contract.selected_resources,
        final_answer_required=contract.final_answer_required,
        allow_no_tool_final=contract.allow_no_tool_final,
        contract_sources=("llm_planner", "fallback"),
        harness_profile=contract.harness_profile,
        planner_metadata=metadata,
    )


def _planner_exception_reason(exc: Exception) -> str:
    error_type = exc.__class__.__name__
    message = str(exc).strip()
    if message:
        return f"task contract planner LLM call failed: {error_type}: {message}"
    return f"task contract planner LLM call failed: {error_type}"


def _contract_from_planner_payload(
    payload: dict[str, Any],
    *,
    task_intent: TaskIntent,
    current_message: str,
    history: list[dict[str, Any]] | None,
    current_image_files: list[str] | None,
    current_audio_files: list[str] | None,
    current_video_files: list[str] | None,
    task_context_decision: TaskContextDecision | None,
) -> TaskContract:
    objective = str(task_intent.objective or current_message or "").strip()
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
            status="invalid",
            reason="task contract planner returned an unsupported or missing task_type",
            raw_response_preview=_truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), max_chars=240),
        )
    forced_no_tool = _is_no_tool_command_usage_question(task_intent, current_message)
    forced_recent_context_no_tool = _is_recent_context_no_tool_follow_up(
        current_message=current_message,
        history=history,
    )
    if forced_no_tool or forced_recent_context_no_tool:
        raw_task_type = "pure_answer"
    task_type = _PLANNER_TASK_TYPE_ALIASES.get(raw_task_type, raw_task_type)
    tool_groups = _normalize_planner_tool_groups(payload.get("required_tool_groups"))
    if forced_no_tool or forced_recent_context_no_tool:
        tool_groups = []
    if task_type == "history_retrieval":
        tool_groups = [tool_group for tool_group in tool_groups if tool_group == "history_retrieval"]
    inherited_tool_group = getattr(task_context_decision, "inherited_tool_group", "") or ""
    if (
        inherited_tool_group in _ALLOWED_PLANNER_TOOL_GROUPS
        and inherited_tool_group not in tool_groups
        and not _message_forbids_inherited_tool_group(current_message, inherited_tool_group)
    ):
        tool_groups.append(inherited_tool_group)
    _ensure_task_type_tool_groups(task_type, tool_groups)

    requirements: list[EvidenceRequirement] = []
    acceptance_criteria: list[AcceptanceCriterion] = []
    selected: list[ResourceRef] = []

    for tool_group in tool_groups:
        if tool_group == "web_research":
            _append_web_contract(requirements, acceptance_criteria, min_source_count=2)
        elif tool_group == "workspace_read":
            _append_workspace_contract(requirements, acceptance_criteria)
        elif tool_group == "workspace_write":
            _append_workspace_contract(requirements, acceptance_criteria)
            if not _has_requirement(requirements, kind="file_change"):
                requirements.append(
                    EvidenceRequirement(
                        kind="file_change",
                        min_count=1,
                        description="Record at least one workspace file change.",
                    )
                )
            acceptance_criteria = _append_acceptance_criteria(acceptance_criteria, (_verification_or_gap_criterion(),))
        elif tool_group == "media":
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
                requirements.append(_tool_group_requirement("media"))
            acceptance_criteria = _append_acceptance_criteria(
                acceptance_criteria,
                (_media_artifact_criterion(), _media_final_answer_criterion()),
            )
        elif tool_group == "history_retrieval":
            requirements.append(_tool_group_requirement("history_retrieval"))
            acceptance_criteria = _append_acceptance_criteria(acceptance_criteria, (_history_final_answer_criterion(),))
        elif tool_group == "scheduling":
            requirements.append(_tool_group_requirement("scheduling"))
            acceptance_criteria = _append_acceptance_criteria(acceptance_criteria, (_operation_report_criterion(),))
        elif tool_group == "execution":
            requirements.append(_tool_group_requirement("execution"))
            acceptance_criteria = _append_acceptance_criteria(acceptance_criteria, (_operation_report_criterion(),))
        elif tool_group == "verification":
            requirements.append(
                EvidenceRequirement(
                    kind="verification",
                    tool_group="verification",
                    min_count=1,
                    description="Record verification evidence before finalizing.",
                )
            )

    planner_reason = _truncate(str(payload.get("reason") or "llm planner returned a task contract"), max_chars=240)
    metadata = {
        "planner_status": "validated",
        "raw_task_type": raw_task_type,
        "required_tool_groups": list(tool_groups),
        "reason": planner_reason,
    }
    if forced_no_tool:
        metadata["override_reason"] = "command usage question does not require workspace evidence"
    if forced_recent_context_no_tool:
        metadata["override_reason"] = "immediate follow-up explicitly asked not to gather new evidence"
    return TaskContract(
        objective=objective,
        task_type=task_type,
        requirements=tuple(requirements),
        acceptance_criteria=tuple(acceptance_criteria),
        selected_resources=tuple(dict.fromkeys(selected)),
        final_answer_required=_coerce_bool(payload.get("final_answer_required", True)),
        allow_no_tool_final=(True if (forced_no_tool or forced_recent_context_no_tool) else _coerce_bool(payload.get("allow_no_tool_final", not requirements))) and not requirements,
        contract_sources=("llm_planner",),
        planner_metadata=metadata,
    )


def _normalize_planner_tool_groups(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else []
    groups: list[str] = []
    for item in raw_values:
        text = str(item or "").strip()
        if text == "workspace_change":
            text = "workspace_write"
        elif text == "media_analysis":
            text = "media"
        elif text == "ops":
            text = "verification"
        if text in _ALLOWED_PLANNER_TOOL_GROUPS and text not in groups:
            groups.append(text)
    return groups


def _is_no_tool_command_usage_question(task_intent: TaskIntent, current_message: str) -> bool:
    text = str(current_message or task_intent.objective or "").strip()
    if task_intent.kind != "question":
        return False
    if task_intent.expects_code_change or task_intent.expects_verification:
        return False
    forbids_workspace_evidence = bool(_NO_WORKSPACE_EVIDENCE_RE.search(text))
    if _EXPLICIT_WORKSPACE_EVIDENCE_RE.search(text) and not forbids_workspace_evidence:
        return False
    return forbids_workspace_evidence and bool(_COMMAND_USAGE_DISCUSSION_RE.search(text))


def _is_recent_context_no_tool_follow_up(
    *,
    current_message: str,
    history: list[dict[str, Any]] | None,
) -> bool:
    text = str(current_message or "").strip()
    if not text:
        return False
    if not (_RECENT_CONTEXT_NO_TOOL_RE.search(text) and _NO_NEW_EVIDENCE_RE.search(text)):
        return False
    return any(str(item.get("role") or "") == "assistant" and str(item.get("content") or "").strip() for item in (history or [])[-4:])


def _ensure_task_type_tool_groups(task_type: str, tool_groups: list[str]) -> None:
    required: tuple[str, ...]
    if task_type == "web_research":
        required = ("web_research",)
    elif task_type == "workspace_read":
        required = ("workspace_read",)
    elif task_type == "code_change":
        required = ("workspace_read", "workspace_write")
    elif task_type == "media_extraction":
        required = ("media",)
    elif task_type == "history_retrieval":
        required = ("history_retrieval",)
    else:
        required = ()
    for tool_group in required:
        if tool_group not in tool_groups:
            tool_groups.append(tool_group)


def _append_web_contract(
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    min_source_count: int,
) -> None:
    if not _has_requirement(requirements, kind="tool_group", tool_group="web_research"):
        requirements.append(
            EvidenceRequirement(
                kind="tool_group",
                tool_group="web_research",
                coverage="any",
                min_count=1,
                description="Use web research tools before answering this external information request.",
            )
        )
    acceptance_criteria[:] = _append_acceptance_criteria(
        acceptance_criteria,
        (
            AcceptanceCriterion(
                kind="source_artifact",
                min_count=min_source_count,
                description="Produce enough traceable web sources before finalizing the answer.",
            ),
            AcceptanceCriterion(
                kind="source_detail",
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
    if not _has_requirement(requirements, kind="tool_group", tool_group="workspace_read"):
        requirements.append(
            EvidenceRequirement(
                kind="tool_group",
                tool_group="workspace_read",
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


def _message_forbids_inherited_tool_group(message: str, tool_group: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(message or "").strip().lower())
    if not normalized:
        return False
    if tool_group == "web_research":
        return bool(re.search(
            r"(?:不要|不用|不需要|別|别)[^。.!?]{0,16}(?:重新)?(?:搜尋|搜寻|上網|上网|查網路|查网络)"
            r"|\b(?:do not|don't|dont|without|no)\b[^.。!?]{0,32}\b(?:web|internet|online|search|research)\b",
            normalized,
        ))
    if tool_group in {"workspace_read", "workspace_write"}:
        return bool(re.search(
            r"(?:不要|不用|不需要|別|别)[^。.!?]{0,16}(?:讀檔|读档|看檔|看档|改檔|改档|讀取檔案|读取文件)"
            r"|\b(?:do not|don't|dont|without|no)\b[^.。!?]{0,32}\b(?:files?|workspace|read|edit|write)\b",
            normalized,
        ))
    return False


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
        kind="source_reference",
        min_count=1,
        description="Reference at least one gathered web source by URL, domain, or title.",
    )


def _workspace_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the inspected workspace context.",
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
