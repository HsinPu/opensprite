"""Resolve configured LLM providers into runtime client settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..auth.credentials import (
    DEFAULT_LLM_CAPABILITY,
    CredentialNotFoundError,
    mark_credential_used,
    resolve_credential,
)
from ..auth.codex import CodexAuthError, load_or_refresh_codex_token
from ..auth.copilot import COPILOT_BASE_URL, CopilotAuthError, get_copilot_api_token, load_copilot_token
from ..config import ProviderConfig


OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
GITHUB_COPILOT_BASE_URL = COPILOT_BASE_URL
class ProviderRuntimeError(RuntimeError):
    """Raised when a configured provider cannot be resolved for runtime use."""


@dataclass(frozen=True)
class ResolvedProviderRuntime:
    provider_name: str
    api_key: str
    model: str
    base_url: str
    enabled: bool
    api_mode: str | None = None
    auth_type: str = "api_key"
    reasoning_enabled: bool = False
    reasoning_effort: str | None = None
    reasoning_max_tokens: int | None = None
    reasoning_exclude: bool = False
    provider_sort: str | None = None
    require_parameters: bool = False


def default_app_home(config_path: str | Path | None = None) -> Path:
    if config_path is not None:
        return Path(config_path).expanduser().resolve().parent
    return Path.home() / ".opensprite"


def resolve_provider_runtime(
    provider: ProviderConfig,
    *,
    provider_name: str,
    app_home: str | Path | None = None,
) -> ResolvedProviderRuntime:
    """Resolve a ProviderConfig into the arguments needed by create_llm()."""
    configured_provider = str(provider.provider or provider_name or "").strip()
    auth_type = provider.auth_type
    api_mode = provider.api_mode
    base_url = str(provider.base_url or "").strip()
    api_key = str(provider.api_key or "").strip()
    credential_id = str(provider.credential_id or "").strip()
    app_home_path = Path(app_home) if app_home is not None else default_app_home()

    if auth_type == "openai_codex_oauth":
        configured_provider = configured_provider or "openai-codex"
        api_mode = api_mode or "responses"
        base_url = base_url or OPENAI_CODEX_BASE_URL
        if not api_key:
            try:
                api_key = load_or_refresh_codex_token(
                    app_home_path
                ).access_token
            except CodexAuthError as exc:
                raise ProviderRuntimeError(str(exc)) from exc
    elif configured_provider == "copilot" or auth_type == "github_copilot_oauth":
        configured_provider = "copilot"
        base_url = base_url or GITHUB_COPILOT_BASE_URL
        api_mode = api_mode or "chat_completions"
        if not api_key and auth_type == "github_copilot_oauth":
            try:
                api_key = load_copilot_token(app_home_path).access_token
            except CopilotAuthError as exc:
                raise ProviderRuntimeError(str(exc)) from exc
        api_key = get_copilot_api_token(api_key)
    elif api_mode is None:
        api_mode = "chat_completions"

    if not api_key and auth_type == "api_key":
        try:
            credential = resolve_credential(
                provider=configured_provider,
                credential_id=credential_id or None,
                capability=DEFAULT_LLM_CAPABILITY,
                app_home=app_home_path,
            )
            api_key = credential.secret
            if not base_url and credential.base_url:
                base_url = credential.base_url
            mark_credential_used(credential.provider, credential.id, app_home=app_home_path)
        except CredentialNotFoundError as exc:
            if credential_id:
                raise ProviderRuntimeError(str(exc)) from exc
    elif not api_key and auth_type == "optional_api_key":
        api_key = "no-key-required"

    return ResolvedProviderRuntime(
        provider_name=configured_provider,
        api_key=api_key,
        model=provider.model,
        base_url=base_url,
        enabled=provider.enabled,
        api_mode=api_mode,
        auth_type=auth_type,
        reasoning_enabled=provider.reasoning_enabled,
        reasoning_effort=provider.reasoning_effort,
        reasoning_max_tokens=provider.reasoning_max_tokens,
        reasoning_exclude=provider.reasoning_exclude,
        provider_sort=provider.provider_sort,
        require_parameters=provider.require_parameters,
    )


def create_llm_from_runtime(runtime: ResolvedProviderRuntime):
    from .registry import create_llm

    return create_llm(
        api_key=runtime.api_key,
        model=runtime.model,
        base_url=runtime.base_url,
        provider_name=runtime.provider_name,
        enabled=runtime.enabled,
        api_mode=runtime.api_mode,
        auth_type=runtime.auth_type,
        reasoning_enabled=runtime.reasoning_enabled,
        reasoning_effort=runtime.reasoning_effort,
        reasoning_max_tokens=runtime.reasoning_max_tokens,
        reasoning_exclude=runtime.reasoning_exclude,
        provider_sort=runtime.provider_sort,
        require_parameters=runtime.require_parameters,
    )
