from opensprite.agent.curator_policy import CURATOR_NO_RUNNING_EVENT_LOOP_REASON


def test_curator_reason_markers_are_stable():
    assert CURATOR_NO_RUNNING_EVENT_LOOP_REASON == "no-running-event-loop"
