"""Helpers for OpenAI-compatible chat completion streams."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .base import LLMResponse, ToolCall
from .tool_args import parse_tool_arguments
from ..utils.log import logger


@dataclass
class _ToolCallBuffer:
    index: int
    id: str = ""
    name: str = ""
    arguments_parts: list[str] = field(default_factory=list)


def _coerce_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def _coerce_reasoning(delta_payload: Any) -> str:
    for name in ("reasoning_content", "reasoning", "reasoning_text"):
        value = _get_attr_or_item(delta_payload, name)
        if value:
            return _coerce_content(value)
    return ""


def _get_attr_or_item(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _iter_tool_call_deltas(delta_payload: Any) -> list[Any]:
    tool_calls = _get_attr_or_item(delta_payload, "tool_calls")
    if not tool_calls:
        return []
    return list(tool_calls)


async def collect_openai_compatible_stream(
    stream: Any,
    *,
    provider_name: str,
    default_model: str,
    response_delta_callback: Callable[[str], Awaitable[None]] | None = None,
    tool_input_delta_callback: Callable[[str, str, str, int], Awaitable[None]] | None = None,
    reasoning_delta_callback: Callable[[str], Awaitable[None]] | None = None,
) -> LLMResponse:
    """Collect text and tool-call chunks from an OpenAI-compatible async stream."""
    content_parts: list[str] = []
    tool_buffers: dict[int, _ToolCallBuffer] = {}
    model_name = default_model

    async for chunk in stream:
        model_name = getattr(chunk, "model", model_name)
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta_payload = getattr(choices[0], "delta", None)
        if delta_payload is None:
            continue

        piece = _coerce_content(_get_attr_or_item(delta_payload, "content", ""))
        if piece:
            content_parts.append(piece)
            if response_delta_callback is not None:
                try:
                    await response_delta_callback(piece)
                except Exception as cb_err:
                    logger.warning("{} response_delta_callback failed; continuing stream: {}", provider_name, cb_err)

        reasoning_piece = _coerce_reasoning(delta_payload)
        if reasoning_piece and reasoning_delta_callback is not None:
            try:
                await reasoning_delta_callback(reasoning_piece)
            except Exception as cb_err:
                logger.warning("{} reasoning_delta_callback failed; continuing stream: {}", provider_name, cb_err)

        for tool_delta in _iter_tool_call_deltas(delta_payload):
            index = int(_get_attr_or_item(tool_delta, "index", len(tool_buffers)) or 0)
            buffer = tool_buffers.setdefault(index, _ToolCallBuffer(index=index))
            call_id = _get_attr_or_item(tool_delta, "id")
            if call_id:
                buffer.id = str(call_id)
            function = _get_attr_or_item(tool_delta, "function")
            if function is None:
                continue
            name = _get_attr_or_item(function, "name")
            if name:
                buffer.name = str(name)
            arguments = _get_attr_or_item(function, "arguments")
            if arguments:
                argument_delta = str(arguments)
                buffer.arguments_parts.append(argument_delta)
                if tool_input_delta_callback is not None:
                    try:
                        await tool_input_delta_callback(
                            buffer.id or f"tool_call_{index + 1}",
                            buffer.name,
                            argument_delta,
                            len(buffer.arguments_parts),
                        )
                    except Exception as cb_err:
                        logger.warning("{} tool_input_delta_callback failed; continuing stream: {}", provider_name, cb_err)

    tool_calls = []
    for position, buffer in enumerate(sorted(tool_buffers.values(), key=lambda item: item.index), start=1):
        if not buffer.name:
            logger.warning("{} stream tool call missing function name; skipping index={}", provider_name, buffer.index)
            continue
        tool_calls.append(
            ToolCall(
                id=buffer.id or f"tool_call_{position}",
                name=buffer.name,
                arguments=parse_tool_arguments(
                    "".join(buffer.arguments_parts),
                    provider_name=provider_name,
                    tool_name=buffer.name,
                ),
            )
        )

    return LLMResponse(content="".join(content_parts), model=model_name, tool_calls=tool_calls)
