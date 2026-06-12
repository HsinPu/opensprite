from opensprite.agent.execution_support.events import format_repeated_invalid_tool_call_content


def test_repeated_invalid_tool_call_fallback_formats_configured_template():
    assert format_repeated_invalid_tool_call_content("REPEATED\n{result}", "bad args") == "REPEATED\nbad args"


def test_repeated_invalid_tool_call_fallback_uses_result_without_template():
    assert format_repeated_invalid_tool_call_content("", " bad args ") == "bad args"


def test_repeated_invalid_tool_call_fallback_preserves_bad_template():
    assert format_repeated_invalid_tool_call_content("REPEATED {missing}", "bad args") == "REPEATED {missing}\n\nbad args"
