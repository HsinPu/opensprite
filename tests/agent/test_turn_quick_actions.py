from opensprite.agent.turn_quick_actions import (
    metadata_requests_direct_verification,
    metadata_requests_follow_up_resume,
)


def test_turn_quick_action_helpers_normalize_metadata_values():
    assert metadata_requests_follow_up_resume({"quick_action": " resume_follow_up "}) is True
    assert metadata_requests_follow_up_resume({"quick_action": "run_verification"}) is False
    assert metadata_requests_direct_verification({"quick_action": " run_verification "}) is True
    assert metadata_requests_direct_verification({"quick_action": "resume_follow_up"}) is False
