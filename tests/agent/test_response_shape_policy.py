from opensprite.agent.quality_gate import (
    ITEMIZED_OUTPUT_MISSING_REASON,
    TERSE_FINAL_ANSWER_REASON,
    itemized_output_follow_up_instruction,
    normalized_response_text,
    response_has_minimum_text_length,
    response_item_count,
)


def test_itemized_output_missing_reason_is_stable():
    assert ITEMIZED_OUTPUT_MISSING_REASON == "assistant did not provide the requested itemized result"


def test_terse_final_answer_reason_is_stable():
    assert TERSE_FINAL_ANSWER_REASON == "assistant final answer was too terse for the task"


def test_response_item_count_counts_bullets_numbers_and_table_rows():
    assert response_item_count("- one\n* two\n3. three\n| four |") == 4


def test_response_item_count_ignores_plain_paragraphs():
    assert response_item_count("one\ntwo\nthree") == 0


def test_normalized_response_text_collapses_whitespace():
    assert normalized_response_text("  hello\n\nworld\t ") == "hello world"


def test_response_has_minimum_text_length_uses_normalized_text():
    assert response_has_minimum_text_length("hello   world", 11)
    assert not response_has_minimum_text_length("hello", 6)


def test_itemized_output_follow_up_instruction_requires_items():
    instruction = itemized_output_follow_up_instruction()

    assert "requested itemized result" in instruction
    assert "list/table entries" in instruction
