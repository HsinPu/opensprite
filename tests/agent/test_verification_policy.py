from opensprite.agent.verification_policy import (
    REQUIRED_VERIFICATION_FAILED_REASON,
    REQUIRED_VERIFICATION_NOT_RECORDED_REASON,
    VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON,
    required_verification_completion_reason,
)


def test_required_verification_failed_reason_is_stable():
    assert REQUIRED_VERIFICATION_FAILED_REASON == "required verification did not pass"


def test_required_verification_not_recorded_reason_is_stable():
    assert REQUIRED_VERIFICATION_NOT_RECORDED_REASON == "required verification was not recorded"


def test_required_verification_completion_reason_reflects_attempt_status():
    assert required_verification_completion_reason(verification_attempted=True) == REQUIRED_VERIFICATION_FAILED_REASON
    assert (
        required_verification_completion_reason(verification_attempted=False)
        == REQUIRED_VERIFICATION_NOT_RECORDED_REASON
    )


def test_verification_outcome_or_gap_missing_reason_is_stable():
    assert VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON == "verification outcome or gap was not reported"
