from opensprite.context.file_builder import FileContextBuilder
from opensprite.context.paths import get_user_overlay_file, sync_templates
from opensprite.documents.user_overlay import UserOverlayStore
from opensprite.documents.user_profile import create_user_profile_store


def test_file_builder_loads_the_user_profile_for_the_active_session(tmp_path):
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
        default_skills_dir=tmp_path / "skills",
    )

    profile_a = create_user_profile_store(app_home, "telegram:user-a")
    profile_b = create_user_profile_store(app_home, "telegram:user-b")
    profile_a.write_managed_block("- Prefers dark mode.")
    profile_b.write_managed_block("- Prefers light mode.")

    prompt_a = builder.build_system_prompt("telegram:user-a")
    prompt_b = builder.build_system_prompt("telegram:user-b")

    assert "- Prefers dark mode." in prompt_a
    assert "- Prefers dark mode." not in prompt_b
    assert "- Prefers light mode." in prompt_b
    assert "- Prefers light mode." not in prompt_a
    assert str(profile_a.user_profile_file.resolve()) in prompt_a
    assert str(profile_b.user_profile_file.resolve()) in prompt_b


def test_file_builder_includes_stable_user_overlay_before_session_profile(tmp_path):
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
        default_skills_dir=tmp_path / "skills",
    )

    overlay_store = UserOverlayStore(app_home=app_home)
    overlay_store.write("web:profile-a", "# Stable Preferences\n- Prefers concise replies.\n")
    profile = create_user_profile_store(app_home, "web:browser-1")
    profile.write_managed_block("- Prefers detailed replies for this session.")
    builder.set_session_overlay_id("web:browser-1", "web:profile-a")

    prompt = builder.build_system_prompt("web:browser-1")

    overlay_path = str(get_user_overlay_file("web:profile-a", app_home=app_home).resolve())
    assert "# Stable User Overlay" in prompt
    assert "- Prefers concise replies." in prompt
    assert "- Prefers detailed replies for this session." in prompt
    assert overlay_path in prompt
    assert prompt.index("- Prefers concise replies.") < prompt.index("- Prefers detailed replies for this session.")


def test_file_builder_build_messages_includes_relevant_user_overlay_context(tmp_path):
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
        default_skills_dir=tmp_path / "skills",
    )
    overlay_store = UserOverlayStore(app_home=app_home)
    overlay_store.write(
        "web:profile-a",
        "# Stable Preferences\n- Prefers concise replies.\n\n# Stable Facts\n- Works mostly on Python backend tasks.\n\n# Response Language\n- Traditional Chinese (Taiwan)\n",
    )
    builder.user_overlay_index.write(
        "web:profile-a",
        {
            "updated_at": "2026-05-04T12:00:00Z",
            "response_language": {"text": "Traditional Chinese (Taiwan)", "confidence": 0.95},
            "preferences": [{"id": "pref:concise", "text": "Prefer concise replies.", "confidence": 0.9, "updated_at": "2026-05-04T12:00:00Z"}],
            "stable_facts": [{"id": "fact:python", "text": "Works mostly on Python backend tasks.", "confidence": 0.85, "updated_at": "2026-05-04T12:00:00Z"}],
        },
    )
    builder.set_session_overlay_id("web:browser-1", "web:profile-a")

    messages = builder.build_messages([], "Help me refactor this Python backend service.", session_id="web:browser-1")
    relevant_overlay = [message for message in messages if message["role"] == "system" and "# Relevant Stable User Overlay" in str(message["content"])]

    assert len(relevant_overlay) == 1
    assert "Works mostly on Python backend tasks." in relevant_overlay[0]["content"]
    assert "Traditional Chinese (Taiwan)" in relevant_overlay[0]["content"]
