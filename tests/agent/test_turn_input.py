from opensprite.agent.turn_input import (
    metadata_is_cli_via_web,
    metadata_requests_direct_verification,
    metadata_requests_follow_up_resume,
)


def test_turn_metadata_helpers_normalize_marker_values():
    assert metadata_is_cli_via_web({"source": " cli_via_web "}) is True
    assert metadata_is_cli_via_web({"source": "browser"}) is False
    assert metadata_requests_follow_up_resume({"quick_action": " resume_follow_up "}) is True
    assert metadata_requests_follow_up_resume({"quick_action": "run_verification"}) is False
    assert metadata_requests_direct_verification({"quick_action": " run_verification "}) is True
    assert metadata_requests_direct_verification({"quick_action": "resume_follow_up"}) is False
