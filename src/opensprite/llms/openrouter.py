"""
opensprite/llms/openrouter.py - OpenRouter LLM 實作

實作 LLMProvider 介面，使用 OpenRouter API
OpenRouter 可以訪問多種 LLM 模型（OpenAI、Anthropic、Meta 等）
"""
from typing import Any, Awaitable, Callable

from .base import LLMProvider, LLMResponse, ChatMessage, ToolCall
from .openai_streaming import collect_openai_compatible_stream, coerce_reasoning_details
from .tool_args import parse_tool_arguments
from ..utils.log import logger


_OPENROUTER_TIMEOUT_SECONDS = 120.0
_OPENROUTER_CONNECT_TIMEOUT_SECONDS = 20.0


def _safe_len(value: Any) -> str:
    try:
        return str(len(value))
    except Exception:
        return "n/a"


def _coerce_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def _usage_payload(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            return dict(usage.model_dump(exclude_none=True))
        except Exception:
            pass
    if isinstance(usage, dict):
        return dict(usage)
    return {
        key: getattr(usage, key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }


class OpenRouterLLM(LLMProvider):
    """
    OpenRouter LLM 實作
    
    使用 OpenRouter API，可以訪問多種 LLM 模型
    官網：https://openrouter.ai/
    """
    
    def __init__(
        self, 
        api_key: str, 
        default_model: str = "openai/gpt-4o-mini",
        base_url: str = "",
        reasoning_enabled: bool = True,
        reasoning_effort: str | None = "medium",
        reasoning_max_tokens: int | None = None,
        reasoning_exclude: bool = False,
        provider_sort: str | None = None,
        require_parameters: bool = False,
    ):
        """
        初始化 OpenRouter LLM
        
        參數：
            api_key: OpenRouter API Key
            default_model: 預設模型名稱
                常用模型：
                - openai/gpt-4o-mini
                - openai/gpt-4o
                - anthropic/claude-3.5-sonnet
                - meta-llama/llama-3.1-70b-instruct
                - google/gemma-2-27b-instruct
        """
        self.api_key = api_key
        self.default_model = default_model
        self.reasoning_enabled = reasoning_enabled
        self.reasoning_effort = reasoning_effort
        self.reasoning_max_tokens = reasoning_max_tokens
        self.reasoning_exclude = reasoning_exclude
        self.provider_sort = provider_sort
        self.require_parameters = require_parameters
        from httpx import Timeout

        self._client_kwargs = {
            "api_key": api_key,
            "base_url": base_url or "https://openrouter.ai/api/v1",
            # OpenRouter 需要這些 headers
            "default_headers": {
                "HTTP-Referer": "https://github.com/HsinPu/opensprite",
                "X-OpenRouter-Title": "OpenSprite",
                "X-Title": "OpenSprite"
            },
            "timeout": Timeout(
                _OPENROUTER_TIMEOUT_SECONDS,
                connect=_OPENROUTER_CONNECT_TIMEOUT_SECONDS,
                read=_OPENROUTER_TIMEOUT_SECONDS,
                write=30.0,
                pool=_OPENROUTER_CONNECT_TIMEOUT_SECONDS,
            ),
        }
        self.client = self._build_client()

    def _build_client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI(**self._client_kwargs)

    async def _create_completion(self, params: dict[str, Any]):
        try:
            return await self.client.chat.completions.create(**params)
        except Exception as exc:
            extra_body = params.get("extra_body") if isinstance(params.get("extra_body"), dict) else {}
            if "reasoning" not in extra_body:
                raise
            logger.warning(
                "OpenRouter reasoning request failed; retrying without reasoning: model={}, error={}",
                params.get("model"),
                exc,
            )
            self.reasoning_enabled = False
            self.reasoning_effort = None
            self.reasoning_max_tokens = None
            self.reasoning_exclude = False
            retry_params = dict(params)
            retry_extra_body = dict(extra_body)
            retry_extra_body.pop("reasoning", None)
            if retry_extra_body:
                retry_params["extra_body"] = retry_extra_body
            else:
                retry_params.pop("extra_body", None)
            return await self.client.chat.completions.create(**retry_params)
    
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
        """
        呼叫 OpenRouter Chat Completions API
        """
        _ = status_callback
        # 轉換成 OpenAI 格式
        api_messages = []
        for m in messages:
            if isinstance(m, dict):
                msg = {"role": m.get("role", "?"), "content": m.get("content", "")}
                if m.get("tool_call_id"):
                    msg["tool_call_id"] = m["tool_call_id"]
                if m.get("tool_calls"):
                    msg["tool_calls"] = m["tool_calls"]
                if m.get("reasoning_details"):
                    msg["reasoning_details"] = m["reasoning_details"]
            else:
                msg = {"role": m.role, "content": m.content}
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                if m.tool_calls:
                    msg["tool_calls"] = m.tool_calls
                if m.reasoning_details:
                    msg["reasoning_details"] = m.reasoning_details
            api_messages.append(msg)
        
        params: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": api_messages,
        }
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if top_p is not None:
            params["top_p"] = top_p
        if frequency_penalty is not None:
            params["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            params["presence_penalty"] = presence_penalty

        extra_body: dict[str, Any] = {}
        reasoning: dict[str, Any] = {}
        if self.reasoning_enabled:
            if self.reasoning_effort:
                reasoning["effort"] = self.reasoning_effort
            if self.reasoning_max_tokens is not None:
                reasoning["max_tokens"] = self.reasoning_max_tokens
        if self.reasoning_exclude:
            reasoning["exclude"] = True
        if reasoning:
            extra_body["reasoning"] = reasoning

        provider: dict[str, Any] = {}
        if self.provider_sort:
            provider["sort"] = self.provider_sort
        if self.require_parameters:
            provider["require_parameters"] = True
        if provider:
            extra_body["provider"] = provider
        if extra_body:
            params["extra_body"] = extra_body

        # 加入 tools 如果有
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        if response_delta_callback is not None:
            params["stream"] = True
            stream = await self._create_completion(params)
            return await collect_openai_compatible_stream(
                stream,
                provider_name="OpenRouter",
                default_model=model or self.default_model,
                response_delta_callback=response_delta_callback,
                tool_input_delta_callback=tool_input_delta_callback,
                reasoning_delta_callback=reasoning_delta_callback,
            )

        # 呼叫 API
        response = await self._create_completion(params)
        choices = getattr(response, "choices", None)
        logger.info(
            "OpenRouter response summary: model={}, choices_type={}, choices_len={}",
            getattr(response, "model", None),
            type(choices).__name__,
            _safe_len(choices),
        )

        if not choices:
            logger.warning(
                "OpenRouter returned empty choices: response_id={}, model={}, object={}, usage={}",
                getattr(response, "id", None),
                getattr(response, "model", None),
                getattr(response, "object", None),
                getattr(response, "usage", None),
            )
            return LLMResponse(
                content="",
                model=getattr(response, "model", model or self.default_model),
                tool_calls=[],
                usage=_usage_payload(getattr(response, "usage", None)),
            )

        try:
            message = choices[0].message
        except Exception:
            logger.exception(
                "OpenRouter response parse failed: response_type={}, model={}, choices_type={}, choices_len={}, choices_preview={}",
                type(response).__name__,
                getattr(response, "model", None),
                type(choices).__name__,
                _safe_len(choices),
                repr(choices)[:500],
            )
            return LLMResponse(
                content="",
                model=getattr(response, "model", model or self.default_model),
                tool_calls=[],
                usage=_usage_payload(getattr(response, "usage", None)),
            )

        if message is None:
            logger.warning("OpenRouter response missing message payload; returning empty response")
            return LLMResponse(
                content="",
                model=getattr(response, "model", model or self.default_model),
                tool_calls=[],
                usage=_usage_payload(getattr(response, "usage", None)),
            )
        
        # 解析 tool calls
        tool_calls = []
        if getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                function = getattr(tc, "function", None)
                if function is None:
                    logger.warning("OpenRouter tool call missing function payload; skipping")
                    continue
                args = parse_tool_arguments(
                    getattr(function, "arguments", None),
                    provider_name="OpenRouter",
                    tool_name=getattr(function, "name", "") or "",
                )
                
                tool_calls.append(ToolCall(
                    id=getattr(tc, "id", "") or f"tool_call_{len(tool_calls) + 1}",
                    name=getattr(function, "name", "") or "",
                    arguments=args
                ))

        reasoning_details = coerce_reasoning_details(getattr(message, "reasoning_details", None))
        if reasoning_details:
            logger.info("OpenRouter response reasoning_details count={}", len(reasoning_details))
        
        return LLMResponse(
            content=_coerce_content(getattr(message, "content", "")),
            model=getattr(response, "model", model or self.default_model),
            tool_calls=tool_calls,
            usage=_usage_payload(getattr(response, "usage", None)),
            finish_reason=str(getattr(choices[0], "finish_reason", "") or "") or None,
            reasoning_details=reasoning_details,
        )
    
    def get_default_model(self) -> str:
        return self.default_model

    def recover_after_error(self, error: BaseException) -> bool:
        _ = error
        try:
            self.client = self._build_client()
            return True
        except Exception:
            return False
