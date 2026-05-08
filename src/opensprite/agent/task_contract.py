"""Task contracts and evidence requirements for completion checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .resource_index import ResourceIndex, ResourceRef
from .task_intent import TaskIntent
from ..tools.evidence import ToolEvidence


TOOL_GROUPS: dict[str, frozenset[str]] = {
    "image_text": frozenset({"ocr_image", "analyze_image"}),
    "image_understanding": frozenset({"analyze_image"}),
    "audio_text": frozenset({"transcribe_audio"}),
    "video_understanding": frozenset({"analyze_video"}),
    "web_research": frozenset({"web_search", "web_fetch", "browser_navigate", "browser_snapshot"}),
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
    r"|(?:上網|網路|搜尋|查找|新聞|來源|連結)",
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

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "objective": self.objective,
            "task_type": self.task_type,
            "requirements": [item.to_metadata() for item in self.requirements],
            "acceptance_criteria": [item.to_metadata() for item in self.acceptance_criteria],
            "selected_resources": [item.to_metadata() for item in self.selected_resources],
            "final_answer_required": self.final_answer_required,
            "allow_no_tool_final": self.allow_no_tool_final,
        }


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
    ) -> TaskContract:
        objective = str(task_intent.objective or current_message or "").strip()
        text = f"{objective}\n{current_message or ''}"
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

        if image_resources and cls._looks_like_image_task(text, task_intent, current_image_files):
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
        elif audio_resources and cls._looks_like_audio_task(text, current_audio_files):
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
        elif video_resources and cls._looks_like_video_task(text, current_video_files):
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

        if cls._looks_like_web_task(text):
            acceptance_criteria.append(
                AcceptanceCriterion(
                    kind="source_artifact",
                    min_count=1,
                    description="Produce at least one web source artifact before finalizing the answer.",
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
            task_type = "web_research" if task_type == "pure_answer" else task_type

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
