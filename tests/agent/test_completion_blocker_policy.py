from opensprite.agent.completion_gate import (
    COMPLETION_GATE_DID_NOT_PASS_REASON,
    CompletionBlockerMessages,
    CompletionGateResult,
    completion_blocker_response,
)


MESSAGES = CompletionBlockerMessages(
    intro="INTRO",
    reason_prefix="REASON: ",
    detail_header="DETAIL",
    missing_evidence_header="MISSING",
    stop_notice="STOP",
)


def test_completion_blocker_default_reason_is_centralized():
    assert COMPLETION_GATE_DID_NOT_PASS_REASON == "completion gate did not pass"

    response = completion_blocker_response(
        CompletionGateResult(status="", reason=""),
        MESSAGES,
    )

    assert f"REASON: {COMPLETION_GATE_DID_NOT_PASS_REASON}" in response
