"""Workflow completion evidence and follow-up helpers."""

from __future__ import annotations

from typing import Any

from ...documents.active_task import DONE_ACTIVE_TASK_STATUS
from ...storage.base import StoredDelegatedTask
from ...subagent_prompts.profiles import REVIEW_PROMPT_TYPES
from ..subagent import (
    STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD,
    STRUCTURED_SUBAGENT_STATUS_FIELD,
    STRUCTURED_SUBAGENT_SUMMARY_FIELD,
    first_structured_review_finding,
    is_clean_structured_subagent_status,
)
from ..task.contract import (
    WORKFLOW_COMPLETION_INTENT_KINDS,
    TaskContract,
    TaskIntent,
    intent_supports_fallback_active_task_update,
)
from ..workflow import (
    WORKFLOW_ERROR_FIELD,
    WORKFLOW_ID_FIELD,
    WORKFLOW_NEXT_STEP_ID_FIELD,
    WORKFLOW_NEXT_STEP_LABEL_FIELD,
    WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD,
    WORKFLOW_REVIEW_ATTEMPTED_FIELD,
    WORKFLOW_REVIEW_FINDING_COUNT_FIELD,
    WORKFLOW_REVIEW_FIRST_FINDING_FIELD,
    WORKFLOW_REVIEW_PASSED_FIELD,
    WORKFLOW_REVIEW_SUMMARY_FIELD,
    WORKFLOW_STATUS_FIELD,
    WORKFLOW_SUMMARY_FIELD,
    WORKFLOW_VERIFICATION_ATTEMPTED_FIELD,
    WORKFLOW_VERIFICATION_PASSED_FIELD,
    is_workflow_cancelled_status,
    is_workflow_completed_status,
    is_workflow_failed_status,
    is_workflow_unsuccessful_status,
)
from .results import (
    COMPLETION_RESULT_REASON_FIELD,
    COMPLETION_RESULT_STATUS_FIELD,
)
from .status import (
    BLOCKED_COMPLETION_STATUS,
    COMPLETE_COMPLETION_STATUS,
    INCOMPLETE_COMPLETION_STATUS,
    NEEDS_REVIEW_COMPLETION_STATUS,
    NEEDS_VERIFICATION_COMPLETION_STATUS,
    is_complete_completion_status,
    needs_verification_completion_status,
)
from .value_utils import coerce_text as _coerce_text
from .workflow_rules import (
    is_research_then_outline_workflow,
    is_review_workflow,
    task_review_evidence_missing_detail,
    task_review_findings_follow_up_detail,
    workflow_clean_review_reason,
    workflow_completed_all_steps_reason,
    workflow_fix_follow_up_fields,
    workflow_review_evidence_missing_detail,
    workflow_review_evidence_missing_reason,
    workflow_review_findings_follow_up_reason,
    workflow_review_follow_up_fields,
    workflow_unsuccessful_reason,
)

WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON = "workflow completed but required verification evidence is still missing"
REVIEW_EVIDENCE_ATTEMPTED_FIELD = "attempted"
REVIEW_EVIDENCE_PASSED_FIELD = "passed"
REVIEW_EVIDENCE_SUMMARY_FIELD = "summary"
REVIEW_EVIDENCE_PROMPT_TYPES_FIELD = "prompt_types"
REVIEW_EVIDENCE_FINDING_COUNT_FIELD = "finding_count"
REVIEW_EVIDENCE_FIRST_FINDING_FIELD = "first_finding"
_REVIEW_PROMPT_TYPES = REVIEW_PROMPT_TYPES


def _workflow_next_step_metadata(workflow: dict[str, Any]) -> dict[str, str]:
    fields = {
        WORKFLOW_NEXT_STEP_ID_FIELD: _coerce_text(workflow.get(WORKFLOW_NEXT_STEP_ID_FIELD)),
        WORKFLOW_NEXT_STEP_LABEL_FIELD: _coerce_text(workflow.get(WORKFLOW_NEXT_STEP_LABEL_FIELD)),
        WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD: _coerce_text(workflow.get(WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD)),
    }
    return fields if any(fields.values()) else {}


