"""Runtime task-contract guidance used when building LLM prompts."""

from __future__ import annotations

from typing import Any

from ..task.capabilities import HISTORY_RETRIEVAL_TASK_TYPE
from ..task.contract import (
    TaskContextDecision,
    TaskContract,
    is_itemized_output_criterion,
    is_media_artifact_criterion,
    is_operation_report_criterion,
    is_source_artifact_criterion,
    is_source_detail_criterion,
    is_source_reference_criterion,
    is_substantive_final_answer_criterion,
    is_verification_or_gap_criterion,
    is_workspace_location_criterion,
)


def structured_retrieval_decision(task_context_decision: TaskContextDecision | None) -> bool | None:
    if task_context_decision is None:
        return None
    inherited_task_type = str(task_context_decision.inherited_task_type or "").strip()
    return inherited_task_type == HISTORY_RETRIEVAL_TASK_TYPE


def build_task_contract_guidance(contract: TaskContract) -> str:
    if should_answer_contract_without_tools(contract):
        return "\n".join([
            "## Runtime Task Contract",
            f"- Task type: {contract.task_type}",
            "- No tool evidence is required for this turn. Answer directly from general knowledge.",
            "- Do not call tools just to prepare a generic answer.",
        ])
    if not (contract.requirements or contract.acceptance_criteria or contract.selected_resources):
        return ""
    lines = [
        "## Runtime Task Contract",
        "Satisfy these runtime completion requirements before giving the final answer.",
        f"- Task type: {contract.task_type}",
    ]
    if contract.selected_resources:
        lines.append("- Required resources:")
        for resource in contract.selected_resources[:12]:
            label = f"{resource.id} ({resource.kind}, {resource.source})"
            if resource.path:
                label += f" path={resource.path}"
            lines.append(f"  - {label}")
        if len(contract.selected_resources) > 12:
            lines.append(f"  - ... {len(contract.selected_resources) - 12} more resource(s)")
    if contract.requirements:
        lines.append("- Required evidence:")
        for requirement in contract.requirements:
            detail = requirement.description or requirement.kind
            qualifiers = []
            if requirement.tools:
                qualifiers.append(f"tools={', '.join(requirement.tools)}")
            if requirement.coverage:
                qualifiers.append(f"coverage={requirement.coverage}")
            qualifiers.append(f"min_count={requirement.min_count}")
            lines.append(f"  - {detail} ({', '.join(qualifiers)})")
    if contract.acceptance_criteria:
        lines.append("- Final answer acceptance criteria:")
        for criterion in contract.acceptance_criteria:
            lines.append(f"  - {format_acceptance_criterion(criterion)}")
    lines.extend([
        "- If a requirement cannot be satisfied, state the blocker clearly instead of claiming completion.",
        "- Do not answer with only an acknowledgement, plan, or promise of future work when tool evidence or artifacts are required.",
    ])
    return "\n".join(lines)


def should_answer_contract_without_tools(contract: TaskContract) -> bool:
    return (
        bool(contract.allow_no_tool_final)
        and not contract.requirements
        and not contract.acceptance_criteria
        and not contract.selected_resources
    )


def format_acceptance_criterion(criterion: Any) -> str:
    if is_itemized_output_criterion(criterion):
        return f"Provide at least {max(1, int(criterion.min_count or 1))} itemized result entries; do not answer with only a plan or acknowledgement."
    if is_substantive_final_answer_criterion(criterion):
        min_chars = max(1, int(getattr(criterion, "min_response_chars", 0) or 1))
        return f"Write a substantive final answer using the inspected media/tool results (minimum {min_chars} visible characters)."
    if is_source_artifact_criterion(criterion):
        return f"Produce at least {max(1, int(criterion.min_count or 1))} traceable source(s) from web/source tools before finalizing."
    if is_source_detail_criterion(criterion):
        return "Fetch or inspect at least one source page before finalizing; search result snippets alone are not sufficient."
    if is_source_reference_criterion(criterion):
        return "Reference at least one gathered source by URL, domain, or title in the final answer."
    if is_workspace_location_criterion(criterion):
        return (
            "Identify the relevant workspace file path, symbol, or configuration location in the final answer, "
            "using only names and locations shown by workspace tool output; verify uncertain symbol names before citing them."
        )
    if is_media_artifact_criterion(criterion):
        return "Produce the required media artifact before finalizing."
    if is_verification_or_gap_criterion(criterion):
        return "After code changes, run focused verification when possible; if not possible, state the verification gap explicitly."
    if is_operation_report_criterion(criterion):
        return "Report validation, rollback, blocker, or residual risk for the operation."
    return criterion.description or criterion.kind
