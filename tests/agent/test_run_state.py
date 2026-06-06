from opensprite.agent.run_hooks import AgentRunStateService, RunBusyError


def test_run_state_prevents_overlapping_runs_for_same_session():
    service = AgentRunStateService()
    service.start("web:browser-1", "run-1")

    try:
        service.start("web:browser-1", "run-2")
    except RunBusyError as exc:
        assert "run-1" in str(exc)
    else:
        raise AssertionError("RunBusyError was not raised")


def test_run_state_tracks_cancel_request_and_finish():
    service = AgentRunStateService()
    service.start("web:browser-1", "run-1")

    active = service.request_cancel("web:browser-1", "run-1")

    assert active is not None
    assert service.is_cancel_requested("web:browser-1", "run-1") is True
    service.finish("web:browser-1", "run-1")
    assert service.get_active("web:browser-1") is None