def _workflow_review_metadata(workflow: dict[str, Any]) -> dict[str, Any]:
    return {
        WORKFLOW_REVIEW_ATTEMPTED_FIELD: bool(workflow.get(WORKFLOW_REVIEW_ATTEMPTED_FIELD)),
        WORKFLOW_REVIEW_PASSED_FIELD: bool(workflow.get(WORKFLOW_REVIEW_PASSED_FIELD)),
        WORKFLOW_REVIEW_FINDING_COUNT_FIELD: int(workflow.get(WORKFLOW_REVIEW_FINDING_COUNT_FIELD) or 0),
        WORKFLOW_REVIEW_SUMMARY_FIELD: _coerce_text(workflow.get(WORKFLOW_REVIEW_SUMMARY_FIELD)),
    }


def _workflow_verification_metadata(workflow: dict[str, Any]) -> dict[str, bool]:
    return {
        WORKFLOW_VERIFICATION_ATTEMPTED_FIELD: bool(workflow.get(WORKFLOW_VERIFICATION_ATTEMPTED_FIELD)),
        WORKFLOW_VERIFICATION_PASSED_FIELD: bool(workflow.get(WORKFLOW_VERIFICATION_PASSED_FIELD)),
    }


def _latest_workflow_outcome(workflow_outcomes: tuple[Any, ...]) -> dict[str, Any] | None:
    for outcome in reversed(workflow_outcomes):
        if isinstance(outcome, dict) and _coerce_text(outcome.get(WORKFLOW_ID_FIELD)):
            return outcome
    return None


def _workflow_gate_review_result_fields(workflow_gate: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_attempted": bool(
            workflow_gate.get(WORKFLOW_REVIEW_ATTEMPTED_FIELD, review[REVIEW_EVIDENCE_ATTEMPTED_FIELD])
        ),
        "review_passed": bool(
            workflow_gate.get(WORKFLOW_REVIEW_PASSED_FIELD, review[REVIEW_EVIDENCE_PASSED_FIELD])
        ),
        "review_summary": _coerce_text(
            workflow_gate.get(WORKFLOW_REVIEW_SUMMARY_FIELD) or review[REVIEW_EVIDENCE_SUMMARY_FIELD]
        ),
        "review_prompt_types": review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
        "review_finding_count": int(
            workflow_gate.get(WORKFLOW_REVIEW_FINDING_COUNT_FIELD, review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD])
        ),
    }


def _workflow_gate_core_result_fields(
    workflow_gate: dict[str, Any],
    *,
    verification_required: bool,
    review_required: bool,
) -> dict[str, Any]:
    return {
        "status": workflow_gate[COMPLETION_RESULT_STATUS_FIELD],
        "reason": workflow_gate[COMPLETION_RESULT_REASON_FIELD],
        "verification_required": verification_required,
        "review_required": review_required,
    }


def _workflow_gate_follow_up_result_fields(workflow_gate: dict[str, Any]) -> dict[str, str | None]:
    return {
        "follow_up_workflow": _string_or_none(workflow_gate.get(WORKFLOW_ID_FIELD)),
        "follow_up_step_id": _string_or_none(workflow_gate.get(WORKFLOW_NEXT_STEP_ID_FIELD)),
        "follow_up_step_label": _string_or_none(workflow_gate.get(WORKFLOW_NEXT_STEP_LABEL_FIELD)),
        "follow_up_prompt_type": _string_or_none(workflow_gate.get(WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD)),
    }


def _workflow_gate_active_task_result_fields(
    workflow_gate: dict[str, Any],
    *,
    workflow_gate_complete: bool,
    task_intent: TaskIntent,
    task_contract: TaskContract,
) -> dict[str, Any]:
    return {
        "active_task_status": DONE_ACTIVE_TASK_STATUS if workflow_gate_complete else None,
        "active_task_detail": workflow_gate.get("detail") or None,
        "should_update_active_task": workflow_gate_complete
        and intent_supports_fallback_active_task_update(task_intent, task_contract),
    }


def _workflow_gate_verification_result_fields(
    workflow_gate: dict[str, Any],
    *,
    verification_attempted: bool,
    verification_passed: bool,
    verification_follow_up: dict[str, Any],
    needs_verification: bool,
) -> dict[str, Any]:
    return {
        "verification_attempted": bool(
            workflow_gate.get(WORKFLOW_VERIFICATION_ATTEMPTED_FIELD, verification_attempted)
        ),
        "verification_passed": bool(
            workflow_gate.get(WORKFLOW_VERIFICATION_PASSED_FIELD, verification_passed)
        ),
        "verification_action": verification_follow_up["action"] if needs_verification else None,
        "verification_path": verification_follow_up["path"] if needs_verification else None,
        "verification_pytest_args": verification_follow_up["pytest_args"] if needs_verification else (),
    }


