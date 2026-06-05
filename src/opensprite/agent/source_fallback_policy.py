"""Policy helpers for source-backed fallback responses."""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from .completion_status import (
    BLOCKED_COMPLETION_STATUS,
    is_incomplete_completion_status,
    needs_review_completion_status,
    normalize_completion_status,
)
from .task_contract import is_tool_group_requirement
from .web_source_policy import (
    is_source_acceptance_criterion_kind,
    is_web_research_task_type,
    is_web_research_tool_group,
)

if TYPE_CHECKING:
    from .completion_gate import CompletionGateResult
    from .execution import ExecutionResult


def source_fallback_allowed(completion_result: CompletionGateResult, execution_result: ExecutionResult) -> bool:
    if not (
        is_incomplete_completion_status(completion_result.status)
        or normalize_completion_status(completion_result.status) == BLOCKED_COMPLETION_STATUS
        or needs_review_completion_status(completion_result.status)
    ):
        return False
    return task_contract_requires_web_sources(execution_result.task_contract)


def task_contract_requires_web_sources(contract: Any) -> bool:
    if contract is None:
        return False
    if is_web_research_task_type(getattr(contract, "task_type", None)):
        return True
    for requirement in getattr(contract, "requirements", ()) or ():
        if is_tool_group_requirement(requirement) and is_web_research_tool_group(getattr(requirement, "tool_group", None)):
            return True
    for criterion in getattr(contract, "acceptance_criteria", ()) or ():
        if is_source_acceptance_criterion_kind(getattr(criterion, "kind", None)):
            return True
    return False


def clean_source_fallback_snippet(snippet: str) -> str:
    cleaned = str(snippet or "")
    cleaned = re.sub(r"\[!\[[^\]]*]\([^)]+\)]\([^)]+\)", "", cleaned)
    cleaned = re.sub(r"!\[[^\]]*]\([^)]+\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)]\((https?://[^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    return " ".join(cleaned.split())
