"""Shared media model settings helpers for Web settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .llm_presets import load_llm_presets
from .provider_settings import (
    ProviderSettingsError,
    ProviderSettingsNotFound,
    ProviderSettingsValidationError,
    get_model_choices,
    get_provider_choices,
    get_provider_preset_id,
    load_json_dict,
)
from .schema import Config, SpeechConfig, VideoConfig, VisionConfig


MEDIA_SECTIONS = {
    "vision": VisionConfig,
    "speech": SpeechConfig,
    "video": VideoConfig,
}


class MediaSettingsService:
    """Read and mutate media model settings on disk."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).expanduser().resolve()

    def _load_main_data(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise ProviderSettingsNotFound(f"Config file not found: {self.config_path}")
        return load_json_dict(self.config_path)

    def _load_state(self) -> tuple[dict[str, Any], dict[str, Any], Config]:
        main_data = self._load_main_data()
        loaded = Config.from_json(self.config_path)
        providers = {name: provider.model_dump() for name, provider in loaded.llm.providers.items()}
        return main_data, providers, loaded

    @staticmethod
    def _section_payload(category: str, config: Any, providers: dict[str, Any]) -> dict[str, Any]:
        provider_id = ""
        for candidate_id, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            if (
                str(provider.get("api_key", "") or "") == str(config.api_key or "")
                and str(provider.get("base_url", "") or "") == str(config.base_url or "")
                and (provider.get("provider") or candidate_id) == config.provider
            ):
                provider_id = candidate_id
                break
        return {
            "category": category,
            "enabled": bool(config.enabled),
            "provider": config.provider,
            "provider_id": provider_id,
            "model": config.model,
            "base_url": config.base_url,
            "api_key_configured": bool(config.api_key),
        }

    def list_media(self) -> dict[str, Any]:
        """Return media model settings without leaking API keys."""
        main_data, providers, loaded = self._load_state()
        presets = load_llm_presets()
        provider_choices = []
        for provider_id in get_provider_choices({"llm": {"providers": providers}}, provider_order=presets.provider_order):
            provider = providers.get(provider_id, {})
            if not isinstance(provider, dict) or not str(provider.get("api_key", "") or "").strip():
                continue
            preset_id = get_provider_preset_id(provider_id, provider, presets)
            preset = presets.providers.get(preset_id) if preset_id else None
            choices, selected = get_model_choices(
                str(provider.get("model") or "") or None,
                model_choices=preset.model_choices if preset else (),
            )
            provider_choices.append(
                {
                    "id": provider_id,
                    "provider": preset_id or provider_id,
                    "name": str(provider.get("name") or "").strip() or (preset.display_name if preset else provider_id),
                    "model": selected or "",
                    "models": choices,
                }
            )

        return {
            "sections": {
                "vision": self._section_payload("vision", loaded.vision or VisionConfig(), providers),
                "speech": self._section_payload("speech", loaded.speech or SpeechConfig(), providers),
                "video": self._section_payload("video", loaded.video or VideoConfig(), providers),
            },
            "providers": provider_choices,
            "restart_required": False,
            "media_file": str(Config.get_media_file_path(self.config_path, main_data)),
        }

    def update_media(self, category: str, *, enabled: bool, provider_id: str | None, model: str | None) -> dict[str, Any]:
        """Update one media model category."""
        if category not in MEDIA_SECTIONS:
            raise ProviderSettingsValidationError(f"Unknown media category: {category}")

        main_data, providers, _loaded = self._load_state()
        media_path = Config.ensure_media_file(self.config_path, main_data)
        media_data = load_json_dict(media_path)
        current = media_data.get(category, {}) if isinstance(media_data.get(category), dict) else {}
        next_section = dict(current)
        next_section["enabled"] = bool(enabled)

        if enabled:
            normalized_provider_id = str(provider_id or "").strip()
            normalized_model = str(model or "").strip()
            if not normalized_provider_id:
                raise ProviderSettingsValidationError("provider_id is required when media model is enabled")
            if not normalized_model:
                raise ProviderSettingsValidationError("model is required when media model is enabled")
            provider = providers.get(normalized_provider_id)
            if not isinstance(provider, dict) or not str(provider.get("api_key", "") or "").strip():
                raise ProviderSettingsNotFound(f"Provider is not connected: {normalized_provider_id}")
            preset_id = str(provider.get("provider") or normalized_provider_id).strip()
            next_section.update(
                {
                    "provider": preset_id,
                    "api_key": provider.get("api_key", ""),
                    "model": normalized_model,
                    "base_url": provider.get("base_url"),
                }
            )
        else:
            next_section.setdefault("provider", current.get("provider") or "minimax")
            next_section.setdefault("api_key", current.get("api_key") or "")
            next_section.setdefault("model", current.get("model") or "")
            next_section.setdefault("base_url", current.get("base_url"))

        media_data[category] = MEDIA_SECTIONS[category](**next_section).model_dump()
        Config.write_media_file(self.config_path, media_data, main_data)
        return {"ok": True, "category": category, "restart_required": True, "media": self.list_media()}
