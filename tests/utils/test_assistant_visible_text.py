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


def test_sanitize_assistant_visible_text_preserves_code_fence_examples():
    text = "Example:\n```xml\n<system-reminder>hidden</system-reminder>\n```\nVisible answer"

    assert sanitize_assistant_visible_text(text) == text


def test_sanitize_assistant_visible_text_preserves_inline_literal_examples():
    text = "Use `<system-reminder>` to open and `</system-reminder>` to close."

    assert sanitize_assistant_visible_text(text) == text


def test_sanitize_assistant_visible_text_strips_unclosed_system_reminder_block():
    text = "Visible answer\n<system-reminder>hidden mode switch"

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_strips_real_tags_while_preserving_literal_examples():
    text = "<think>hidden</think>Visible text with `<think>` example."

    assert sanitize_assistant_visible_text(text) == "Visible text with `<think>` example."
