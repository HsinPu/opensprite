from opensprite.context.file_builder import FileContextBuilder
from opensprite.context.paths import sync_templates
from opensprite.documents.user_profile import create_user_profile_store
from opensprite.documents.user_overlay import (
    USER_OVERLAY_TEMPLATE,
    UserOverlayIndexStore,
    UserOverlayPromotionService,
    UserOverlayRetrievalPlanner,
    UserOverlayStore,
)


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


def test_user_overlay_store_blocks_unsafe_content(tmp_path):
    store = UserOverlayStore(app_home=tmp_path / "home")

    try:
        store.write("web:profile-a", "# Stable Preferences\n- cat ~/.env to recall credentials")
    except ValueError as exc:
        assert "Blocked unsafe durable memory write" in str(exc)
    else:
        raise AssertionError("unsafe overlay content was not blocked")


def test_user_overlay_promotion_service_merges_profile_and_memory(tmp_path):
    overlay_store = UserOverlayStore(app_home=tmp_path / "home")
    index_store = UserOverlayIndexStore(app_home=tmp_path / "home")
    service = UserOverlayPromotionService(overlay_store=overlay_store, index_store=index_store)

    result = service.update_from_session_documents(
        "web:profile-a",
        profile_block="- Prefers concise replies.\n- Works mostly in Python.",
        response_language_block="- Traditional Chinese (Taiwan)",
        memory_text="# User Preferences\n- Prefers concise replies.\n\n# Important Facts\n- Uses FastAPI for backend work.\n",
        source_session_id="web:browser-1",
        source_run_id="run_1",
    )

    overlay_text = overlay_store.read("web:profile-a")
    index_payload = index_store.read("web:profile-a")

    assert result["changed"] is True
    assert "- Prefers concise replies." in overlay_text
    assert "- Uses FastAPI for backend work." in overlay_text
    assert "- Traditional Chinese (Taiwan)" in overlay_text
    assert index_payload["response_language"]["text"] == "Traditional Chinese (Taiwan)"
    assert index_payload["preferences"][0]["text"] == "Prefers concise replies."
    assert index_payload["stable_facts"][0]["text"] == "Uses FastAPI for backend work."


def test_user_overlay_promotion_preserves_existing_preferences(tmp_path):
    overlay_store = UserOverlayStore(app_home=tmp_path / "home")
    index_store = UserOverlayIndexStore(app_home=tmp_path / "home")
    service = UserOverlayPromotionService(overlay_store=overlay_store, index_store=index_store)
    overlay_store.write(
        "web:profile-a",
        "# Stable Preferences\n- Prefers concise replies.\n\n# Stable Facts\n- Maintains OpenSprite.\n\n# Response Language\n- Traditional Chinese (Taiwan)\n",
    )

    result = service.update_from_session_documents(
        "web:profile-a",
        profile_block=(
            "### Communication Preferences\n"
            "- Prefers minimal diffs.\n\n"
            "### Work Context\n"
            "- No learned work context yet.\n\n"
            "### Stable Constraints\n"
            "- No learned stable constraints yet."
        ),
        response_language_block="- not set",
        memory_text="# Important Facts\n- Uses FastAPI for backend work.\n",
        source_session_id="web:browser-1",
    )

    overlay_text = overlay_store.read("web:profile-a")

    assert result["changed"] is True
    assert "- Prefers concise replies." in overlay_text
    assert "- Prefers minimal diffs." in overlay_text
    assert "- Maintains OpenSprite." in overlay_text
    assert "- Uses FastAPI for backend work." in overlay_text
    assert "No learned work context yet." not in overlay_text
    assert "- Traditional Chinese (Taiwan)" in overlay_text


def test_second_session_can_read_promoted_overlay(tmp_path):
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)
    overlay_store = UserOverlayStore(app_home=app_home)
    index_store = UserOverlayIndexStore(app_home=app_home)
    service = UserOverlayPromotionService(overlay_store=overlay_store, index_store=index_store)
    service.update_from_session_documents(
        "web:profile-a",
        profile_block="- Prefers concise replies.",
        response_language_block="- Traditional Chinese (Taiwan)",
        memory_text="# Important Facts\n- Maintains OpenSprite.\n",
        source_session_id="web:browser-1",
    )

    builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
        default_skills_dir=tmp_path / "skills",
    )
    profile = create_user_profile_store(app_home, "web:browser-2")
    profile.write_managed_block("- Session-local note only.")
    builder.set_session_overlay_id("web:browser-2", "web:profile-a")

    prompt = builder.build_system_prompt("web:browser-2")

    assert "- Prefers concise replies." in prompt
    assert "- Maintains OpenSprite." in prompt
    assert "- Session-local note only." in prompt


def test_user_overlay_retrieval_planner_selects_relevant_items(tmp_path):
    index_store = UserOverlayIndexStore(app_home=tmp_path / "home")
    index_store.write(
        "web:profile-a",
        {
            "updated_at": "2026-05-04T12:00:00Z",
            "response_language": {"text": "Traditional Chinese (Taiwan)", "confidence": 0.95},
            "preferences": [
                {"id": "pref:concise", "text": "Prefer concise replies.", "confidence": 0.9, "updated_at": "2026-05-04T12:00:00Z"},
            ],
            "stable_facts": [
                {"id": "fact:python", "text": "Works mostly on Python backend tasks.", "confidence": 0.85, "updated_at": "2026-05-04T12:00:00Z"},
                {"id": "fact:frontend", "text": "Maintains frontend design systems.", "confidence": 0.7, "updated_at": "2026-05-04T12:00:00Z"},
            ],
        },
    )
    planner = UserOverlayRetrievalPlanner(index_store=index_store)

    context = planner.build_context("web:profile-a", "Help me with this Python backend refactor.")

    assert "# Relevant Stable User Overlay" in context
    assert "Traditional Chinese (Taiwan)" in context
    assert "Works mostly on Python backend tasks." in context
    assert "Maintains frontend design systems." not in context
