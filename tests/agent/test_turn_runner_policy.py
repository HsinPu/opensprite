from opensprite.agent.turn_runner_policy import (
    LLM_NOT_CONFIGURED_LOG_REASON,
    LLM_NOT_CONFIGURED_TURN_REASON,
    MEDIA_ONLY_TURN_REASON,
)


def test_turn_runner_reason_markers_are_stable():
    assert MEDIA_ONLY_TURN_REASON == "media_only"
    assert LLM_NOT_CONFIGURED_TURN_REASON == "llm_not_configured"
    assert LLM_NOT_CONFIGURED_LOG_REASON == "llm-not-configured"