def _workflow_gate_result_fields(
    workflow_gate: dict[str, Any],
    *,
    task_intent: TaskIntent,
    task_contract: TaskContract,
    verification_required: bool,
    verification_attempted: bool,
    verification_passed: bool,
    verification_follow_up: dict[str, Any],
    review_required: bool,
    review: dict[str, Any],
) -> dict[str, Any]:
    workflow_gate_status = workflow_gate.get(COMPLETION_RESULT_STATUS_FIELD)
    workflow_gate_complete = is_complete_completion_status(workflow_gate_status)
    workflow_gate_needs_verification = needs_verification_completion_status(workflow_gate_status)
    return {
        **_workflow_gate_core_result_fields(
            workflow_gate,
            verification_required=verification_required,
            review_required=review_required,
        ),
        **_workflow_gate_active_task_result_fields(
            workflow_gate,
            workflow_gate_complete=workflow_gate_complete,
            task_intent=task_intent,
            task_contract=task_contract,
        ),
        **_workflow_gate_follow_up_result_fields(workflow_gate),
        **_workflow_gate_verification_result_fields(
            workflow_gate,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
            verification_follow_up=verification_follow_up,
            needs_verification=workflow_gate_needs_verification,
        ),
        **_workflow_gate_review_result_fields(workflow_gate, review),
    }


def _review_evidence(delegated_tasks: tuple[StoredDelegatedTask, ...]) -> dict[str, Any]:
    prompt_types: list[str] = []
    finding_count = 0
    attempted = False
    clean_review_recorded = False
    problematic_review_recorded = False
    summary = ""
    first_finding = ""
    for task in delegated_tasks:
        prompt_type = str(task.prompt_type or "").strip()
        if prompt_type not in _REVIEW_PROMPT_TYPES:
            continue
        prompt_types.append(prompt_type)
        if not is_workflow_completed_status(task.status):
            continue
        attempted = True
        structured = task.metadata.get("structured_output") if isinstance(task.metadata, dict) else None
        structured_status = str((structured or {}).get(STRUCTURED_SUBAGENT_STATUS_FIELD) or "").strip()
        task_findings = int((structured or {}).get(STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD) or 0)
        finding_count += max(0, task_findings)
        task_summary = str((structured or {}).get(STRUCTURED_SUBAGENT_SUMMARY_FIELD) or task.summary or "").strip()
        if task_summary and not summary:
            summary = task_summary
        if not first_finding:
            first_finding = first_structured_review_finding(structured)
        if is_clean_structured_subagent_status(structured_status) and task_findings == 0:
            clean_review_recorded = True
            continue
        problematic_review_recorded = True
    return {
        REVIEW_EVIDENCE_ATTEMPTED_FIELD: attempted,
        REVIEW_EVIDENCE_PASSED_FIELD: attempted and clean_review_recorded and not problematic_review_recorded and finding_count == 0,
        REVIEW_EVIDENCE_SUMMARY_FIELD: summary,
        REVIEW_EVIDENCE_PROMPT_TYPES_FIELD: tuple(dict.fromkeys(prompt_types)),
        REVIEW_EVIDENCE_FINDING_COUNT_FIELD: finding_count,
        REVIEW_EVIDENCE_FIRST_FINDING_FIELD: first_finding,
    }


