from opensprite.documents.user_overlay import USER_OVERLAY_TEMPLATE, UserOverlayIndexStore, UserOverlayStore


def test_user_overlay_store_bootstraps_and_reads_overlay(tmp_path):
    store = UserOverlayStore(app_home=tmp_path / "home")

    initial = store.ensure_exists("web:profile-a")

    assert "# Stable Preferences" in initial
    assert "# Stable Facts" in initial
    assert "# Response Language" in initial
    assert initial == store.read("web:profile-a")


def test_user_overlay_index_store_roundtrips_payload(tmp_path):
    store = UserOverlayIndexStore(app_home=tmp_path / "home")

    store.write(
        "web:profile-a",
        {
            "updated_at": "2026-05-04T12:00:00Z",
            "response_language": {"text": "Traditional Chinese (Taiwan)", "confidence": 0.95},
            "preferences": [{"id": "pref_1", "text": "Prefer concise replies."}],
            "stable_facts": [{"id": "fact_1", "text": "Works mainly on Python backend."}],
        },
    )

    payload = store.read("web:profile-a")

    assert payload["schema_version"] == 1
    assert payload["overlay_id"] == "web:profile-a"
    assert payload["response_language"]["text"] == "Traditional Chinese (Taiwan)"
    assert payload["preferences"][0]["id"] == "pref_1"
    assert payload["stable_facts"][0]["id"] == "fact_1"


def test_user_overlay_template_stays_human_readable():
    assert USER_OVERLAY_TEMPLATE.startswith("# Stable Preferences")
