"""Load packaged LLM provider presets for Web settings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any


@dataclass(frozen=True)
class ProviderPreset:
    """Preset fields for one LLM vendor."""

    default_base_url: str
    model_choices: tuple[str, ...]
    display_name: str | None = None
    auth_type: str = "api_key"
    api_mode: str | None = None
    capabilities: tuple[str, ...] = ()
    model_discovery: dict[str, Any] | None = None
    media_discovery: dict[str, Any] | None = None
    model_metadata_fields: tuple[str, ...] = ()
    media_model_choices: dict[str, tuple[str, ...]] | None = None
    model_capabilities: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class LLMPresets:
    """Bundled llm-presets.json content."""

    version: int
    provider_order: tuple[str, ...]
    providers: dict[str, ProviderPreset]


@dataclass(frozen=True)
class ProviderProfileDefaults:
    """Effective provider defaults inferred from a bundled profile."""

    provider_id: str
    auth_type: str
    api_mode: str | None
    default_base_url: str


def _parse_providers(raw: dict[str, Any]) -> dict[str, ProviderPreset]:
    out: dict[str, ProviderPreset] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f'llm-presets: providers["{name}"] must be an object')
        base = entry.get("default_base_url")
        if not isinstance(base, str) or not base.strip():
            raise ValueError(f'llm-presets: providers["{name}"].default_base_url is required')
        models = entry.get("model_choices", [])
        if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
            raise ValueError(f'llm-presets: providers["{name}"].model_choices must be a string array')
        dn = entry.get("display_name")
        display = str(dn).strip() if isinstance(dn, str) and dn.strip() else None
        auth_type = str(entry.get("auth_type") or "api_key").strip()
        api_mode_raw = entry.get("api_mode")
        api_mode = str(api_mode_raw).strip() if isinstance(api_mode_raw, str) and api_mode_raw.strip() else None
        capabilities = _parse_string_tuple(entry, name, "capabilities")
        model_discovery = _parse_discovery_config(entry, name, "model_discovery")
        media_discovery = _parse_discovery_config(entry, name, "media_discovery")
        model_metadata_fields = _parse_string_tuple(entry, name, "model_metadata_fields")
        raw_media_models = entry.get("media_model_choices", {})
        if raw_media_models is None:
            raw_media_models = {}
        if not isinstance(raw_media_models, dict):
            raise ValueError(f'llm-presets: providers["{name}"].media_model_choices must be an object')
        media_models: dict[str, tuple[str, ...]] = {}
        for category, category_models in raw_media_models.items():
            if not isinstance(category, str) or not isinstance(category_models, list) or not all(
                isinstance(m, str) for m in category_models
            ):
                raise ValueError(
                    f'llm-presets: providers["{name}"].media_model_choices entries must be string arrays'
                )
            media_models[category] = tuple(category_models)
        raw_model_capabilities = entry.get("model_capabilities", {})
        if raw_model_capabilities is None:
            raw_model_capabilities = {}
        if not isinstance(raw_model_capabilities, dict):
            raise ValueError(f'llm-presets: providers["{name}"].model_capabilities must be an object')
        model_capabilities: dict[str, dict[str, Any]] = {}
        for model_name, capability in raw_model_capabilities.items():
            if not isinstance(model_name, str) or not isinstance(capability, dict):
                raise ValueError(f'llm-presets: providers["{name}"].model_capabilities entries must be objects')
            model_capabilities[model_name] = dict(capability)
        out[name] = ProviderPreset(
            default_base_url=base.strip(),
            model_choices=tuple(models),
            display_name=display,
            auth_type=auth_type,
            api_mode=api_mode,
            capabilities=capabilities,
            model_discovery=model_discovery,
            media_discovery=media_discovery,
            model_metadata_fields=model_metadata_fields,
            media_model_choices=media_models or None,
            model_capabilities=model_capabilities or None,
        )
    return out


def _parse_string_tuple(entry: dict[str, Any], provider_name: str, field_name: str) -> tuple[str, ...]:
    raw = entry.get(field_name, [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f'llm-presets: providers["{provider_name}"].{field_name} must be a string array')
    return tuple(item.strip() for item in raw if item.strip())


def _parse_discovery_config(entry: dict[str, Any], provider_name: str, field_name: str) -> dict[str, Any] | None:
    raw = entry.get(field_name)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f'llm-presets: providers["{provider_name}"].{field_name} must be an object')
    discovery_type = raw.get("type")
    if not isinstance(discovery_type, str) or not discovery_type.strip():
        raise ValueError(f'llm-presets: providers["{provider_name}"].{field_name}.type is required')
    return {**raw, "type": discovery_type.strip()}


def load_llm_presets() -> LLMPresets:
    """Read and validate ``llm-presets.json`` shipped inside ``opensprite.config``."""
    path = resources.files("opensprite.config").joinpath("llm-presets.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("llm-presets.json must contain a JSON object")

    version = data.get("version", 1)
    if not isinstance(version, int):
        raise ValueError("llm-presets.json: version must be an integer")

    order = data.get("provider_order", [])
    if not isinstance(order, list) or not order or not all(isinstance(x, str) for x in order):
        raise ValueError("llm-presets.json: provider_order must be a non-empty string array")

    raw_providers = data.get("providers", {})
    if not isinstance(raw_providers, dict):
        raise ValueError("llm-presets.json: providers must be an object")

    providers = _parse_providers(raw_providers)
    for name in order:
        if name not in providers:
            raise ValueError(f'llm-presets.json: provider_order entry "{name}" missing from providers')

    return LLMPresets(version=version, provider_order=tuple(order), providers=providers)


def get_provider_profile(provider_id: str | None) -> ProviderPreset | None:
    """Return one bundled provider profile by id."""
    normalized = str(provider_id or "").strip()
    if not normalized:
        return None
    return load_llm_presets().providers.get(normalized)


def provider_default_base_url(provider_id: str | None) -> str:
    """Return a provider profile default base URL, or an empty string for unknown providers."""
    profile = get_provider_profile(provider_id)
    return profile.default_base_url if profile else ""


def provider_auth_type(provider_id: str | None) -> str:
    """Return a provider profile auth type, defaulting to API key auth."""
    profile = get_provider_profile(provider_id)
    return profile.auth_type if profile else "api_key"


def provider_api_mode(provider_id: str | None) -> str | None:
    """Return a provider profile API mode, if one is defined."""
    profile = get_provider_profile(provider_id)
    return profile.api_mode if profile else None


def provider_profile_defaults(
    provider_id: str | None,
    *,
    auth_type: str | None = "api_key",
    api_mode: str | None = None,
) -> ProviderProfileDefaults:
    """Return explicit values overlaid with bundled provider profile defaults."""
    normalized = str(provider_id or "").strip()
    explicit_auth_type = str(auth_type or "api_key").strip() or "api_key"
    if not normalized:
        if explicit_auth_type == "openai_codex_oauth":
            normalized = "openai-codex"
        elif explicit_auth_type == "github_copilot_oauth":
            normalized = "copilot"

    profile_auth_type = provider_auth_type(normalized)
    effective_auth_type = explicit_auth_type
    if effective_auth_type == "api_key" and profile_auth_type != "api_key":
        effective_auth_type = profile_auth_type

    profile_api_mode = provider_api_mode(normalized)
    effective_api_mode = api_mode or profile_api_mode
    default_base_url = provider_default_base_url(normalized)
    if normalized == "minimax" and effective_api_mode != profile_api_mode:
        default_base_url = ""

    return ProviderProfileDefaults(
        provider_id=normalized,
        auth_type=effective_auth_type,
        api_mode=effective_api_mode,
        default_base_url=default_base_url,
    )
