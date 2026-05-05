"""OpenAI Responses API LLM provider skeleton."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .base import ChatMessage, LLMProvider, LLMResponse, ToolCall
from .tool_args import parse_tool_arguments


def _message_content(content: Any) -> Any:
    if isinstance(content, list):
        converted: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                converted.append({"type": "input_text", "text": str(item)})
                continue
            if item.get("type") == "text":
                converted.append({"type": "input_text", "text": item.get("text", "")})
            elif item.get("type") == "image_url":
                image_url = item.get("image_url") or {}
                converted.append({"type": "input_image", "image_url": image_url.get("url", "")})
            else:
                converted.append(dict(item))
        return converted
    return str(content or "")


def _response_input(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, dict):
            role = message.get("role", "user")
            content = message.get("content", "")
            tool_call_id = message.get("tool_call_id")
        else:
            role = message.role
            content = message.content
            tool_call_id = message.tool_call_id
        item = {"role": role, "content": _message_content(content)}
        if tool_call_id:
            item["tool_call_id"] = tool_call_id
        out.append(item)
    return out


def _responses_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        converted.append(
            {
                "type": "function",
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return converted


def _usage_payload(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            return dict(usage.model_dump(exclude_none=True))
        except Exception:
            pass
    return dict(usage) if isinstance(usage, dict) else {}


def _output_items(response: Any) -> list[Any]:
    output = getattr(response, "output", None)
    return list(output) if isinstance(output, list) else []


def _extract_tool_calls(response: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for item in _output_items(response):
        item_type = getattr(item, "type", None) if not isinstance(item, dict) else item.get("type")
        if item_type != "function_call":
            continue
        call_id = getattr(item, "call_id", None) if not isinstance(item, dict) else item.get("call_id")
        name = getattr(item, "name", None) if not isinstance(item, dict) else item.get("name")
        arguments = getattr(item, "arguments", None) if not isinstance(item, dict) else item.get("arguments")
        calls.append(
            ToolCall(
                id=str(call_id or f"tool_call_{len(calls) + 1}"),
                name=str(name or ""),
                arguments=parse_tool_arguments(arguments, provider_name="OpenAI Responses", tool_name=str(name or "")),
            )
        )
    return calls


class OpenAIResponsesLLM(LLMProvider):
    """OpenAI Responses API provider used by Codex OAuth and direct Responses routes."""

    def __init__(self, api_key: str, base_url: str | None = None, default_model: str = "gpt-5.1-codex"):
        from openai import AsyncOpenAI

        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        self._client_kwargs = {"api_key": api_key, **({"base_url": base_url} if base_url else {})}
        self.client = AsyncOpenAI(**self._client_kwargs)

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        status_callback: Callable[[str], Awaitable[None]] | None = None,
        response_delta_callback: Callable[[str], Awaitable[None]] | None = None,
        tool_input_delta_callback: Callable[[str, str, str, int], Awaitable[None]] | None = None,
        reasoning_delta_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        _ = status_callback, top_p, frequency_penalty, presence_penalty, tool_input_delta_callback
        params: dict[str, Any] = {
            "model": model or self.default_model,
            "input": _response_input(messages),
        }
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_output_tokens"] = max_tokens
        converted_tools = _responses_tools(tools)
        if converted_tools:
            params["tools"] = converted_tools

        if response_delta_callback is not None:
            params["stream"] = True
            stream = await self.client.responses.create(**params)
            text_parts: list[str] = []
            reasoning_parts: list[dict[str, Any]] = []
            async for event in stream:
                event_type = getattr(event, "type", "")
                delta = getattr(event, "delta", None)
                if event_type == "response.output_text.delta" and delta:
                    text = str(delta)
                    text_parts.append(text)
                    await response_delta_callback(text)
                elif event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"} and delta:
                    text = str(delta)
                    reasoning_parts.append({"type": event_type, "text": text})
                    if reasoning_delta_callback is not None:
                        await reasoning_delta_callback(text)
            return LLMResponse(
                content="".join(text_parts),
                model=model or self.default_model,
                reasoning_details=reasoning_parts or None,
            )

        response = await self.client.responses.create(**params)
        return LLMResponse(
            content=str(getattr(response, "output_text", "") or ""),
            model=str(getattr(response, "model", model or self.default_model)),
            tool_calls=_extract_tool_calls(response),
            usage=_usage_payload(getattr(response, "usage", None)),
            finish_reason=str(getattr(response, "status", "") or "") or None,
        )

    def get_default_model(self) -> str:
        return self.default_model

    def recover_after_error(self, error: BaseException) -> bool:
        _ = error
        try:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(**self._client_kwargs)
            return True
        except Exception:
            return False
