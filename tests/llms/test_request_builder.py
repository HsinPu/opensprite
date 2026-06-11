from opensprite.llms.base import ChatMessage
from opensprite.llms.reasoning import (
    is_valid_reasoning_effort,
    normalize_reasoning_effort,
    reasoning_config_from_effort,
)
from opensprite.llms.request_log_fields import request_param_log_fields
from opensprite.llms.request_modes import (
    JSON_PLANNING_MIN_OUTPUT_TOKENS,
    LLMRequestMode,
    request_kwargs_for_mode,
)
from opensprite.llms.request_builder import (
    OPENAI_RESPONSES_REQUEST_PROFILE,
    build_llm_request,
    normalize_openai_compatible_messages,
)


def test_normalize_openai_compatible_messages_omits_reasoning_details_by_default():
    messages = [
        ChatMessage(
            role="assistant",
            content="previous answer",
            tool_calls=[{"id": "call-1", "type": "function"}],
            reasoning_details=[{"type": "reasoning.text", "text": "thinking"}],
        )
    ]

    assert normalize_openai_compatible_messages(messages) == [
        {
            "role": "assistant",
            "content": "previous answer",
            "tool_calls": [{"id": "call-1", "type": "function"}],
        }
    ]


def test_normalize_openai_compatible_messages_includes_reasoning_details_when_enabled():
    messages = [
        {
            "role": "assistant",
            "content": "previous answer",
            "reasoning_details": [{"type": "reasoning.text", "text": "thinking"}],
        }
    ]

    assert normalize_openai_compatible_messages(messages, include_reasoning_details=True) == [
        {
            "role": "assistant",
            "content": "previous answer",
            "reasoning_details": [{"type": "reasoning.text", "text": "thinking"}],
        }
    ]


def test_normalize_openai_compatible_messages_uses_legacy_dict_defaults():
    assert normalize_openai_compatible_messages([{"content": "hello"}]) == [{"role": "?", "content": "hello"}]


def test_openai_responses_request_profile_uses_responses_param_shape():
    params = build_llm_request(
        OPENAI_RESPONSES_REQUEST_PROFILE.options(
            model="gpt-test",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "name": "lookup", "parameters": {}}],
            max_tokens=123,
            stream=True,
        )
    )

    assert params == {
        "model": "gpt-test",
        "input": [{"role": "user", "content": "hello"}],
        "max_output_tokens": 123,
        "tools": [{"type": "function", "name": "lookup", "parameters": {}}],
        "stream": True,
    }


def test_build_llm_request_includes_provider_extra_body_when_set():
    params = build_llm_request(
        OPENAI_RESPONSES_REQUEST_PROFILE.options(
            model="gpt-test",
            messages=[{"role": "user", "content": "hello"}],
            extra_body={"reasoning": {"enabled": True, "effort": "high"}},
        )
    )

    assert params["extra_body"] == {"reasoning": {"enabled": True, "effort": "high"}}


def test_build_llm_request_includes_top_level_extra_params_when_set():
    params = build_llm_request(
        OPENAI_RESPONSES_REQUEST_PROFILE.options(
            model="gpt-test",
            messages=[{"role": "user", "content": "hello"}],
            extra_params={"reasoning": {"effort": "medium"}},
        )
    )

    assert params["reasoning"] == {"effort": "medium"}


def test_reasoning_effort_helpers_normalize_supported_modes():
    assert normalize_reasoning_effort(" HIGH ") == "high"
    assert normalize_reasoning_effort("unknown") == ""
    assert is_valid_reasoning_effort("xhigh") is True
    assert is_valid_reasoning_effort("turbo") is False
    assert reasoning_config_from_effort("") is None
    assert reasoning_config_from_effort("none") == {"enabled": False}
    assert reasoning_config_from_effort("low") == {"enabled": True, "effort": "low"}


def test_request_mode_json_planning_enforces_minimum_output_tokens():
    kwargs = request_kwargs_for_mode({"max_tokens": 12}, LLMRequestMode.JSON_PLANNING)

    assert kwargs["request_mode"] == "json_planning"
    assert kwargs["max_tokens"] == JSON_PLANNING_MIN_OUTPUT_TOKENS


def test_request_param_log_fields_are_sanitized_and_provider_neutral():
    fields = request_param_log_fields(
        {
            "model": "test-model",
            "input": [{"role": "user", "content": "secret prompt"}],
            "tools": [{"type": "function", "function": {"name": "secret_tool"}}],
            "tool_choice": {"type": "auto"},
            "stream": True,
            "max_output_tokens": 321,
            "reasoning": {"effort": "high"},
        },
        request_mode=LLMRequestMode.COMPLETION_JUDGE,
    )

    assert fields == {
        "mode": "completion_judge",
        "model": "test-model",
        "messages": 1,
        "tools": 1,
        "tool_choice": '{"type":"auto"}',
        "stream": True,
        "max_tokens": 321,
        "reasoning": '{"effort":"high"}',
    }
    assert "secret prompt" not in str(fields)
    assert "secret_tool" not in str(fields)
