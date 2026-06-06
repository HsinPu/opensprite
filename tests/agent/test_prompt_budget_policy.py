from opensprite.agent.execution import (
    PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON,
    PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON,
    prompt_trim_base_exceeds_budget_reason,
    prompt_trim_first_message_exceeds_budget_reason,
)


def test_prompt_budget_trim_reasons_are_stable():
    assert PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON == "base-exceeds-budget"
    assert PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON == "first-message-exceeds-budget"
    assert prompt_trim_base_exceeds_budget_reason() == PROMPT_TRIM_BASE_EXCEEDS_BUDGET_REASON
    assert (
        prompt_trim_first_message_exceeds_budget_reason()
        == PROMPT_TRIM_FIRST_MESSAGE_EXCEEDS_BUDGET_REASON
    )
