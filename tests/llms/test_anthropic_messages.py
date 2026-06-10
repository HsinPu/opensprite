import asyncio

from opensprite.llms import ChatMessage
from opensprite.llms.anthropic_messages import AnthropicMessagesLLM


def test_anthropic_messages_builds_basic_payload_without_thinking_options():
    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic/",
        default_model="MiniMax-M2.7",
    )
    payload = provider._build_payload(
        [ChatMessage(role="system", content="Be helpful"), ChatMessage(role="user", content="Think deeply")],
        tools=None,
        model=None,
        max_tokens=1024,
    )

    assert provider.base_url == "https://api.minimax.io/anthropic"
    assert provider._headers()["Authorization"] == "Bearer minimax-key"
    assert "anthropic-beta" not in provider._headers()
    assert payload["model"] == "MiniMax-M2.7"
    assert payload["system"] == "Be helpful"
    assert payload["messages"] == [{"role": "user", "content": "Think deeply"}]
    assert "thinking" not in payload
    assert "temperature" not in payload
    assert payload["max_tokens"] == 1024
    assert "cache_control" not in str(payload)


def test_anthropic_messages_ignores_reasoning_effort_for_minimax_endpoint():
    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic/",
        default_model="MiniMax-M2.7",
        reasoning_effort="high",
    )
    payload = provider._build_payload(
        [ChatMessage(role="user", content="Think deeply")],
        tools=None,
        model=None,
        max_tokens=1024,
    )

    assert "thinking" not in payload
    assert "output_config" not in payload


def test_anthropic_messages_maps_reasoning_effort_for_official_anthropic_endpoint():
    provider = AnthropicMessagesLLM(
        api_key="anthropic-key",
        base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-6",
        reasoning_effort="high",
        prompt_cache_enabled=False,
    )
    payload = provider._build_payload(
        [ChatMessage(role="user", content="Think deeply")],
        tools=None,
        model=None,
        max_tokens=16000,
    )

    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert payload["output_config"] == {"effort": "high"}


def test_anthropic_messages_downgrades_xhigh_for_claude_4_6():
    provider = AnthropicMessagesLLM(
        api_key="anthropic-key",
        base_url="https://api.anthropic.com",
        default_model="claude-opus-4-6",
        reasoning_effort="xhigh",
        prompt_cache_enabled=False,
    )
    payload = provider._build_payload(
        [ChatMessage(role="user", content="Think deeply")],
        tools=None,
        model=None,
        max_tokens=16000,
    )

    assert payload["output_config"] == {"effort": "max"}


def test_anthropic_messages_uses_context_output_reserve_for_main_requests():
    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic/",
        default_model="MiniMax-M2.7",
    )

    assert provider.context_request_kwargs(output_token_reserve=32768) == {"max_tokens": 32768}


def test_anthropic_messages_omits_max_tokens_when_unset():
    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic/",
        default_model="MiniMax-M2.7",
    )

    payload = provider._build_payload(
        [ChatMessage(role="user", content="Hello")],
        tools=None,
        model=None,
        max_tokens=None,
    )

    assert "max_tokens" not in payload


def test_anthropic_messages_applies_prompt_cache_for_official_anthropic_base_url():
    provider = AnthropicMessagesLLM(
        api_key="anthropic-key",
        base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-6",
    )
    payload = provider._build_payload(
        [
            ChatMessage(role="system", content="Stable system"),
            ChatMessage(role="user", content="First"),
            ChatMessage(role="assistant", content="Second"),
            ChatMessage(role="user", content="Third"),
            ChatMessage(role="assistant", content="Fourth"),
        ],
        tools=None,
        model=None,
        max_tokens=1024,
    )

    assert payload["system"] == [
        {"type": "text", "text": "Stable system", "cache_control": {"type": "ephemeral"}}
    ]
    assert "cache_control" not in payload["messages"][0]["content"][0]
    assert [message["content"][-1]["cache_control"] for message in payload["messages"][-3:]] == [
        {"type": "ephemeral"},
        {"type": "ephemeral"},
        {"type": "ephemeral"},
    ]


def test_anthropic_messages_can_force_prompt_cache_for_compatible_endpoint():
    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic",
        default_model="MiniMax-M2.7",
        prompt_cache_enabled=True,
    )

    payload = provider._build_payload(
        [ChatMessage(role="system", content="Stable system"), ChatMessage(role="user", content="Hello")],
        tools=None,
        model=None,
        max_tokens=1024,
    )

    assert payload["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_messages_does_not_duplicate_v1_messages_path():
    provider = AnthropicMessagesLLM(
        api_key="anthropic-key",
        base_url="https://api.example.com/v1/",
        default_model="claude-sonnet-4-6",
    )

    assert provider.base_url == "https://api.example.com/v1"
    assert provider._messages_url() == "https://api.example.com/v1/messages"


def test_anthropic_messages_accepts_full_messages_endpoint_base_url():
    provider = AnthropicMessagesLLM(
        api_key="anthropic-key",
        base_url="https://api.example.com/v1/messages/",
        default_model="claude-sonnet-4-6",
    )

    assert provider._messages_url() == "https://api.example.com/v1/messages"


def test_anthropic_messages_appends_v1_messages_to_api_root():
    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic/",
        default_model="MiniMax-M2.7",
    )

    assert provider._messages_url() == "https://api.minimax.io/anthropic/v1/messages"


def test_anthropic_messages_response_maps_text_thinking_and_tools():
    calls = []

    async def fake_post(payload):
        calls.append(payload)
        return {
            "model": "MiniMax-M2.7",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "content": [
                {"type": "thinking", "thinking": "plan"},
                {"type": "text", "text": "I will call a tool."},
                {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": "x"}},
            ],
        }

    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic",
        default_model="MiniMax-M2.7",
    )
    provider._post_messages = fake_post

    response = asyncio.run(
        provider.chat(
            [ChatMessage(role="user", content="Use a tool")],
            tools=[{"name": "lookup", "parameters": {"type": "object"}}],
            max_tokens=1024,
        )
    )

    assert calls[0]["tools"] == [{"name": "lookup", "description": "", "input_schema": {"type": "object"}}]
    assert response.content == "I will call a tool."
    assert response.reasoning_details == [{"type": "thinking", "thinking": "plan"}]
    assert response.tool_calls[0].name == "lookup"
    assert response.tool_calls[0].arguments == {"q": "x"}
    assert response.finish_reason == "tool_use"
    assert response.usage == {"input_tokens": 10, "output_tokens": 3}
