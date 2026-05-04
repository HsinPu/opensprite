from opensprite.documents.user_overlay_identity import resolve_user_overlay_id


def test_resolve_user_overlay_id_prefers_web_overlay_profile_metadata():
    overlay_id = resolve_user_overlay_id(
        channel="web",
        sender_id=None,
        metadata={"overlay_profile_id": "profile-abc123"},
    )

    assert overlay_id == "web:profile-abc123"


def test_resolve_user_overlay_id_uses_sender_id_for_non_web_channels():
    overlay_id = resolve_user_overlay_id(
        channel="telegram",
        sender_id="user-42",
        metadata={},
    )

    assert overlay_id == "telegram:user-42"


def test_resolve_user_overlay_id_returns_none_without_stable_identity():
    overlay_id = resolve_user_overlay_id(
        channel="web",
        sender_id=None,
        metadata={},
    )

    assert overlay_id is None
