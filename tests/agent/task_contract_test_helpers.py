"""Test-only task contract factory for legacy completion gate fixtures."""

from __future__ import annotations

import re
from typing import Any

from opensprite.agent.task_contract import AcceptanceCriterion, EvidenceRequirement, ResourceIndex, TaskContract
from opensprite.agent.task_resolver import TaskContextDecision, TaskContextResolver
from opensprite.agent.task_resolver import TaskIntent


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_WEB_RE = re.compile(r"\b(?:web|internet|online|reddit|url|link|news|search|sources?|latest|current|recent|stock|release|releases)\b|(?:上網|網路|搜尋|來源|連結|即時|股價|市值|匯率|報價|天氣|新聞)", re.IGNORECASE)
_WORKSPACE_RE = re.compile(r"\b(?:repo|repository|codebase|file|files|function|class|pytest|src/|tests/|apps/)\b|[\w.-]+\.(?:py|js|ts|vue|json|md|toml|yaml|yml)\b|(?:程式|程式碼|檔案|函式|專案|測試|設定)", re.IGNORECASE)
_HISTORY_RE = re.compile(r"\b(?:again|before|earlier|history|previous|last time)\b|(?:之前|先前|剛剛|上次|剛才|前面)", re.IGNORECASE)
_NO_WEB_RE = re.compile(r"\b(?:do not|don't|dont|without|no)\b[^.。]*\b(?:use\s+)?(?:the\s+)?(?:web|internet|online|search|sources?)\b|(?:不要|不用|不需要|別)(?:上網|搜尋|查網路)", re.IGNORECASE)
_NO_WORKSPACE_RE = re.compile(r"\b(?:do not|don't|dont|without|no)\s+(?:read\s+)?(?:files?|workspace|repo)\b|(?:不要|不用|不需要|別)(?:讀檔|看檔|改檔|看程式碼)", re.IGNORECASE)
_PURE_ANSWER_RE = re.compile(r"\b(?:translate|translation|calculate|compute)\b|(?:翻譯|翻成|計算)", re.IGNORECASE)


