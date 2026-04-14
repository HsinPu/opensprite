from opensprite.utils.assistant_visible_text import (
    sanitize_assistant_visible_text,
    strip_assistant_internal_scaffolding,
)


def test_sanitize_assistant_visible_text_strips_think_blocks():
    text = "<think>secret</think>Visible answer"

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_strips_system_reminder_blocks():
    text = (
        "<system-reminder>\n"
        "Your operational mode has changed from plan to build.\n"
        "You are no longer in read-only mode.\n"
        "You are permitted to make file changes.\n"
        "</system-reminder>\n"
        "Visible answer"
    )

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_strip_assistant_internal_scaffolding_handles_nested_system_reminder_inside_think():
    text = "<think><system-reminder>hidden</system-reminder></think>Visible answer"

    assert strip_assistant_internal_scaffolding(text) == "Visible answer"
