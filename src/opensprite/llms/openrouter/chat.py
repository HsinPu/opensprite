"""
opensprite/llms/openrouter.py - OpenRouter LLM 實作

實作 LLMProvider 介面，使用 OpenRouter API
OpenRouter 可以訪問多種 LLM 模型（OpenAI、Anthropic、Meta 等）
"""
from typing import Any, Awaitable, Callable

from ..base import LLMProvider, LLMResponse, ChatMessage
from ..openai.streaming import collect_openai_compatible_stream
from ..reasoning import normalize_reasoning_effort, reasoning_config_or_default
from ..request_builder import OPENAI_REASONING_HISTORY_REQUEST_PROFILE, build_llm_request, normalize_openai_compatible_messages
from ..request_log_fields import log_llm_request_params
from ..response_utils import coerce_content as _coerce_content
from ..response_utils import coerce_reasoning_details
from ..response_utils import extract_openai_compatible_message
from ..response_utils import extract_openai_compatible_tool_calls
from ..response_utils import usage_payload as _usage_payload
from ...utils.log import logger


_OPENROUTER_TIMEOUT_SECONDS = 120.0
_OPENROUTER_CONNECT_TIMEOUT_SECONDS = 20.0
_REQUEST_PROFILE = OPENAI_REASONING_HISTORY_REQUEST_PROFILE


def _openrouter_reasoning_extra_body(reasoning_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build OpenRouter's provider-specific reasoning payload when explicitly configured."""
    if not reasoning_config:
        return None
    return {"reasoning": dict(reasoning_config)}


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
        reasoning_effort: str = "",
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
        self.reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        self.reasoning_config = reasoning_config_or_default(self.reasoning_effort)
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
        return await self.client.chat.completions.create(**params)
    
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
        """
        呼叫 OpenRouter Chat Completions API
        """
        _ = status_callback
        resolved_model = model or self.default_model
        # 轉換成 OpenAI 格式
        api_messages = normalize_openai_compatible_messages(
            messages,
            include_reasoning_details=_REQUEST_PROFILE.include_reasoning_details,
        )
        
        params = build_llm_request(
            _REQUEST_PROFILE.options(
                model=resolved_model,
                messages=api_messages,
                tools=tools,
                max_tokens=max_tokens,
                extra_body=_openrouter_reasoning_extra_body(self.reasoning_config),
                stream=response_delta_callback is not None,
            )
        )
        log_llm_request_params("OpenRouter", params, request_mode=request_mode)

        if response_delta_callback is not None:
            stream = await self._create_completion(params)
            return await collect_openai_compatible_stream(
                stream,
                provider_name="OpenRouter",
                default_model=resolved_model,
                response_delta_callback=response_delta_callback,
                tool_input_delta_callback=tool_input_delta_callback,
                reasoning_delta_callback=reasoning_delta_callback,
            )

        # 呼叫 API
        response = await self._create_completion(params)
        message_result = extract_openai_compatible_message(
            response,
            provider_name="OpenRouter",
            default_model=resolved_model,
        )
        if message_result.fallback_response is not None:
            return message_result.fallback_response
        message = message_result.message
        tool_calls = extract_openai_compatible_tool_calls(message, provider_name="OpenRouter")

        reasoning_details = coerce_reasoning_details(getattr(message, "reasoning_details", None))
        if reasoning_details:
            logger.info("OpenRouter response reasoning_details count={}", len(reasoning_details))
        
        return LLMResponse(
            content=_coerce_content(getattr(message, "content", "")),
            model=getattr(response, "model", resolved_model),
            tool_calls=tool_calls,
            usage=_usage_payload(getattr(response, "usage", None)),
            finish_reason=str(getattr(message_result.choice, "finish_reason", "") or "") or None,
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