class TaskContractService:
    """Compatibility builder used only by tests that exercise completion policy."""

    @classmethod
    def build(cls, **kwargs: Any) -> TaskContract:
        return cls.build_deterministic(**kwargs)

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
        harness_profile: Any | None = None,
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
        criteria: list[AcceptanceCriterion] = []
        task_type = "pure_answer" if task_intent.kind in {"conversation", "question", "command"} else (task_intent.kind or "task")
        selected = []
        inherited_tool_group = task_context_decision.inherited_tool_group

        if harness_profile is not None and getattr(harness_profile, "name", "") == "chat":
            task_type = "pure_answer"
        elif resource_index.by_kind("image"):
            selected.extend(resource_index.by_kind("image"))
            requirements.append(_resource_requirement("image_text", selected, "Inspect each referenced image before finalizing the answer."))
            criteria.append(_substantive("Provide a substantive final answer that uses the inspected media results."))
            task_type = "media_extraction"
        elif resource_index.by_kind("audio"):
            selected.extend(resource_index.by_kind("audio"))
            requirements.append(_resource_requirement("audio_text", selected, "Transcribe each referenced audio clip before finalizing the answer."))
            criteria.append(_substantive("Provide a substantive final answer that uses the inspected media results."))
            task_type = "media_extraction"
        elif resource_index.by_kind("video"):
            selected.extend(resource_index.by_kind("video"))
            requirements.append(_resource_requirement("video_understanding", selected, "Analyze each referenced video before finalizing the answer."))
            criteria.append(_substantive("Provide a substantive final answer that uses the inspected media results."))
            task_type = "media_extraction"

        if not requirements and _needs_workspace(text, inherited_tool_group):
            requirements.append(EvidenceRequirement(kind="tool_group", tool_group="workspace_read", min_count=1, description="Inspect the relevant workspace files or code context before answering."))
            criteria.append(_substantive("Answer with relevant workspace context."))
            task_type = "workspace_read"

        if not requirements and _needs_web(text, inherited_tool_group):
            requirements.append(EvidenceRequirement(kind="tool_group", tool_group="web_research", min_count=1, description="Use web research tools before answering this external information request."))
            criteria.extend(
                [
                    AcceptanceCriterion(kind="source_artifact", min_count=1 if _URL_RE.search(text) else 2, description="Produce enough traceable web sources before finalizing the answer."),
                    AcceptanceCriterion(kind="source_detail", min_count=1, description="Fetch or inspect at least one source page before finalizing; search snippets alone are not enough."),
                    _substantive("Answer with details from the gathered web sources."),
                ]
            )
            if _expects_source_reference(text):
                criteria.append(
                    AcceptanceCriterion(kind="source_reference", min_count=1, description="Cite source URLs, domains, or titles in the final answer.")
                )
            task_type = "web_research"

        if not requirements and _needs_history(text, inherited_tool_group):
            requirements.append(EvidenceRequirement(kind="tool_group", tool_group="history_retrieval", min_count=1, description="Search prior chat history before answering this recall request."))
            criteria.append(_substantive("Answer using the relevant prior conversation context."))
            task_type = "history_retrieval"

        if task_intent.expects_code_change and not _chat_profile(harness_profile):
            requirements.append(EvidenceRequirement(kind="file_change", min_count=1, description="Record at least one workspace file change."))
            task_type = "code_change"

        if task_intent.expects_verification and not _chat_profile(harness_profile):
            requirements.append(EvidenceRequirement(kind="verification", tool_group="verification", min_count=1, description="Record verification evidence before finalizing."))

        requested_count = _requested_item_count(objective)
        if requested_count >= 3:
            criteria.append(
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
            acceptance_criteria=tuple(criteria),
            selected_resources=tuple(selected),
            final_answer_required=True,
            allow_no_tool_final=not requirements,
            contract_sources=("test_fixture",),
            harness_profile=harness_profile.to_metadata() if harness_profile is not None and hasattr(harness_profile, "to_metadata") else None,
        )


def _chat_profile(profile: Any | None) -> bool:
    return profile is not None and getattr(profile, "name", "") == "chat"


def _needs_web(text: str, inherited_tool_group: str | None) -> bool:
    if _NO_WEB_RE.search(text or "") or _PURE_ANSWER_RE.search(text or ""):
        return False
    if _needs_workspace(text, None):
        return False
    return inherited_tool_group == "web_research" or bool(_URL_RE.search(text or "") or _WEB_RE.search(text or ""))


def _needs_workspace(text: str, inherited_tool_group: str | None) -> bool:
    if _NO_WORKSPACE_RE.search(text or "") or _PURE_ANSWER_RE.search(text or ""):
        return False
    return inherited_tool_group == "workspace_read" or bool(_WORKSPACE_RE.search(text or ""))


def _needs_history(text: str, inherited_tool_group: str | None) -> bool:
    return inherited_tool_group == "history_retrieval" or bool(_HISTORY_RE.search(text or ""))


def _resource_requirement(tool_group: str, selected: list[Any], description: str) -> EvidenceRequirement:
    return EvidenceRequirement(
        kind="resource_coverage",
        tool_group=tool_group,
        resource_ids=tuple(item.id for item in selected),
        coverage="all",
        min_count=len(selected),
        description=description,
    )


def _substantive(description: str) -> AcceptanceCriterion:
    return AcceptanceCriterion(kind="substantive_final_answer", min_response_chars=80, description=description)


def _requested_item_count(objective: str) -> int:
    counts = [int(match) for match in re.findall(r"(?<![A-Za-z0-9_-])(\d{1,3})(?![A-Za-z0-9_-])", str(objective or ""))]
    return max(counts, default=0)


def _expects_source_reference(text: str) -> bool:
    return bool(re.search(r"(?:reddit|eddit)|(?:股價|市值|匯率|報價|00981t|00980a)", text or "", re.IGNORECASE))
