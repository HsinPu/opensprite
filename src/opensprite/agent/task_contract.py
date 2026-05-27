"""Task contracts and evidence requirements for completion checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..llms import ChatMessage
from .resource_index import ResourceIndex, ResourceRef
from .harness_profile import HarnessProfile, has_no_tool_constraint, has_no_web_constraint, has_no_workspace_constraint
from .task_context_resolver import TaskContextDecision, TaskContextResolver
from .task_intent import TaskIntent
from .tool_groups import TOOL_GROUPS
from ..tools.evidence import ToolEvidence

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_IMAGE_TASK_HINT_RE = re.compile(
    r"\b(?:image|images|photo|photos|picture|pictures|screenshot|screenshots|prompt|prompts|ocr|text)\b"
    r"|(?:圖片|照片|截圖|圖|文字|提示詞|提詞|讀取|辨識|抓出|取出|提取|擷取)",
    re.IGNORECASE,
)
_AUDIO_TASK_HINT_RE = re.compile(r"\b(?:audio|voice|speech|transcribe)\b|(?:音訊|語音|錄音|轉錄)", re.IGNORECASE)
_VIDEO_TASK_HINT_RE = re.compile(r"\b(?:video|clip)\b|(?:影片|視頻|短片)", re.IGNORECASE)
_WORKSPACE_TASK_HINT_RE = re.compile(
    r"\b(?:repo|repository|codebase|file|files|function|class|method|traceback|pytest|src/|tests/|apps/|todo)\b"
    r"|[\w.-]+\.(?:py|js|ts|vue|json|md|yaml|yml|toml)\b"
    r"|(?:程式|程式碼|檔案|函式|類別|專案|錯誤|測試|建置|設定|原始碼)",
    re.IGNORECASE,
)
_CODE_PATH_RE = re.compile(
    r"(?:^|\s)(?:[\w.-]+[\\/])+[\w.-]+|"
    r"(?:^|\s)[\w.-]+\.(?:py|js|ts|tsx|jsx|vue|json|toml|yaml|yml|md|css|html|java|go|rs|sql)(?:\s|$)",
    re.IGNORECASE,
)
_HISTORY_TASK_HINT_RE = re.compile(
    r"\b(?:again|before|earlier|history|last time|previous|revisit|repeat)\b"
    r"|(?:之前|先前|剛剛|上次|剛才|前面|剛提到|提過|說過)",
    re.IGNORECASE,
)
_WEB_KEYWORD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:web|internet|online|reddit|url|link|news)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_WEB_TASK_HINT_RE = re.compile(
    r"(?:上網|網路|新聞|來源|連結|即時|市值|股價|報價|匯率|天氣)",
    re.IGNORECASE,
)
_WEB_SEARCH_TERM_RE = re.compile(r"\b(?:search)\b|(?:搜尋)", re.IGNORECASE)
_NO_WEB_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\s+(?:use\s+)?(?:web|internet|online|search|sources?)\b"
    r"|(?:不要|不用|不需要|別)(?:上網|搜尋|搜索|查資料|查網路|使用\s*web)",
    re.IGNORECASE,
)
_LOCAL_RUNTIME_RE = re.compile(
    r"\b(?:channel|session id|current time|trace metrics?|cli chat)\b|(?:目前時間|現在時間|對話|工作階段|執行階段)",
    re.IGNORECASE,
)
_PURE_ANSWER_RE = re.compile(
    r"\b(?:translate|translation|calculate|compute)\b|(?:翻譯|翻成|計算|算出)",
    re.IGNORECASE,
)
_PURE_ANSWER_LITERAL_PHRASES = (
    "\u7ffb\u8b6f",
    "\u7ffb\u6210",
    "\u7ffb\u8b6f\u6210",
    "\u7ffb\u6210\u82f1\u6587",
    "\u7ffb\u6210\u4e2d\u6587",
    "\u8a08\u7b97",
)
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
    "workspace_read, workspace_write, media, history_retrieval, verification. If no tool evidence is needed, "
    "use pure_answer and an empty required_tool_groups array. The JSON keys are: task_type, "
    "required_tool_groups, final_answer_required, allow_no_tool_final, reason."
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
        semantic_contract={
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
        response = await provider.chat(
            [
                ChatMessage(role="system", content=_PLANNER_CONTRACT_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=_build_planner_contract_prompt(
                        current_message=current_message,
                        history=history or [],
                        task_intent=task_intent,
                        current_image_files=current_image_files,
                        current_audio_files=current_audio_files,
                        current_video_files=current_video_files,
                        task_context_decision=task_context_decision,
                    ),
                ),
            ],
            model=model,
            **self.llm_config.decoding_kwargs(),
        )
        payload = _parse_json_object(str(getattr(response, "content", "") or ""))
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
        harness_profile: HarnessProfile | None = None,
    ) -> TaskContract:
        return cls.build_deterministic(
            task_intent=task_intent,
            current_message=current_message,
            history=history,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
            task_context_decision=task_context_decision,
            harness_profile=harness_profile,
        )

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
        harness_profile: HarnessProfile | None = None,
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
        no_web_constraint = has_no_web_constraint(text)
        no_workspace_constraint = has_no_workspace_constraint(text)
        no_tool_constraint = has_no_tool_constraint(text)
        chat_no_tool_profile = harness_profile is not None and harness_profile.name == "chat" and no_tool_constraint
        if chat_no_tool_profile:
            task_type = "pure_answer"

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
        if not web_required and not requirements and not no_web_constraint:
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

        workspace_required = not requirements and cls._looks_like_workspace_task(text)
        if not workspace_required and not requirements and not no_workspace_constraint:
            workspace_required = inherited_tool_group == "workspace_read"

        if workspace_required:
            acceptance_criteria.append(_workspace_final_answer_criterion())
            requirements.append(
                EvidenceRequirement(
                    kind="tool_group",
                    tool_group="workspace_read",
                    coverage="any",
                    min_count=1,
                    description="Inspect the relevant workspace files or code context before answering.",
                )
            )
            task_type = "workspace_read"

        history_required = not requirements and cls._looks_like_history_task(text)
        if not history_required and not requirements:
            history_required = inherited_tool_group == "history_retrieval"

        if history_required:
            acceptance_criteria.append(_history_final_answer_criterion())
            requirements.append(
                EvidenceRequirement(
                    kind="tool_group",
                    tool_group="history_retrieval",
                    coverage="any",
                    min_count=1,
                    description="Search prior chat history before answering this recall request.",
                )
            )
            task_type = "history_retrieval"

        if task_intent.expects_code_change and not chat_no_tool_profile:
            requirements.append(
                EvidenceRequirement(
                    kind="file_change",
                    min_count=1,
                    description="Record at least one workspace file change.",
                )
            )
            task_type = "code_change"

        if task_intent.expects_verification and not chat_no_tool_profile:
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

        requirements, acceptance_criteria, task_type, contract_sources, profile_metadata = _apply_harness_profile(
            requirements=requirements,
            acceptance_criteria=acceptance_criteria,
            task_type=task_type,
            harness_profile=harness_profile,
            selected_resources=selected,
        )

        return TaskContract(
            objective=objective,
            task_type=task_type,
            requirements=tuple(requirements),
            acceptance_criteria=tuple(acceptance_criteria),
            selected_resources=tuple(selected),
            final_answer_required=True,
            allow_no_tool_final=not requirements,
            contract_sources=contract_sources,
            harness_profile=profile_metadata,
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
        text = text or ""
        if has_no_web_constraint(text) or _LOCAL_RUNTIME_RE.search(text) or _is_pure_answer_request(text):
            return False
        text_without_code_paths = _CODE_PATH_RE.sub(" ", text)
        return bool(
            _URL_RE.search(text)
            or _WEB_KEYWORD_RE.search(text_without_code_paths)
            or _WEB_TASK_HINT_RE.search(text_without_code_paths)
            or (_WEB_SEARCH_TERM_RE.search(text_without_code_paths) and _WEB_KEYWORD_RE.search(text_without_code_paths))
        )

    @staticmethod
    def _looks_like_workspace_task(text: str) -> bool:
        if has_no_workspace_constraint(text or "") or _is_pure_answer_request(text):
            return False
        return bool(_WORKSPACE_TASK_HINT_RE.search(text or ""))

    @staticmethod
    def _looks_like_history_task(text: str) -> bool:
        return bool(_HISTORY_TASK_HINT_RE.search(text or ""))


def _apply_harness_profile(
    *,
    requirements: list[EvidenceRequirement],
    acceptance_criteria: list[AcceptanceCriterion],
    task_type: str,
    harness_profile: HarnessProfile | None,
    selected_resources: list[ResourceRef],
) -> tuple[list[EvidenceRequirement], list[AcceptanceCriterion], str, tuple[str, ...], dict[str, Any] | None]:
    """Tighten deterministic contract requirements from the selected harness profile."""
    contract_sources = ("deterministic",)
    if harness_profile is None:
        return requirements, acceptance_criteria, task_type, contract_sources, None

    profile_metadata = harness_profile.to_metadata()
    contract_sources = _append_unique(contract_sources, "harness_profile")
    profile_name = harness_profile.name

    if profile_name == "research":
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
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (
                AcceptanceCriterion(
                    kind="source_artifact",
                    min_count=1,
                    description="Produce at least one traceable web source before finalizing the answer.",
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
        task_type = "web_research"
    elif profile_name == "coding":
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
        if harness_profile.task_type == "workspace_change":
            if not _has_requirement(requirements, kind="file_change"):
                requirements.append(
                    EvidenceRequirement(
                        kind="file_change",
                        min_count=1,
                        description="Record at least one workspace file change.",
                    )
                )
            acceptance_criteria = _append_acceptance_criteria(
                acceptance_criteria,
                (_verification_or_gap_criterion(),),
            )
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (_workspace_final_answer_criterion(),),
        )
        task_type = "code_change" if harness_profile.task_type == "workspace_change" else "workspace_read"
    elif profile_name == "media":
        if selected_resources:
            acceptance_criteria = _append_acceptance_criteria(
                acceptance_criteria,
                (_media_artifact_criterion(), _media_final_answer_criterion()),
            )
            task_type = "media_extraction"
    elif profile_name == "ops":
        acceptance_criteria = _append_acceptance_criteria(
            acceptance_criteria,
            (
                _operation_report_criterion(),
                AcceptanceCriterion(
                    kind="substantive_final_answer",
                    min_response_chars=80,
                    description="Report the operation performed, approval or validation status, and any remaining risk.",
                ),
            ),
        )
        task_type = "operations"

    return requirements, acceptance_criteria, task_type, contract_sources, profile_metadata


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


def _task_type_from_intent(task_intent: TaskIntent) -> str:
    if task_intent.expects_code_change:
        return "code_change"
    if task_intent.kind in {"conversation", "question", "command"}:
        return "pure_answer"
    return task_intent.kind or "task"


def _is_pure_answer_request(text: str) -> bool:
    text = text or ""
    lowered = text.lower()
    return bool(_PURE_ANSWER_RE.search(text) or any(phrase in lowered for phrase in _PURE_ANSWER_LITERAL_PHRASES))


def _requested_item_count(objective: str) -> int:
    counts = [int(match) for match in re.findall(r"(?<![A-Za-z0-9_-])(\d{1,3})(?![A-Za-z0-9_-])", str(objective or ""))]
    return max(counts, default=0)


def _append_unique(items: tuple[str, ...], item: str) -> tuple[str, ...]:
    values = [value for value in items if value]
    if item not in values:
        values.append(item)
    return tuple(values)


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
        "conversation state, choose history_retrieval. If no tool evidence is needed, choose pure_answer.\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "task_type": "pure_answer | web_research | workspace_read | workspace_change | media_analysis | history_retrieval | ops | task | analysis",\n'
        '  "required_tool_groups": ["web_research | workspace_read | workspace_write | media | history_retrieval | verification"],\n'
        '  "final_answer_required": true,\n'
        '  "allow_no_tool_final": true,\n'
        '  "reason": "short explanation for trace only"\n'
        "}\n\n"
        f"Input:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _planner_blocked_contract(*, objective: str, reason: str) -> TaskContract:
    return TaskContract(
        objective=objective,
        task_type="pure_answer",
        final_answer_required=True,
        allow_no_tool_final=True,
        contract_sources=("llm_planner",),
        semantic_contract={
            "planner_status": "blocked",
            "reason": reason,
        },
    )


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
    raw_task_type = _allowed_string(payload.get("task_type"), _ALLOWED_PLANNER_TASK_TYPES) or "pure_answer"
    task_type = _PLANNER_TASK_TYPE_ALIASES.get(raw_task_type, raw_task_type)
    tool_groups = _normalize_planner_tool_groups(payload.get("required_tool_groups"))
    inherited_tool_group = getattr(task_context_decision, "inherited_tool_group", "") or ""
    if inherited_tool_group in _ALLOWED_PLANNER_TOOL_GROUPS and inherited_tool_group not in tool_groups:
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
            media_resources = (
                resource_index.by_kind("image")
                + resource_index.by_kind("audio")
                + resource_index.by_kind("video")
            )
            selected.extend(media_resources)
            if media_resources:
                requirements.append(
                    EvidenceRequirement(
                        kind="resource_coverage",
                        tool_group="media",
                        resource_ids=tuple(item.id for item in media_resources),
                        coverage="all",
                        min_count=len(media_resources),
                        description="Inspect each referenced media resource before finalizing the answer.",
                    )
                )
            else:
                requirements.append(_tool_group_requirement("media"))
            acceptance_criteria = _append_acceptance_criteria(
                acceptance_criteria,
                (_media_artifact_criterion(), _media_final_answer_criterion()),
            )
        elif tool_group == "history_retrieval":
            requirements.append(_tool_group_requirement("history_retrieval"))
            acceptance_criteria = _append_acceptance_criteria(acceptance_criteria, (_history_final_answer_criterion(),))
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
    return TaskContract(
        objective=objective,
        task_type=task_type,
        requirements=tuple(requirements),
        acceptance_criteria=tuple(acceptance_criteria),
        selected_resources=tuple(dict.fromkeys(selected)),
        final_answer_required=_coerce_bool(payload.get("final_answer_required", True)),
        allow_no_tool_final=_coerce_bool(payload.get("allow_no_tool_final", not requirements)) and not requirements,
        contract_sources=("llm_planner",),
        semantic_contract=metadata,
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
