"""Provider wrappers for routing subagent calls to alternate models."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .base import LLMProvider, LLMResponse, ChatMessage


class ModelRoutedProvider(LLMProvider):
    """Wrap one provider and inject a model override for delegated calls."""

    def __init__(self, base_provider: LLMProvider, *, model: str):
        self.base_provider = base_provider
        self.model = str(model or "").strip()

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
        return await self.base_provider.chat(
            messages=messages,
            tools=tools,
            model=model or self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            status_callback=status_callback,
            response_delta_callback=response_delta_callback,
            tool_input_delta_callback=tool_input_delta_callback,
            reasoning_delta_callback=reasoning_delta_callback,
        )

    def get_default_model(self) -> str:
        return self.model or self.base_provider.get_default_model()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base_provider, name)
