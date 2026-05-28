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


def test_sanitize_assistant_visible_text_strips_multilingual_system_reminder_blocks():
    text = "<system-reminder>你的操作模式已從計畫切換成建置。</system-reminder>可見回覆"

    assert sanitize_assistant_visible_text(text) == "可見回覆"


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


def test_sanitize_assistant_visible_text_strips_minimax_tool_call_blocks():
    text = (
        "<minimax:tool_call>\n"
        "<invoke name=\"web_fetch\">\n"
        "<parameter name=\"url\">https://example.com</parameter>\n"
        "</invoke>\n"
        "</minimax:tool_call>\n"
        "Visible answer"
    )

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_preserves_minimax_tool_call_examples_in_code():
    text = "Example:\n```xml\n<minimax:tool_call>literal</minimax:tool_call>\n```\nVisible answer"

    assert sanitize_assistant_visible_text(text) == text


def test_sanitize_assistant_visible_text_strips_generic_tool_call_blocks():
    text = (
        '<tool_call name="web_research">\n'
        '{"query": "台積電 今日股價"}\n'
        "</tool_call>\n"
        "Visible answer"
    )

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_preserves_generic_tool_call_examples_in_code():
    text = 'Example:\n```xml\n<tool_call name="web_research">literal</tool_call>\n```\nVisible answer'

    assert sanitize_assistant_visible_text(text) == text


def test_sanitize_assistant_visible_text_strips_direct_tool_tag_blocks():
    text = (
        "<search_history>\n"
        "<query>AMD stock price 2026-05-28 web research</query>\n"
        "<limit>10</limit>\n"
        "</search_history>\n"
        "Visible answer"
    )

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_preserves_direct_tool_tag_examples_in_code():
    text = "Example:\n```xml\n<search_history>literal</search_history>\n```\nVisible answer"

    assert sanitize_assistant_visible_text(text) == text


def test_sanitize_assistant_visible_text_strips_dsml_tool_call_blocks():
    text = (
        "<｜｜DSML｜｜tool_calls>\n"
        '<｜｜DSML｜｜invoke name="web_fetch">\n'
        '<｜｜DSML｜｜parameter name="url" string="true">https://example.com</｜｜DSML｜｜parameter>\n'
        "</｜｜DSML｜｜invoke>\n"
        "<｜｜DSML｜｜/tool_calls>\n"
        "Visible answer"
    )

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_strips_single_bar_dsml_tool_call_blocks():
    text = (
        "<｜DSML｜tool_calls>\n"
        '<｜DSML｜invoke name="web_fetch">\n'
        '<｜DSML｜parameter name="url" string="true">https://example.com</｜DSML｜parameter>\n'
        "</｜DSML｜invoke>\n"
        "<｜DSML｜/tool_calls>\n"
        "Visible answer"
    )

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_preserves_dsml_tool_call_examples_in_code():
    text = "Example:\n```xml\n<｜｜DSML｜｜tool_calls>literal<｜｜DSML｜｜/tool_calls>\n```\nVisible answer"

    assert sanitize_assistant_visible_text(text) == text


def test_sanitize_assistant_visible_text_strips_bracket_tool_call_blocks():
    text = (
        "[TOOL_CALL]\n"
        "{tool => \"web_fetch\", args => { --url \"https://example.com\" }}\n"
        "[/TOOL_CALL]\n"
        "Visible answer"
    )

    assert sanitize_assistant_visible_text(text) == "Visible answer"


def test_sanitize_assistant_visible_text_preserves_bracket_tool_call_examples_in_code():
    text = "Example:\n```text\n[TOOL_CALL]literal[/TOOL_CALL]\n```\nVisible answer"

    assert sanitize_assistant_visible_text(text) == text
