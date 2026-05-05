"""Shared provider/model settings helpers for Web settings."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from .llm_presets import ProviderPreset, load_llm_presets
from .schema import Config


OPENROUTER_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}
OPENROUTER_PROVIDER_SORTS = {"price", "throughput", "latency"}
MODEL_DISCOVERY_TIMEOUT_SECONDS = 8.0


class ProviderSettingsError(Exception):
    """Base error for provider settings operations."""


class ProviderSettingsValidationError(ProviderSettingsError):
    """Raised when a request is malformed."""


class ProviderSettingsNotFound(ProviderSettingsError):
    """Raised when a provider cannot be found."""


class ProviderSettingsConflict(ProviderSettingsError):
    """Raised when an operation would leave settings inconsistent."""


def load_json_dict(path: Path) -> dict[str, Any]:
    """Load a JSON object from disk."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ProviderSettingsValidationError(f"Config file must contain a JSON object: {path}")
    return data


def write_json_dict(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON object using the repository's standard formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_selected_provider(config_data: dict[str, Any], *, provider_order: tuple[str, ...]) -> str | None:
    """Return the currently selected provider, if valid."""
    llm = config_data.get("llm", {})
    providers = llm.get("providers", {}) if isinstance(llm, dict) else {}
    default = llm.get("default") if isinstance(llm, dict) else None
    if isinstance(default, str) and default in providers:
        return default

    for provider_name in provider_order:
        provider = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
        if isinstance(provider, dict) and (provider.get("enabled") or provider.get("api_key")):
            return provider_name
    return None


def get_provider_choices(config_data: dict[str, Any], *, provider_order: tuple[str, ...]) -> list[str]:
    """Build a stable provider selection list."""
    providers = config_data.get("llm", {}).get("providers", {})
    order_set = set(provider_order)
    ordered = list(provider_order)
    extras = sorted(name for name in providers if name not in order_set)
    return ordered + extras


def get_model_choices(
    current_model: str | None,
    *,
    model_choices: tuple[str, ...],
    custom_choice: str | None = None,
) -> tuple[list[str], str | None]:
    """Return model choices and the default selection for a provider."""
    choices = list(model_choices)
    if current_model and current_model not in choices:
        choices.insert(0, current_model)
    if custom_choice and custom_choice not in choices:
        choices.append(custom_choice)
    default = current_model or (choices[0] if choices else None)
    return choices, default


def _dedupe_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for model in models:
        normalized = str(model or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _read_json_url(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=MODEL_DISCOVERY_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _models_from_openai_compatible_payload(payload: dict[str, Any] | None) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return _dedupe_models([str(item.get("id") or "") for item in data if isinstance(item, dict)])


def fetch_openai_compatible_models(api_key: str, base_url: str) -> list[str]:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        return []
    candidates = [normalized]
    if normalized.endswith("/v1"):
        candidates.append(normalized[:-3].rstrip("/"))
    else:
        candidates.append(f"{normalized}/v1")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for candidate in _dedupe_models(candidates):
        models = _models_from_openai_compatible_payload(_read_json_url(f"{candidate}/models", headers=headers))
        if models:
            return models
    return []


def fetch_openrouter_models() -> list[str]:
    payload = _read_json_url("https://openrouter.ai/api/v1/models", headers={"Accept": "application/json"})
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        params = item.get("supported_parameters")
        if isinstance(params, list) and params and "tools" not in {str(param) for param in params}:
            continue
        out.append(model_id)
    return _dedupe_models(out)


def fetch_codex_models(app_home: str | Path | None = None) -> list[str]:
    try:
        from ..auth.codex import load_or_refresh_codex_token

        token = load_or_refresh_codex_token(app_home).access_token
    except Exception:
        return []
    payload = _read_json_url(
        "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug or item.get("supported_in_api") is False:
            continue
        visibility = str(item.get("visibility") or "").strip().lower()
        if visibility in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append((rank, slug))
    sortable.sort(key=lambda item: (item[0], item[1]))
    return _dedupe_models([slug for _, slug in sortable])


def discover_provider_models(
    provider_id: str,
    provider: dict[str, Any],
    preset: ProviderPreset | None,
    *,
    app_home: str | Path | None = None,
) -> tuple[list[str], str]:
    fallback = list(preset.model_choices if preset else ())
    preset_id = str(provider.get("provider") or provider_id or "").strip()
    live: list[str] = []
    if preset_id == "openai-codex":
        live = fetch_codex_models(app_home)
    elif preset_id == "openrouter":
        live = fetch_openrouter_models()
    elif preset_id in {"openai", "minimax"} or str(provider.get("base_url") or "").strip():
        live = fetch_openai_compatible_models(
            str(provider.get("api_key") or "").strip(),
            str(provider.get("base_url") or (preset.default_base_url if preset else "")).strip(),
        )
    if live:
        return _dedupe_models(live + fallback), "live"
    return fallback, "preset"


def prune_llm_providers(llm: dict[str, Any]) -> None:
    """Keep the default provider and any configured providers; drop empty shells."""
    providers = llm.get("providers")
    if not isinstance(providers, dict):
        return
    default = llm.get("default")
    if not isinstance(default, str) or not default.strip():
        return
    default = default.strip()
    keep: set[str] = {default}
    for name, provider in providers.items():
        if isinstance(provider, dict) and str(provider.get("api_key", "") or "").strip():
            keep.add(name)
    llm["providers"] = {name: dict(providers[name]) for name in sorted(keep) if name in providers}


def ensure_provider_entry(
    providers: dict[str, Any],
    provider_id: str,
    preset: ProviderPreset,
) -> dict[str, Any]:
    """Ensure one provider entry exists and has baseline fields."""
    existing = providers.get(provider_id)
    if not isinstance(existing, dict):
        existing = {}
        providers[provider_id] = existing

    existing.setdefault("api_key", "")
    existing.setdefault("model", "")
    existing.setdefault("base_url", preset.default_base_url)
    existing.setdefault("auth_type", preset.auth_type)
    if preset.api_mode:
        existing.setdefault("api_mode", preset.api_mode)
    existing.setdefault("enabled", False)
    if not str(existing.get("base_url", "") or "").strip():
        existing["base_url"] = preset.default_base_url
    return existing


def get_provider_preset_id(provider_id: str, provider: dict[str, Any], presets: Any) -> str | None:
    """Return the base preset id for a configured provider instance."""
    configured = str(provider.get("provider", "") or "").strip()
    if configured in presets.providers:
        return configured
    if provider_id in presets.providers:
        return provider_id
    return None


def make_provider_instance_id(base_provider_id: str, providers: dict[str, Any], display_name: str | None = None) -> str:
    """Create a stable id for an additional provider connection."""
    if base_provider_id not in providers:
        return base_provider_id
    slug_source = str(display_name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug_source).strip("_")
    if slug:
        candidate = f"{base_provider_id}_{slug}"
        if candidate not in providers:
            return candidate
    index = 2
    while f"{base_provider_id}_{index}" in providers:
        index += 1
    return f"{base_provider_id}_{index}"


def connect_provider_in_config(
    config_data: dict[str, Any],
    provider_id: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
    base_provider_id: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Connect or update a provider inside an in-memory config object."""
    presets = load_llm_presets()
    preset_id = base_provider_id or provider_id
    if preset_id not in presets.providers:
        raise ProviderSettingsNotFound(f"Unknown provider: {preset_id}")

    llm = config_data.setdefault("llm", {})
    providers = llm.setdefault("providers", {})
    preset = presets.providers[preset_id]
    provider = ensure_provider_entry(providers, provider_id, preset)
    provider["provider"] = preset_id
    provider["auth_type"] = preset.auth_type
    if preset.api_mode:
        provider["api_mode"] = preset.api_mode
    if preset_id == "openrouter":
        provider.setdefault("reasoning_enabled", True)
        provider.setdefault("reasoning_effort", "medium")
    normalized_name = str(display_name or "").strip()
    if normalized_name:
        provider["name"] = normalized_name

    normalized_key = str(api_key or "").strip()
    if normalized_key:
        provider["api_key"] = normalized_key
    elif preset.auth_type == "api_key" and not str(provider.get("api_key", "") or "").strip():
        raise ProviderSettingsValidationError("api_key is required when connecting a new provider")

    normalized_base_url = str(base_url or "").strip()
    if normalized_base_url:
        provider["base_url"] = normalized_base_url
    elif not str(provider.get("base_url", "") or "").strip():
        provider["base_url"] = preset.default_base_url

    provider.setdefault("model", "")
    provider["enabled"] = bool(provider.get("enabled", False))
    return provider


def select_model_in_config(
    config_data: dict[str, Any],
    provider_id: str,
    model: str,
    *,
    require_api_key: bool = True,
) -> dict[str, Any]:
    """Select the active provider/model inside an in-memory config object."""
    presets = load_llm_presets()
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise ProviderSettingsValidationError("model is required")

    llm = config_data.setdefault("llm", {})
    providers = llm.setdefault("providers", {})
    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        raise ProviderSettingsConflict("Provider must be connected before selecting a model")
    preset_id = get_provider_preset_id(provider_id, provider, presets)
    if preset_id is None:
        raise ProviderSettingsNotFound(f"Unknown provider: {provider_id}")
    if require_api_key and preset_id:
        preset = presets.providers[preset_id]
        if preset.auth_type == "api_key" and not str(provider.get("api_key", "") or "").strip():
            raise ProviderSettingsConflict("Provider must be connected before selecting a model")

    preset = presets.providers[preset_id]
    if not str(provider.get("base_url", "") or "").strip():
        provider["base_url"] = preset.default_base_url
    provider["model"] = normalized_model
    llm["default"] = provider_id
    for name, item in providers.items():
        if isinstance(item, dict):
            item["enabled"] = name == provider_id
    return provider


def public_openrouter_options(provider: dict[str, Any]) -> dict[str, Any]:
    """Return OpenRouter request options safe for settings APIs."""
    return {
        "reasoning_enabled": bool(provider.get("reasoning_enabled", True)),
        "reasoning_effort": provider.get("reasoning_effort", "medium"),
        "reasoning_max_tokens": provider.get("reasoning_max_tokens"),
        "reasoning_exclude": bool(provider.get("reasoning_exclude", False)),
        "provider_sort": provider.get("provider_sort"),
        "require_parameters": bool(provider.get("require_parameters", False)),
    }


def is_provider_connected(provider: dict[str, Any], preset: ProviderPreset | None) -> bool:
    """Return whether a provider instance is configured enough for model selection."""
    if not isinstance(provider, dict):
        return False
    if not provider:
        return False
    if preset and preset.auth_type != "api_key":
        return True
    return bool(str(provider.get("api_key", "") or "").strip())


def update_openrouter_options(provider: dict[str, Any], body: dict[str, Any]) -> None:
    """Validate and update optional OpenRouter request settings."""
    if "reasoning_enabled" in body:
        provider["reasoning_enabled"] = bool(body["reasoning_enabled"])
    if "reasoning_effort" in body:
        value = body["reasoning_effort"]
        if value is None or str(value).strip() == "":
            provider["reasoning_effort"] = None
        elif str(value) in OPENROUTER_REASONING_EFFORTS:
            provider["reasoning_effort"] = str(value)
        else:
            raise ProviderSettingsValidationError("reasoning_effort must be one of minimal, low, medium, high, or xhigh")
    if "reasoning_max_tokens" in body:
        value = body["reasoning_max_tokens"]
        if value is None or str(value).strip() == "":
            provider["reasoning_max_tokens"] = None
        else:
            try:
                normalized = int(value)
            except (TypeError, ValueError) as exc:
                raise ProviderSettingsValidationError("reasoning_max_tokens must be a positive integer") from exc
            if normalized < 1:
                raise ProviderSettingsValidationError("reasoning_max_tokens must be a positive integer")
            provider["reasoning_max_tokens"] = normalized
    if "reasoning_exclude" in body:
        provider["reasoning_exclude"] = bool(body["reasoning_exclude"])
    if "provider_sort" in body:
        value = body["provider_sort"]
        if value is None or str(value).strip() == "":
            provider["provider_sort"] = None
        elif str(value) in OPENROUTER_PROVIDER_SORTS:
            provider["provider_sort"] = str(value)
        else:
            raise ProviderSettingsValidationError("provider_sort must be one of price, throughput, or latency")
    if "require_parameters" in body:
        provider["require_parameters"] = bool(body["require_parameters"])


class ProviderSettingsService:
    """Read and mutate provider/model settings on disk."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).expanduser().resolve()

    def _load_main_data(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise ProviderSettingsNotFound(f"Config file not found: {self.config_path}")
        return load_json_dict(self.config_path)

    def _load_state(self) -> tuple[dict[str, Any], dict[str, Any], Any]:
        main_data = self._load_main_data()
        loaded = Config.from_json(self.config_path)
        providers = {name: provider.model_dump() for name, provider in loaded.llm.providers.items()}
        return main_data, providers, loaded

    def _persist_llm_state(self, main_data: dict[str, Any], providers: dict[str, Any]) -> None:
        llm_data = main_data.setdefault("llm", {})
        if not isinstance(llm_data, dict):
            raise ProviderSettingsValidationError("llm config must be an object")
        llm_data.pop("providers", None)
        llm_data.setdefault("providers_file", "llm.providers.json")
        write_json_dict(self.config_path, main_data)
        Config.ensure_llm_providers_file(self.config_path, main_data)
        Config.write_llm_providers_file(self.config_path, providers, llm_data)

    @staticmethod
    def _display_name(provider_id: str, preset: ProviderPreset | None = None, provider: dict[str, Any] | None = None) -> str:
        configured_name = str((provider or {}).get("name", "") or "").strip()
        if configured_name:
            return configured_name
        if preset and preset.display_name:
            return preset.display_name
        return provider_id.replace("_", " ").replace("-", " ").title()

    def list_providers(self) -> dict[str, Any]:
        """Return configured and available providers without leaking API keys."""
        main_data, providers, loaded = self._load_state()
        presets = load_llm_presets()
        default_provider = loaded.llm.default
        connected: list[dict[str, Any]] = []

        for provider_id in get_provider_choices({"llm": {"providers": providers}}, provider_order=presets.provider_order):
            provider = providers.get(provider_id, {})
            preset_id = get_provider_preset_id(provider_id, provider, presets)
            preset = presets.providers.get(preset_id) if preset_id else None
            if not is_provider_connected(provider, preset):
                continue
            connected.append(
                {
                    "id": provider_id,
                    "provider": preset_id or provider_id,
                    "name": self._display_name(provider_id, preset, provider),
                    "preset_name": self._display_name(preset_id or provider_id, preset),
                    "base_url": provider.get("base_url") or (preset.default_base_url if preset else None),
                    "model": provider.get("model") or "",
                    "api_key_configured": bool(provider.get("api_key")),
                    "auth_type": provider.get("auth_type") or (preset.auth_type if preset else "api_key"),
                    "requires_api_key": (preset.auth_type if preset else "api_key") == "api_key",
                    "is_default": provider_id == default_provider,
                    "enabled": bool(provider.get("enabled")),
                    "options": public_openrouter_options(provider) if preset_id == "openrouter" else {},
                }
            )

        available = [
            {
                "id": provider_id,
                "name": self._display_name(provider_id, presets.providers[provider_id]),
                "default_base_url": presets.providers[provider_id].default_base_url,
                "auth_type": presets.providers[provider_id].auth_type,
                "api_mode": presets.providers[provider_id].api_mode,
                "requires_api_key": presets.providers[provider_id].auth_type == "api_key",
                "model_choices": list(presets.providers[provider_id].model_choices),
                "connected_count": sum(1 for provider in connected if provider.get("provider") == provider_id),
            }
            for provider_id in presets.provider_order
        ]

        return {
            "default_provider": default_provider,
            "connected": connected,
            "available": available,
            "restart_required": False,
            "config_path": str(self.config_path),
            "providers_file": str(Config.get_llm_providers_file_path(self.config_path, main_data.get("llm", {}))),
        }

    def connect_provider(self, provider_id: str, *, api_key: str | None, base_url: str | None = None, name: str | None = None) -> dict[str, Any]:
        """Connect or update one provider without selecting a model."""
        main_data, providers, loaded = self._load_state()
        instance_id = make_provider_instance_id(provider_id, providers, name)
        config_data = {"llm": {"providers": providers, "default": loaded.llm.default}}
        provider = connect_provider_in_config(
            config_data,
            instance_id,
            api_key=api_key,
            base_url=base_url,
            base_provider_id=provider_id,
            display_name=name,
        )
        self._persist_llm_state(main_data, providers)
        preset = load_llm_presets().providers[provider_id]
        return {
            "ok": True,
            "provider": {
                "id": instance_id,
                "provider": provider_id,
                "name": self._display_name(instance_id, preset, provider),
                "preset_name": self._display_name(provider_id, preset),
                "base_url": provider.get("base_url") or preset.default_base_url,
                "model": provider.get("model") or "",
                "api_key_configured": bool(provider.get("api_key")),
                "auth_type": provider.get("auth_type") or preset.auth_type,
                "requires_api_key": preset.auth_type == "api_key",
                "is_default": instance_id == loaded.llm.default,
                "enabled": bool(provider.get("enabled")),
                "options": public_openrouter_options(provider) if provider_id == "openrouter" else {},
            },
            "restart_required": False,
        }

    def update_provider_options(self, provider_id: str, options: dict[str, Any]) -> dict[str, Any]:
        """Update optional request settings for a connected provider."""
        main_data, providers, _loaded = self._load_state()
        provider = providers.get(provider_id)
        presets = load_llm_presets()
        preset_id = get_provider_preset_id(provider_id, provider if isinstance(provider, dict) else {}, presets)
        preset = presets.providers.get(preset_id) if preset_id else None
        if not isinstance(provider, dict) or not is_provider_connected(provider, preset):
            raise ProviderSettingsNotFound(f"Provider is not connected: {provider_id}")
        if preset_id != "openrouter":
            raise ProviderSettingsValidationError("OpenRouter request options are only available for OpenRouter providers")

        update_openrouter_options(provider, options)
        self._persist_llm_state(main_data, providers)
        return {
            "ok": True,
            "provider_id": provider_id,
            "options": public_openrouter_options(provider),
            "restart_required": bool(provider.get("enabled")),
        }

    def disconnect_provider(self, provider_id: str) -> dict[str, Any]:
        """Disconnect one provider, clearing the active model when needed."""
        main_data, providers, loaded = self._load_state()
        provider = providers.get(provider_id)
        presets = load_llm_presets()
        preset_id = get_provider_preset_id(provider_id, provider if isinstance(provider, dict) else {}, presets)
        preset = presets.providers.get(preset_id) if preset_id else None
        if not isinstance(provider, dict) or not is_provider_connected(provider, preset):
            raise ProviderSettingsNotFound(f"Provider is not connected: {provider_id}")

        was_default = provider_id == loaded.llm.default
        providers.pop(provider_id, None)
        if was_default:
            llm_data = main_data.setdefault("llm", {})
            llm_data["default"] = None
            for item in providers.values():
                if isinstance(item, dict):
                    item["enabled"] = False
        self._persist_llm_state(main_data, providers)
        return {"ok": True, "provider_id": provider_id, "restart_required": was_default}

    def list_models(self) -> dict[str, Any]:
        """Return selectable models for connected providers."""
        _, providers, loaded = self._load_state()
        presets = load_llm_presets()
        out: list[dict[str, Any]] = []
        for provider_id in get_provider_choices({"llm": {"providers": providers}}, provider_order=presets.provider_order):
            provider = providers.get(provider_id, {})
            preset_id = get_provider_preset_id(provider_id, provider, presets)
            preset = presets.providers.get(preset_id) if preset_id else None
            if not is_provider_connected(provider, preset):
                continue
            discovered_models, model_source = discover_provider_models(
                provider_id,
                provider,
                preset,
                app_home=self.config_path.parent,
            )
            choices, _ = get_model_choices(
                str(provider.get("model") or "") or None,
                model_choices=tuple(discovered_models),
            )
            out.append(
                {
                    "id": provider_id,
                    "provider": preset_id or provider_id,
                    "name": self._display_name(provider_id, preset, provider),
                    "preset_name": self._display_name(preset_id or provider_id, preset),
                    "is_connected": True,
                    "is_default": provider_id == loaded.llm.default,
                    "selected_model": provider.get("model") or "",
                    "models": choices,
                    "model_source": model_source,
                    "model_capabilities": (preset.model_capabilities or {}) if preset else {},
                    "options": public_openrouter_options(provider) if preset_id == "openrouter" else {},
                    "supports_custom_model": True,
                }
            )

        active = providers.get(loaded.llm.default or "", {}) if loaded.llm.default else {}
        active_model = active.get("model") if isinstance(active, dict) else None
        return {
            "default_provider": loaded.llm.default,
            "active_model": active_model or "",
            "providers": out,
            "restart_required": False,
        }

    def select_model(self, provider_id: str, model: str) -> dict[str, Any]:
        """Select the active provider/model and persist it."""
        main_data, providers, _loaded = self._load_state()
        config_data = {"llm": {"providers": providers, "default": main_data.get("llm", {}).get("default")}}
        select_model_in_config(config_data, provider_id, model)
        llm_data = main_data.setdefault("llm", {})
        llm_data["default"] = provider_id
        self._persist_llm_state(main_data, providers)
        return {
            "ok": True,
            "provider_id": provider_id,
            "model": str(model).strip(),
            "restart_required": True,
        }
