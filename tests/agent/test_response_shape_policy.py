from opensprite.agent.response_shape_policy import (
    normalized_response_text,
    response_has_minimum_text_length,
    response_item_count,
)


def test_response_item_count_counts_bullets_numbers_and_table_rows():
    assert response_item_count("- one\n* two\n3. three\n| four |") == 4


def test_response_item_count_ignores_plain_paragraphs():
    assert response_item_count("one\ntwo\nthree") == 0


def test_normalized_response_text_collapses_whitespace():
    assert normalized_response_text("  hello\n\nworld\t ") == "hello world"


def test_response_has_minimum_text_length_uses_normalized_text():
    assert response_has_minimum_text_length("hello   world", 11)
    assert not response_has_minimum_text_length("hello", 6)
