"""Ranking policy for source-backed LLM finalization."""

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


OBJECTIVE_KEYWORD_RE = re.compile(r"[a-z0-9.:-]{3,}")
OBJECTIVE_CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
OBJECTIVE_BRAND_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b")
OBJECTIVE_KEYWORD_STOP_WORDS = frozenset(
    {
        "please",
        "current",
        "latest",
        "\u5e6b\u6211",
        "\u76ee\u524d",
        "\u6700\u65b0",
        "\u8acb\u5217\u51fa",
        "\u4f86\u6e90\u7db2\u5740",
    }
)


def source_finalization_allowed(completion_result: CompletionGateResult, execution_result: ExecutionResult) -> bool:
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


def rank_web_sources_for_objective(sources: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    if not objective:
        return sources
    return sorted(
        sources,
        key=lambda source: web_source_relevance_score(source, objective),
        reverse=True,
    )


def web_source_relevance_score(source: dict[str, Any], objective: str) -> int:
    keywords = _objective_keywords(objective)
    if not keywords:
        return 0
    score = 0
    domain = str(source.get("domain") or "").lower()
    if not domain:
        url = str(source.get("url") or "").lower()
        domain = re.sub(r"^https?://", "", url).split("/", 1)[0]
    domain_label = _domain_brand_label(domain)
    if domain_label and domain_label in _objective_brand_tokens(objective):
        score += 10
    haystack = " ".join(
        str(source.get(key) or "")
        for key in ("title", "url", "snippet", "content", "domain")
    ).lower()
    score += sum(1 for keyword in keywords if keyword in haystack)
    return score


def _objective_keywords(objective: str) -> set[str]:
    text = str(objective or "").lower()
    keywords: set[str] = set()
    keywords.update(OBJECTIVE_KEYWORD_RE.findall(text))
    for cjk_text in OBJECTIVE_CJK_SEQUENCE_RE.findall(text):
        keywords.add(cjk_text)
        for size in (2, 3, 4):
            for index in range(0, max(len(cjk_text) - size + 1, 0)):
                keywords.add(cjk_text[index : index + size])
    return {keyword for keyword in keywords if keyword not in OBJECTIVE_KEYWORD_STOP_WORDS}


def _objective_brand_tokens(objective: str) -> set[str]:
    return {
        token.lower()
        for token in OBJECTIVE_BRAND_TOKEN_RE.findall(str(objective or ""))
    }


def _domain_brand_label(domain: str) -> str:
    labels = str(domain or "").lower().removeprefix("www.").split(".")
    labels = [label for label in labels if label]
    if len(labels) < 2:
        return ""
    return labels[-2].replace("-", "")
