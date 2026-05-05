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
    media_model_choices: dict[str, tuple[str, ...]] | None = None
    model_capabilities: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class LLMPresets:
    """Bundled llm-presets.json content."""

    version: int
    provider_order: tuple[str, ...]
    providers: dict[str, ProviderPreset]


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
        raw_capabilities = entry.get("model_capabilities", {})
        if raw_capabilities is None:
            raw_capabilities = {}
        if not isinstance(raw_capabilities, dict):
            raise ValueError(f'llm-presets: providers["{name}"].model_capabilities must be an object')
        capabilities: dict[str, dict[str, Any]] = {}
        for model_name, capability in raw_capabilities.items():
            if not isinstance(model_name, str) or not isinstance(capability, dict):
                raise ValueError(f'llm-presets: providers["{name}"].model_capabilities entries must be objects')
            capabilities[model_name] = dict(capability)
        out[name] = ProviderPreset(
            default_base_url=base.strip(),
            model_choices=tuple(models),
            display_name=display,
            media_model_choices=media_models or None,
            model_capabilities=capabilities or None,
        )
    return out


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
