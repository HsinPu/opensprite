"""Common request builders for LLM provider transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ChatMessage


@dataclass(frozen=True)
class LLMRequestOptions:
    """Provider-neutral inputs for LLM request payloads with optional fields."""

    model: str
    messages: list[dict[str, Any]]
    input_key: str = "messages"
    tools: list[dict[str, Any]] | None = None
    max_tokens: int | None = None
    max_tokens_param: str = "max_tokens"
    extra_body: dict[str, Any] | None = None
    extra_params: dict[str, Any] | None = None
    stream: bool = False
    tool_choice: Any = "auto"


@dataclass(frozen=True)
class LLMRequestProfile:
    """Provider request-shape profile used to keep transport params centralized."""

    input_key: str = "messages"
    max_tokens_param: str = "max_tokens"
    tool_choice: Any = "auto"
    include_reasoning_details: bool = False

    def options(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_params: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> LLMRequestOptions:
        """Create request options using this provider's fixed request shape."""
        return LLMRequestOptions(
            model=model,
            messages=messages,
            input_key=self.input_key,
            tools=tools,
            max_tokens=max_tokens,
            max_tokens_param=self.max_tokens_param,
            extra_body=extra_body,
            extra_params=extra_params,
            stream=stream,
            tool_choice=self.tool_choice,
        )


OPENAI_CHAT_REQUEST_PROFILE = LLMRequestProfile()
OPENAI_REASONING_HISTORY_REQUEST_PROFILE = LLMRequestProfile(include_reasoning_details=True)
OPENAI_RESPONSES_REQUEST_PROFILE = LLMRequestProfile(
    input_key="input",
    max_tokens_param="max_output_tokens",
    tool_choice=None,
)


def build_llm_request(options: LLMRequestOptions) -> dict[str, Any]:
    """Build an LLM request payload while omitting unset optional fields."""
    params: dict[str, Any] = {
        "model": options.model,
        options.input_key: options.messages,
    }

    if options.max_tokens is not None:
        params[options.max_tokens_param] = options.max_tokens

    if options.extra_body:
        params["extra_body"] = options.extra_body

    if options.extra_params:
        params.update(options.extra_params)

    if options.tools:
        params["tools"] = options.tools
        if options.tool_choice is not None:
            params["tool_choice"] = options.tool_choice

    if options.stream:
        params["stream"] = True

    return params


def normalize_openai_compatible_messages(
    messages: list[ChatMessage | dict[str, Any]],
    *,
    include_reasoning_details: bool = False,
) -> list[dict[str, Any]]:
    """Convert internal chat messages into OpenAI-compatible message payloads."""
    api_messages: list[dict[str, Any]] = []

    for message in messages:
        if isinstance(message, dict):
            msg = {
                "role": message.get("role", "?"),
                "content": message.get("content", ""),
            }
            if message.get("tool_call_id"):
                msg["tool_call_id"] = message["tool_call_id"]
            if message.get("tool_calls"):
                msg["tool_calls"] = message["tool_calls"]
            if include_reasoning_details and message.get("reasoning_details"):
                msg["reasoning_details"] = message["reasoning_details"]
        else:
            msg = {"role": message.role, "content": message.content}
            if message.tool_call_id:
                msg["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                msg["tool_calls"] = message.tool_calls
            if include_reasoning_details and message.reasoning_details:
                msg["reasoning_details"] = message.reasoning_details
        api_messages.append(msg)

    return api_messages
