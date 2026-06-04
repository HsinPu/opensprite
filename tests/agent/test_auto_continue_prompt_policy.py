from opensprite.agent.auto_continue_prompt_policy import (
    existing_web_source_section,
    internal_only_response_follow_up_instruction,
    insufficient_source_detail_follow_up_instruction,
    missing_source_citation_follow_up_instruction,
    missing_tool_evidence_follow_up_instruction,
    source_traceability_follow_up_instruction,
    terse_final_answer_follow_up_instruction,
    web_research_coverage_gap_follow_up_instruction,
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
    assert "Do not reply with only a short acknowledgement" in instruction
    assert "substantive final answer" in instruction


def test_missing_tool_evidence_follow_up_instruction_requests_tools():
    instruction = missing_tool_evidence_follow_up_instruction()

    assert "required tool evidence is missing" in instruction
    assert "Call the appropriate tools" in instruction


def test_source_traceability_follow_up_instruction_includes_gap_detail():
    instruction = source_traceability_follow_up_instruction("- Missing traceable source metadata")

    assert "source artifact without traceable source metadata" in instruction
    assert "web_research" in instruction
    assert "- Missing traceable source metadata" in instruction


def test_web_research_coverage_gap_follow_up_instruction_includes_gap_detail():
    instruction = web_research_coverage_gap_follow_up_instruction("- Target fetch count not met")

    assert "`web_research` reported coverage gaps" in instruction
    assert "focused `queries`" in instruction
    assert "- Target fetch count not met" in instruction


def test_insufficient_source_detail_follow_up_instruction_requires_fetch_detail():
    instruction = insufficient_source_detail_follow_up_instruction()

    assert "did not inspect enough source material" in instruction
    assert "web_fetch" in instruction
    assert "Do not finalize from search snippets alone" in instruction


def test_missing_source_citation_follow_up_instruction_uses_existing_sources():
    instruction = missing_source_citation_follow_up_instruction()

    assert "gathered sources are available" in instruction
    assert "Do not rerun tools unless the sources are insufficient" in instruction
    assert "reference at least one source" in instruction


def test_internal_only_response_follow_up_instruction_respects_tool_access():
    with_tools = internal_only_response_follow_up_instruction(allow_tools=True)
    without_tools = internal_only_response_follow_up_instruction(allow_tools=False)

    assert "only contained internal control text" in with_tools
    assert "Do not repeat internal tags" in with_tools
    assert "Do not call tools again" not in with_tools
    assert "Do not call tools again" in without_tools
