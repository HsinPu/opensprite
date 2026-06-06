from opensprite.agent.task_context_policy import (
    TASK_CONTEXT_RESOLUTION_PURPOSE,
    TASK_OBJECTIVE_RESOLUTION_PURPOSE,
    llm_failed_reason,
    llm_low_confidence_reason,
    llm_unavailable_reason,
)


def test_llm_resolution_policy_formats_shared_fallback_reasons():
    assert llm_unavailable_reason(TASK_CONTEXT_RESOLUTION_PURPOSE) == "llm unavailable; task context was not inferred"
    assert llm_failed_reason(TASK_OBJECTIVE_RESOLUTION_PURPOSE) == "llm failed; objective was not enriched"
    assert llm_low_confidence_reason(0.456, TASK_CONTEXT_RESOLUTION_PURPOSE) == (
        "llm confidence too low (0.46); task context was not inferred"
    )
