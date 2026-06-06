from opensprite.agent.completion_gate import (
    MISSING_TASK_EVIDENCE_REASON,
    missing_evidence_active_task_detail,
)


def test_missing_evidence_active_task_detail_formats_items():
    detail = missing_evidence_active_task_detail(("Use web research.", "Record verification."))

    assert detail == "- Use web research.\n- Record verification."


def test_missing_evidence_active_task_detail_omits_empty_detail():
    assert missing_evidence_active_task_detail(()) is None


def test_missing_task_evidence_reason_is_stable():
    assert MISSING_TASK_EVIDENCE_REASON == "required task evidence was not produced"