def _workflow_gate_outcome(
    *,
    task_intent: TaskIntent,
    workflow_outcomes: tuple[dict[str, Any], ...],
    verification_required: bool,
    verification_attempted: bool,
    verification_passed: bool,
) -> dict[str, Any] | None:
    workflow = _latest_workflow_outcome(workflow_outcomes)
    if workflow is None:
        return None
    workflow_id = _coerce_text(workflow.get(WORKFLOW_ID_FIELD))
    workflow_status = _coerce_text(workflow.get(WORKFLOW_STATUS_FIELD))
    review_metadata = _workflow_review_metadata(workflow)
    review_attempted = review_metadata[WORKFLOW_REVIEW_ATTEMPTED_FIELD]
    review_passed = review_metadata[WORKFLOW_REVIEW_PASSED_FIELD]
    review_finding_count = review_metadata[WORKFLOW_REVIEW_FINDING_COUNT_FIELD]
    verification_metadata = _workflow_verification_metadata(workflow)
    workflow_verification_passed = verification_metadata[WORKFLOW_VERIFICATION_PASSED_FIELD]
    workflow_review_summary = review_metadata[WORKFLOW_REVIEW_SUMMARY_FIELD]
    workflow_review_first_finding = _coerce_text(workflow.get(WORKFLOW_REVIEW_FIRST_FINDING_FIELD))
    workflow_summary = _coerce_text(workflow.get(WORKFLOW_SUMMARY_FIELD))
    metadata = {
        WORKFLOW_ID_FIELD: workflow_id,
        **review_metadata,
        **verification_metadata,
        **_workflow_next_step_metadata(workflow),
    }

    if is_workflow_unsuccessful_status(workflow_status):
        detail = _workflow_follow_up_detail(workflow_id, workflow_status, workflow)
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: _completion_status_for_unsuccessful_workflow(workflow_status),
            COMPLETION_RESULT_REASON_FIELD: workflow_unsuccessful_reason(workflow_id),
            "detail": detail,
        }

    if is_research_then_outline_workflow(workflow_id):
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: COMPLETE_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: workflow_completed_all_steps_reason(workflow_id),
        }

    if is_review_workflow(workflow_id):
        if not review_attempted:
            review_step = workflow_review_follow_up_fields(workflow_id)
            return {
                **metadata,
                COMPLETION_RESULT_STATUS_FIELD: NEEDS_REVIEW_COMPLETION_STATUS,
                COMPLETION_RESULT_REASON_FIELD: workflow_review_evidence_missing_reason(workflow_id),
                "detail": workflow_review_evidence_missing_detail(),
                **review_step,
            }
        if not review_passed or review_finding_count > 0:
            fix_step = workflow_fix_follow_up_fields(workflow_id)
            return {
                **metadata,
                COMPLETION_RESULT_STATUS_FIELD: NEEDS_REVIEW_COMPLETION_STATUS,
                COMPLETION_RESULT_REASON_FIELD: workflow_review_findings_follow_up_reason(workflow_id),
                "detail": workflow_review_first_finding or workflow_review_summary or workflow_summary,
                **fix_step,
            }
        if verification_required and not (verification_passed or workflow_verification_passed):
            return {
                **metadata,
                COMPLETION_RESULT_STATUS_FIELD: NEEDS_VERIFICATION_COMPLETION_STATUS,
                COMPLETION_RESULT_REASON_FIELD: WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON,
                "detail": workflow_summary,
            }
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: COMPLETE_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: workflow_clean_review_reason(workflow_id),
        }

    if verification_required and not (verification_passed or workflow_verification_passed):
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: NEEDS_VERIFICATION_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON,
            "detail": workflow_summary,
        }

    if _coerce_text(task_intent.kind) in WORKFLOW_COMPLETION_INTENT_KINDS:
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: COMPLETE_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: workflow_completed_all_steps_reason(workflow_id),
        }

    return None


def _completion_status_for_unsuccessful_workflow(workflow_status: str | None) -> str:
    if is_workflow_failed_status(workflow_status):
        return BLOCKED_COMPLETION_STATUS
    return INCOMPLETE_COMPLETION_STATUS


def _review_follow_up_detail(review: dict[str, Any]) -> str | None:
    if not review.get("attempted"):
        return task_review_evidence_missing_detail()
    detail = _coerce_text(review.get("first_finding") or review.get("summary"))
    return detail or task_review_findings_follow_up_detail()


def _workflow_follow_up_detail(workflow_id: str, workflow_status: str, workflow: dict[str, Any]) -> str:
    step_label = _coerce_text(workflow.get(WORKFLOW_NEXT_STEP_LABEL_FIELD) or workflow.get(WORKFLOW_NEXT_STEP_ID_FIELD))
    error = _coerce_text(workflow.get(WORKFLOW_ERROR_FIELD))
    summary = _coerce_text(workflow.get(WORKFLOW_SUMMARY_FIELD))
    if is_workflow_cancelled_status(workflow_status):
        if step_label and summary:
            return f"Resume with the {step_label} step in {workflow_id}. {summary}"
        if step_label:
            return f"Resume with the {step_label} step in {workflow_id}."
        if summary:
            return f"Finish the remaining workflow steps for {workflow_id}. {summary}"
        return f"Finish the remaining workflow steps for {workflow_id}."
    if step_label and error:
        return f"Resolve the {step_label} step failure in {workflow_id}: {error}"
    if step_label:
        return f"Resolve the {step_label} step failure in {workflow_id}."
    return error or summary


def _string_or_none(value: Any) -> str | None:
    text = _coerce_text(value)
    return text or None
