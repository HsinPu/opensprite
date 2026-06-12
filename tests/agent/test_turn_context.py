from contextvars import ContextVar

from opensprite.agent.turn_context import TurnContextService


def _make_context_service() -> TurnContextService:
    return TurnContextService(
        current_session_id=ContextVar("test_current_session_id", default=None),
        current_channel=ContextVar("test_current_channel", default=None),
        current_external_chat_id=ContextVar("test_current_external_chat_id", default=None),
        current_images=ContextVar("test_current_images", default=None),
        current_audios=ContextVar("test_current_audios", default=None),
        current_videos=ContextVar("test_current_videos", default=None),
        current_outbound_media=ContextVar("test_current_outbound_media", default=None),
        current_run_id=ContextVar("test_current_run_id", default=None),
        current_work_progress=ContextVar("test_current_work_progress", default=None),
    )


def test_turn_context_activate_sets_values_and_resets_after_exit():
    service = _make_context_service()

    assert service.current_session_id() is None
    assert service.queue_outbound_media("image", "outside-context") is not None

    with service.activate(
        session_id="web:session-1",
        channel="web",
        external_chat_id="browser-1",
        images=["images/inbound.png"],
        audios=["audios/inbound.ogg"],
        videos=["videos/inbound.mp4"],
        run_id="run-1",
    ):
        assert service.current_session_id() == "web:session-1"
        assert service.current_channel() == "web"
        assert service.current_external_chat_id() == "browser-1"
        assert service.current_images() == ["images/inbound.png"]
        assert service.current_audios() == ["audios/inbound.ogg"]
        assert service.current_videos() == ["videos/inbound.mp4"]
        assert service.current_run_id() == "run-1"

        assert service.queue_outbound_media("image", "images/out.png") is None
        assert service.queue_outbound_media("voice", "voices/out.wav") is None
        assert service.queued_outbound_media() == {
            "images": ["images/out.png"],
            "voices": ["voices/out.wav"],
            "audios": [],
            "videos": [],
        }

        service.note_file_change(" src/app.py ")
        service.note_file_change("src/app.py")
        service.note_file_change("")
        assert service.snapshot_work_progress() == {
            "file_change_count": 3,
            "touched_paths": ("src/app.py",),
        }

        service.reset_work_progress()
        assert service.snapshot_work_progress() == {
            "file_change_count": 0,
            "touched_paths": (),
        }

    assert service.current_session_id() is None
    assert service.current_channel() is None
    assert service.current_external_chat_id() is None
    assert service.current_images() is None
    assert service.current_audios() is None
    assert service.current_videos() is None
    assert service.current_run_id() is None
