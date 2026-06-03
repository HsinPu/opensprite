from opensprite.agent.auto_continue_prompt_policy import (
    existing_web_source_section,
    terse_final_answer_follow_up_instruction,
)


def test_existing_web_source_section_omits_empty_context():
    assert existing_web_source_section("", allow_tools=False) == ""


def test_existing_web_source_section_allows_more_research_when_tools_available():
    section = existing_web_source_section("1. Example https://example.com", allow_tools=True)

    assert "Existing gathered web sources" in section
    assert "instead of repeating web research unless they are clearly insufficient" in section
    assert "progress-only promise" not in section


def test_existing_web_source_section_requires_final_answer_when_tools_disabled():
    section = existing_web_source_section("1. Example https://example.com", allow_tools=False)

    assert "Existing gathered web sources" in section
    assert "progress-only promise" in section
    assert "Write the final answer now" in section


def test_terse_final_answer_follow_up_instruction_requires_substantive_answer():
    instruction = terse_final_answer_follow_up_instruction()

    assert "previous final answer was too terse" in instruction
    assert "Do not reply with only 'done'" in instruction
    assert "substantive final answer" in instruction
