"""opensprite/llms/registry.py - LLM Provider Registry

用 Registry 模式管理所有 LLM Provider，方便擴充。
"""

from dataclasses import dataclass
from .base import LLMProvider
from .openai import OpenAILLM
from .openai_responses import OpenAIResponsesLLM
from .openrouter import OpenRouterLLM
from .minimax import MiniMaxLLM


@dataclass(frozen=True)
class ProviderSpec:
    """單一 LLM Provider 的元數據"""
    name: str
    keywords: tuple[str, ...]
    detect_by_key_prefix: str = ""
    detect_by_base_keyword: str = ""
    default_base_url: str = ""


PROVIDERS = (
    ProviderSpec("openrouter", ("openrouter",), "sk-or-", "openrouter", "https://openrouter.ai/api/v1"),
    ProviderSpec("openai", ("gpt", "openai"), "", "", "https://api.openai.com/v1"),
    ProviderSpec("minimax", ("minimax",), "", "minimax", "https://api.minimax.io/v1"),
)


def find_provider(api_key: str = "", base_url: str = "", model: str = "", provider_name: str = "") -> ProviderSpec:
    """自動判斷要用哪個 Provider"""
    # 1. 直接指定
    for spec in PROVIDERS:
        if provider_name == spec.name:
            return spec
    # 2. API Key 前綴
    if api_key.startswith("sk-or-"):
        return PROVIDERS[0]  # openrouter
    # 3. base_url 關鍵字
    if "openrouter" in base_url:
        return PROVIDERS[0]
    if "minimax" in base_url:
        return PROVIDERS[2]  # minimax
    # 4. model 關鍵字
    if "gpt" in model.lower():
        return PROVIDERS[1]  # openai
    if "minimax" in model.lower():
        return PROVIDERS[2]  # minimax
    # 預設
    return PROVIDERS[1]  # openai


def create_llm(
    api_key: str,
    model: str,
    base_url: str = "",
    provider_name: str = "",
    enabled: bool = True,
    api_mode: str | None = None,
    auth_type: str = "api_key",
    reasoning_enabled: bool = False,
    reasoning_effort: str | None = None,
    reasoning_max_tokens: int | None = None,
    reasoning_exclude: bool = False,
    provider_sort: str | None = None,
    require_parameters: bool = False,
) -> LLMProvider:
    """建立 LLM Provider"""
    if not enabled:
        raise ValueError(f"Provider {provider_name} is disabled")

    if api_mode == "responses" or auth_type == "openai_codex_oauth":
        return OpenAIResponsesLLM(api_key=api_key, base_url=base_url, default_model=model)
    
    spec = find_provider(api_key, base_url, model, provider_name)
    
    if spec.name == "openrouter":
        return OpenRouterLLM(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            reasoning_enabled=reasoning_enabled,
            reasoning_effort=reasoning_effort,
            reasoning_max_tokens=reasoning_max_tokens,
            reasoning_exclude=reasoning_exclude,
            provider_sort=provider_sort,
            require_parameters=require_parameters,
        )
    
    if spec.name == "minimax":
        return MiniMaxLLM(api_key=api_key, default_model=model)
    
    return OpenAILLM(api_key=api_key, base_url=base_url or spec.default_base_url, default_model=model)
