import asyncio

from opensprite.llms import ChatMessage
from opensprite.llms.anthropic_messages import AnthropicMessagesLLM


def test_anthropic_messages_minimax_enables_thinking_and_headers():
    provider = AnthropicMessagesLLM(
        api_key="minimax-key",
        base_url="https://api.minimax.io/anthropic/",
        default_model="MiniMax-M2.7",
        reasoning_effort="high",
    )
    payload = provider._build_payload(
        [ChatMessage(role="system", content="Be helpful"), ChatMessage(role="user", content="Think deeply")],
        tools=None,
        model=None,
        max_tokens=1024,
    )

    assert provider.base_url == "https://api.minimax.io/anthropic"
    assert provider._headers()["Authorization"] == "Bearer minimax-key"
    assert provider._headers()["anthropic-beta"] == "interleaved-thinking-2025-05-14"
    assert payload["model"] == "MiniMax-M2.7"
    assert payload["system"] == "Be helpful"
    assert payload["messages"] == [{"role": "user", "content": "Think deeply"}]
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 16000}
    assert payload["temperature"] == 1
    assert payload["max_tokens"] == 20096
    assert "cache_control" not in str(payload)


def test_anthropic_messages_applies_prompt_cache_for_official_anthropic_base_url():
    provider = AnthropicMessagesLLM(
        api_key="anthropic-key",
        base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-6",
        reasoning_enabled=False,
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
        reasoning_enabled=False,
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

    response = asyncio.run(provider.chat([ChatMessage(role="user", content="Use a tool")], tools=[{"name": "lookup", "parameters": {"type": "object"}}]))

    assert calls[0]["tools"] == [{"name": "lookup", "description": "", "input_schema": {"type": "object"}}]
    assert response.content == "I will call a tool."
    assert response.reasoning_details == [{"type": "thinking", "thinking": "plan"}]
    assert response.tool_calls[0].name == "lookup"
    assert response.tool_calls[0].arguments == {"q": "x"}
    assert response.finish_reason == "tool_use"
    assert response.usage == {"input_tokens": 10, "output_tokens": 3}
