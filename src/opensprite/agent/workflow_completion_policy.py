"""Shared workflow completion and follow-up routing policy."""

from __future__ import annotations

from .workflows import (
    BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID,
    IMPLEMENT_THEN_REVIEW_WORKFLOW_ID,
    RESEARCH_THEN_OUTLINE_WORKFLOW_ID,
    REVIEW_WORKFLOW_IDS,
)


WORKFLOW_FIX_STEPS = {
    IMPLEMENT_THEN_REVIEW_WORKFLOW_ID: {
        "next_step_id": "implement",
        "next_step_label": "Implement",
        "next_step_prompt_type": "implementer",
    },
    BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID: {
        "next_step_id": "bugfix",
        "next_step_label": "Bug fix",
        "next_step_prompt_type": "bug-fixer",
    },
}
WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON = "workflow completed but required verification evidence is still missing"


def is_research_then_outline_workflow(workflow_id: str | None) -> bool:
    return str(workflow_id or "").strip() == RESEARCH_THEN_OUTLINE_WORKFLOW_ID


def is_review_workflow(workflow_id: str | None) -> bool:
    return str(workflow_id or "").strip() in REVIEW_WORKFLOW_IDS


def workflow_review_follow_up_fields(workflow_id: str | None) -> dict[str, str]:
    if is_review_workflow(workflow_id):
        return {
            "next_step_id": "review",
            "next_step_label": "Code review",
            "next_step_prompt_type": "code-reviewer",
        }
    return {}


def workflow_fix_follow_up_fields(workflow_id: str | None) -> dict[str, str]:
    return dict(WORKFLOW_FIX_STEPS.get(str(workflow_id or "").strip(), {}))
