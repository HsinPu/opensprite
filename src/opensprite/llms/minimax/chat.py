"""MiniMax Anthropic-compatible Messages API provider."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from ..base import ChatMessage, LLMProvider, LLMResponse, ToolCall
from ..reasoning import normalize_reasoning_effort, reasoning_config_from_effort
from ..request_log_fields import log_llm_request_params
from ..response_utils import coerce_content as _coerce_content
from ..tool_args import parse_tool_arguments
from ...utils.url import join_url_path


def _as_plain_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_as_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_as_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _as_plain_data(item) for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _as_plain_data(model_dump())
    return str(value)


def _message_attr(message: ChatMessage | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _convert_content_part(part: Any) -> dict[str, Any] | None:
    if isinstance(part, str):
        return {"type": "text", "text": part}
    if not isinstance(part, dict):
        return {"type": "text", "text": str(part)}

    part_type = part.get("type")
    if part_type in {"text", "input_text"}:
        return {"type": "text", "text": _coerce_content(part.get("text", ""))}
    if part_type in {"image", "tool_use", "tool_result", "thinking", "redacted_thinking"}:
        return dict(part)
    if part_type in {"image_url", "input_image"}:
        image_value = part.get("image_url") or part.get("source") or {}
        url = image_value.get("url", "") if isinstance(image_value, dict) else str(image_value or "")
        if not url.startswith("data:"):
            return {"type": "text", "text": f"[Image: {url}]" if url else "[Image]"}
        header, _, data = url.partition(",")
        media_type = header.removeprefix("data:").split(";", 1)[0] or "image/jpeg"
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    return {"type": "text", "text": _coerce_content(part.get("text", part))}


def _convert_content(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, list):
        blocks = [_convert_content_part(part) for part in content]
        return [block for block in blocks if block is not None]
    return _coerce_content(content)


def _convert_tool(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    return {
        "name": str(function.get("name") or tool.get("name") or ""),
        "description": str(function.get("description") or tool.get("description") or ""),
        "input_schema": function.get("parameters") or tool.get("parameters") or {"type": "object", "properties": {}},
    }


class MiniMaxLLM(LLMProvider):
    """MiniMax provider using the Anthropic-compatible Messages API."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        default_model: str = "MiniMax-M2.7",
        *,
        timeout_seconds: float = 900.0,
        reasoning_effort: str = "",
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or "https://api.minimax.io/anthropic").rstrip("/")
        self.default_model = default_model
        self.reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        self.reasoning_config = reasoning_config_from_effort(self.reasoning_effort)
        self.timeout_seconds = timeout_seconds

    def context_request_kwargs(self, *, output_token_reserve: int) -> dict[str, Any]:
        """Anthropic Messages requires an explicit output token cap."""
        return {"max_tokens": max(1, int(output_token_reserve))}

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        return headers

    def _build_messages(self, messages: list[ChatMessage | dict[str, Any]]) -> tuple[str | list[dict[str, Any]] | None, list[dict[str, Any]]]:
        system_blocks: list[Any] = []
        out: list[dict[str, Any]] = []
        for message in messages:
            role = str(_message_attr(message, "role", "user") or "user")
            content = _message_attr(message, "content", "")
            if role == "system":
                system_blocks.append(_convert_content(content))
                continue
            if role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": str(_message_attr(message, "tool_call_id", "")),
                        "content": _coerce_content(content),
                    }],
                })
                continue
            anthropic_role = "assistant" if role == "assistant" else "user"
            converted = _convert_content(content)
            tool_calls = _message_attr(message, "tool_calls") or []
            if tool_calls:
                blocks = converted if isinstance(converted, list) else ([{"type": "text", "text": converted}] if converted else [])
                for call in tool_calls:
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    blocks.append({
                        "type": "tool_use",
                        "id": str(call.get("id") or f"tool_call_{len(blocks) + 1}"),
                        "name": str(function.get("name") or call.get("name") or ""),
                        "input": parse_tool_arguments(
                            function.get("arguments", call.get("arguments", {})),
                            provider_name="MiniMax",
                            tool_name=str(function.get("name") or call.get("name") or ""),
                        ),
                    })
                converted = blocks
            out.append({"role": anthropic_role, "content": converted})

        if not system_blocks:
            return None, out
        if len(system_blocks) == 1:
            return system_blocks[0], out
        flattened: list[dict[str, Any]] = []
        for block in system_blocks:
            if isinstance(block, list):
                flattened.extend(block)
            else:
                flattened.append({"type": "text", "text": _coerce_content(block)})
        return flattened, out

    def _build_payload(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        system, anthropic_messages = self._build_messages(messages)
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": anthropic_messages,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_convert_tool(tool) for tool in tools]
            payload["tool_choice"] = {"type": "auto"}
        return payload

    async def _post_messages(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=10.0)) as client:
            response = await client.post(self._messages_url(), headers=self._headers(), json=payload)
            response.raise_for_status()
            return response.json()

    def _messages_url(self) -> str:
        return join_url_path(self.base_url, "/v1/messages")

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        status_callback: Callable[[str], Awaitable[None]] | None = None,
        response_delta_callback: Callable[[str], Awaitable[None]] | None = None,
        tool_input_delta_callback: Callable[[str, str, str, int], Awaitable[None]] | None = None,
        reasoning_delta_callback: Callable[[str], Awaitable[None]] | None = None,
        request_mode: str | None = None,
    ) -> LLMResponse:
        _ = status_callback, response_delta_callback, tool_input_delta_callback
        payload = self._build_payload(messages, tools, model, max_tokens)
        log_llm_request_params("MiniMax", payload, request_mode=request_mode)
        data = await self._post_messages(payload)
        content_blocks = data.get("content") if isinstance(data.get("content"), list) else []
        text_parts: list[str] = []
        reasoning_details: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(_coerce_content(block.get("text", "")))
            elif block_type == "thinking":
                reasoning_details.append(_as_plain_data(block))
                if reasoning_delta_callback is not None:
                    await reasoning_delta_callback(_coerce_content(block.get("thinking", "")))
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(
                    id=str(block.get("id") or f"tool_call_{len(tool_calls) + 1}"),
                    name=str(block.get("name") or ""),
                    arguments=parse_tool_arguments(block.get("input") or {}, provider_name="MiniMax", tool_name=str(block.get("name") or "")),
                ))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return LLMResponse(
            content="\n".join(part for part in text_parts if part),
            model=str(data.get("model") or model or self.default_model),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=str(data.get("stop_reason") or "") or None,
            reasoning_details=reasoning_details or None,
        )

    def get_default_model(self) -> str:
        return self.default_model
