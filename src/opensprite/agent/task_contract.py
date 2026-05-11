"""Task contracts and evidence requirements for completion checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..llms import ChatMessage
from .resource_index import ResourceIndex, ResourceRef
from .task_context_resolver import TaskContextDecision, TaskContextResolver
from .task_intent import TaskIntent
from ..tools.evidence import ToolEvidence


TOOL_GROUPS: dict[str, frozenset[str]] = {
    "image_text": frozenset({"ocr_image", "analyze_image"}),
    "image_understanding": frozenset({"analyze_image"}),
    "audio_text": frozenset({"transcribe_audio"}),
    "video_understanding": frozenset({"analyze_video"}),
    "web_research": frozenset({"web_search", "web_fetch", "web_research", "browser_navigate", "browser_snapshot"}),
    "history_retrieval": frozenset({"search_history", "search_knowledge"}),
    "workspace_read": frozenset({"read_file", "glob_files", "grep_files", "code_navigation"}),
    "workspace_write": frozenset({"apply_patch", "write_file", "edit_file"}),
    "verification": frozenset({"verify", "exec"}),
}

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_IMAGE_TASK_HINT_RE = re.compile(
    r"\b(?:image|images|photo|photos|picture|pictures|screenshot|screenshots|prompt|prompts|ocr|text)\b"
    r"|(?:圖片|照片|截圖|圖|文字|提示詞|提詞|讀取|辨識|抓出|取出|提取|擷取)",
    re.IGNORECASE,
)
_AUDIO_TASK_HINT_RE = re.compile(r"\b(?:audio|voice|speech|transcribe)\b|(?:音訊|語音|錄音|轉錄)", re.IGNORECASE)
_VIDEO_TASK_HINT_RE = re.compile(r"\b(?:video|clip)\b|(?:影片|視頻|短片)", re.IGNORECASE)
_WEB_TASK_HINT_RE = re.compile(
    r"\b(?:web|internet|online|reddit|url|link|search|news)\b"
    r"|(?:上網|網路|搜尋|新聞|來源|連結|即時|市值|股價|報價|匯率|天氣)",
    re.IGNORECASE,
)
_ALLOWED_SEMANTIC_TOOL_GROUPS = frozenset({"web_research", "workspace_read", "history_retrieval"})
_ALLOWED_SEMANTIC_TASK_TYPES = frozenset({"web_research", "workspace_read", "task", "analysis", "pure_answer"})
_SEMANTIC_CONTRACT_SYSTEM_PROMPT = (
    "Classify whether a user request needs tool-derived evidence before the final answer. "
    "Return only JSON. You may only add stricter requirements; never remove deterministic evidence requirements."
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
    semantic_contract: dict[str, Any] | None = None

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
        if self.semantic_contract:
            payload["semantic_contract"] = dict(self.semantic_contract)
        return payload


@dataclass(frozen=True)
class SemanticContractDecision:
    """Optional semantic contract signal from a classifier or test stub."""

    requires_tool_evidence: bool = False
    required_tool_group: str | None = None
    task_type: str | None = None
    allow_no_tool_final: bool | None = None
    confidence: float = 0.0
    reason: str = ""

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requires_tool_evidence": self.requires_tool_evidence,
            "confidence": self.confidence,
            "reason": self.reason,
        }
        if self.required_tool_group:
            payload["required_tool_group"] = self.required_tool_group
        if self.task_type:
            payload["task_type"] = self.task_type
        if self.allow_no_tool_final is not None:
            payload["allow_no_tool_final"] = self.allow_no_tool_final
        return payload


class SemanticContractClassifier:
    """LLM-backed classifier for ambiguous task contracts."""

    async def classify(
        self,
        *,
        provider: Any,
        model: str | None,
        task_intent: TaskIntent,
        current_message: str,
        history: list[dict[str, Any]] | None,
        deterministic_contract: TaskContract,
    ) -> SemanticContractDecision | None:
        if not should_classify_semantic_contract(
            current_message=current_message,
            task_intent=task_intent,
            deterministic_contract=deterministic_contract,
        ):
            return None
        if provider is None or str(model or "").strip().lower() == "unconfigured":
            return SemanticContractDecision(reason="semantic classifier unavailable: llm not configured")

        response = await provider.chat(
            [
                ChatMessage(role="system", content=_SEMANTIC_CONTRACT_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=_build_semantic_contract_prompt(
                        current_message=current_message,
                        history=history or [],
                        task_intent=task_intent,
                        deterministic_contract=deterministic_contract,
                    ),
                ),
            ],
            model=model,
            temperature=0.0,
            max_tokens=400,
        )
        return _semantic_decision_from_payload(_parse_json_object(str(getattr(response, "content", "") or "")))


class TaskContractService:
    """Build deterministic contracts from known turn facts."""

    @classmethod
    def build(
        cls,
        *,
        task_intent: TaskIntent,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
        task_context_decision: TaskContextDecision | None = None,
        semantic_decision: SemanticContractDecision | None = None,
    ) -> TaskContract:
        deterministic = cls.build_deterministic(
            task_intent=task_intent,
            current_message=current_message,
            history=history,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
        )
        return merge_semantic_contract(deterministic, semantic_decision)

    @classmethod
    def build_deterministic(
        cls,
        *,
        task_intent: TaskIntent,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
        task_context_decision: TaskContextDecision | None = None,
    ) -> TaskContract:
        objective = str(task_intent.objective or current_message or "").strip()
        text = f"{objective}\n{current_message or ''}"
        task_context_decision = task_context_decision or TaskContextResolver.resolve_deterministic(
            current_message=current_message,
            history=history,
            task_intent=task_intent,
        )
        resource_index = ResourceIndex.from_turn_and_history(
            current_message=current_message,
            history=history,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
        )

        requirements: list[EvidenceRequirement] = []
        acceptance_criteria: list[AcceptanceCriterion] = []
        selected: list[ResourceRef] = []
        task_type = _task_type_from_intent(task_intent)

        image_resources = resource_index.by_kind("image")
        audio_resources = resource_index.by_kind("audio")
        video_resources = resource_index.by_kind("video")
        inherited_tool_group = task_context_decision.inherited_tool_group

        if image_resources and (
            cls._looks_like_image_task(text, task_intent, current_image_files)
            or inherited_tool_group == "image_text"
        ):
            selected.extend(image_resources)
            acceptance_criteria.append(_media_final_answer_criterion())
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
            task_type = "media_extraction"
        elif audio_resources and (
            cls._looks_like_audio_task(text, current_audio_files)
            or inherited_tool_group == "audio_text"
        ):
            selected.extend(audio_resources)
            acceptance_criteria.append(_media_final_answer_criterion())
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
            task_type = "media_extraction"
        elif video_resources and (
            cls._looks_like_video_task(text, current_video_files)
            or inherited_tool_group == "video_understanding"
        ):
            selected.extend(video_resources)
            acceptance_criteria.append(_media_final_answer_criterion())
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
            task_type = "media_extraction"

        web_required = cls._looks_like_web_task(text)
        if not web_required and not requirements:
            web_required = inherited_tool_group == "web_research"

        if web_required:
            min_source_count = 1 if _URL_RE.search(text) else 2
            acceptance_criteria.append(
                AcceptanceCriterion(
                    kind="source_artifact",
                    min_count=min_source_count,
                    description="Produce enough traceable web sources before finalizing the answer.",
                )
            )
            acceptance_criteria.append(
                AcceptanceCriterion(
                    kind="source_detail",
                    min_count=1,
                    description="Fetch or inspect at least one source page before finalizing; search snippets alone are not enough.",
                )
            )
            acceptance_criteria.append(_web_final_answer_criterion())
            acceptance_criteria.append(_web_source_reference_criterion())
            requirements.append(
                EvidenceRequirement(
                    kind="tool_group",
                    tool_group="web_research",
                    coverage="any",
                    min_count=1,
                    description="Use web research tools before answering this external information request.",
                )
            )
            task_type = "web_research"

        if not requirements and inherited_tool_group == "workspace_read":
            requirements.append(
                EvidenceRequirement(
                    kind="tool_group",
                    tool_group="workspace_read",
                    coverage="any",
                    min_count=1,
                    description="Inspect the referenced workspace context before answering this follow-up.",
                )
            )
            task_type = "workspace_read"

        if task_intent.expects_code_change:
            requirements.append(
                EvidenceRequirement(
                    kind="file_change",
                    min_count=1,
                    description="Record at least one workspace file change.",
                )
            )
            task_type = "code_change"

        if task_intent.expects_verification:
            requirements.append(
                EvidenceRequirement(
                    kind="verification",
                    tool_group="verification",
                    min_count=1,
                    description="Record verification evidence before finalizing.",
                )
            )

        requested_count = _requested_item_count(objective)
        if requested_count >= 3:
            acceptance_criteria.append(
                AcceptanceCriterion(
                    kind="itemized_output",
                    min_count=min(requested_count, 3),
                    max_response_chars=260,
                    description="Provide the requested itemized result instead of a short acknowledgement.",
                )
            )

        return TaskContract(
            objective=objective,
            task_type=task_type,
            requirements=tuple(requirements),
            acceptance_criteria=tuple(acceptance_criteria),
            selected_resources=tuple(selected),
            final_answer_required=True,
            allow_no_tool_final=not requirements,
            contract_sources=("deterministic",),
        )

    @staticmethod
    def _looks_like_image_task(text: str, task_intent: TaskIntent, current_image_files: list[str] | None) -> bool:
        if current_image_files and task_intent.kind in {"analysis", "task", "writing", "question"}:
            return True
        return bool(_IMAGE_TASK_HINT_RE.search(text or ""))

    @staticmethod
    def _looks_like_audio_task(text: str, current_audio_files: list[str] | None) -> bool:
        return bool(current_audio_files or _AUDIO_TASK_HINT_RE.search(text or ""))

    @staticmethod
    def _looks_like_video_task(text: str, current_video_files: list[str] | None) -> bool:
        return bool(current_video_files or _VIDEO_TASK_HINT_RE.search(text or ""))

    @staticmethod
    def _looks_like_web_task(text: str) -> bool:
        return bool(_URL_RE.search(text or "") or _WEB_TASK_HINT_RE.search(text or ""))


def merge_semantic_contract(
    contract: TaskContract,
    semantic_decision: SemanticContractDecision | None,
    *,
    min_confidence: float = 0.7,
) -> TaskContract:
    """Merge a semantic contract decision without loosening deterministic requirements."""
    if semantic_decision is None:
        return contract

    metadata = semantic_decision.to_metadata()
    metadata["merge_policy"] = "semantic_may_only_add_requirements"
    confidence = max(0.0, min(1.0, float(semantic_decision.confidence or 0.0)))
    tool_group = str(semantic_decision.required_tool_group or "").strip()
    can_apply = bool(
        semantic_decision.requires_tool_evidence
        and tool_group
        and confidence >= min_confidence
    )
    metadata["applied"] = can_apply
    if not can_apply:
        return TaskContract(
            objective=contract.objective,
            task_type=contract.task_type,
            requirements=contract.requirements,
            acceptance_criteria=contract.acceptance_criteria,
            selected_resources=contract.selected_resources,
            final_answer_required=contract.final_answer_required,
            allow_no_tool_final=contract.allow_no_tool_final,
            contract_sources=_append_unique(contract.contract_sources, "semantic_classifier"),
            semantic_contract=metadata,
        )

    requirements = list(contract.requirements)
    if not any(item.kind == "tool_group" and item.tool_group == tool_group for item in requirements):
        requirements.append(_semantic_tool_requirement(tool_group))

    acceptance_criteria = list(contract.acceptance_criteria)
    if tool_group == "web_research":
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (
                AcceptanceCriterion(
                    kind="source_artifact",
                    min_count=2,
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

    next_task_type = contract.task_type
    semantic_task_type = str(semantic_decision.task_type or "").strip()
    if semantic_task_type and contract.task_type in {"pure_answer", "task", "conversation", "question"}:
        next_task_type = semantic_task_type
    elif tool_group == "web_research" and contract.task_type in {"pure_answer", "task", "conversation", "question"}:
        next_task_type = "web_research"

    return TaskContract(
        objective=contract.objective,
        task_type=next_task_type,
        requirements=tuple(requirements),
        acceptance_criteria=tuple(acceptance_criteria),
        selected_resources=contract.selected_resources,
        final_answer_required=contract.final_answer_required,
        allow_no_tool_final=contract.allow_no_tool_final and not requirements,
        contract_sources=_append_unique(contract.contract_sources, "semantic_classifier"),
        semantic_contract=metadata,
    )


def should_classify_semantic_contract(
    *,
    current_message: str,
    task_intent: TaskIntent,
    deterministic_contract: TaskContract,
) -> bool:
    """Return whether an optional semantic pass may add missing requirements."""
    if deterministic_contract.requirements:
        return False
    if task_intent.expects_code_change or task_intent.expects_verification:
        return False
    if task_intent.kind in {"command", "media_upload"}:
        return False
    message = _compact(current_message)
    if not message or len(message) > 500:
        return False
    if _URL_RE.search(message):
        return False
    if task_intent.kind == "conversation" and not _looks_like_semantic_lookup_candidate(message):
        return False
    return task_intent.kind in {"task", "question", "conversation", "analysis", "writing", "planning"}


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


def _task_type_from_intent(task_intent: TaskIntent) -> str:
    if task_intent.expects_code_change:
        return "code_change"
    if task_intent.kind in {"conversation", "question", "command"}:
        return "pure_answer"
    return task_intent.kind or "task"


def _requested_item_count(objective: str) -> int:
    counts = [int(match) for match in re.findall(r"(?<!\d)\d{1,3}(?!\d)", str(objective or ""))]
    return max(counts, default=0)


def _append_unique(items: tuple[str, ...], item: str) -> tuple[str, ...]:
    values = [value for value in items if value]
    if item not in values:
        values.append(item)
    return tuple(values)


def _semantic_tool_requirement(tool_group: str) -> EvidenceRequirement:
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


def _build_semantic_contract_prompt(
    *,
    current_message: str,
    history: list[dict[str, Any]],
    task_intent: TaskIntent,
    deterministic_contract: TaskContract,
) -> str:
    context = {
        "current_message": _truncate(current_message, max_chars=700),
        "task_intent": task_intent.to_metadata(),
        "recent_history": _recent_history(history),
        "deterministic_contract": deterministic_contract.to_metadata(),
    }
    return (
        "Decide if the latest user request requires tool-derived evidence before the final answer.\n"
        "Use this for ambiguous, multilingual, shorthand, typo-heavy, or code-mixed requests.\n"
        "Classify requests for current/external facts, prices, finance/stock data, weather, news, web pages, or public data as web_research.\n"
        "Do not require tools for opinions, brainstorming, casual chat, or answers that can be completed from existing context.\n"
        "Return only JSON with keys: requires_tool_evidence, required_tool_group, task_type, allow_no_tool_final, confidence, reason.\n"
        "required_tool_group must be one of: web_research, workspace_read, history_retrieval, or null.\n"
        "task_type must be one of: web_research, workspace_read, task, analysis, pure_answer, or null.\n"
        "If unsure, use confidence below 0.7.\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _semantic_decision_from_payload(payload: dict[str, Any]) -> SemanticContractDecision:
    tool_group = _allowed_string(payload.get("required_tool_group"), _ALLOWED_SEMANTIC_TOOL_GROUPS)
    task_type = _allowed_string(payload.get("task_type"), _ALLOWED_SEMANTIC_TASK_TYPES)
    return SemanticContractDecision(
        requires_tool_evidence=_coerce_bool(payload.get("requires_tool_evidence")),
        required_tool_group=tool_group,
        task_type=task_type,
        allow_no_tool_final=(None if "allow_no_tool_final" not in payload else _coerce_bool(payload.get("allow_no_tool_final"))),
        confidence=_coerce_confidence(payload.get("confidence")),
        reason=_truncate(str(payload.get("reason") or "semantic classifier returned a decision"), max_chars=240),
    )


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


def _looks_like_semantic_lookup_candidate(text: str) -> bool:
    lowered = str(text or "").lower()
    return bool(
        re.search(r"\d", lowered)
        or lowered.endswith(("?", "？"))
        or any(marker in lowered for marker in ("多少", "哪", "什麼", "現在", "current", "latest"))
    )


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


def _media_final_answer_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind="substantive_final_answer",
        min_response_chars=80,
        description="Provide a substantive final answer that uses the inspected media results.",
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
