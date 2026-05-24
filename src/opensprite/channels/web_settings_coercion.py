"""Settings coercion helpers for the web adapter."""

from __future__ import annotations

from typing import Any, Callable

from aiohttp import web
from pydantic import ValidationError

from ..config import ToolPermissionProfileOverrideConfig
from ..utils.url import join_url_path


def coerce_approval_mode(value: Any, *, approval_modes: set[str] | frozenset[str]) -> str | None:
    if value is None or value == "":
        return None
    mode = str(value or "").strip().lower()
    if mode not in approval_modes:
        raise web.HTTPBadRequest(text=f"approval_mode must be one of: {', '.join(sorted(approval_modes))}")
    return mode


def coerce_text_list(value: Any, *, field: str, default: list[str] | None = None) -> list[str]:
    if value is None or value == "":
        return list(default or [])
    if isinstance(value, str):
        candidates = value.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        raise web.HTTPBadRequest(text=f"{field} must be a list or comma-separated text")
    items: list[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def coerce_risk_level_list(
    value: Any,
    *,
    field: str,
    default: list[str] | None = None,
    all_risk_levels: set[str] | frozenset[str],
) -> list[str]:
    values = coerce_text_list(value, field=field, default=default)
    invalid = [item for item in values if item not in all_risk_levels]
    if invalid:
        raise web.HTTPBadRequest(text=f"{field} contains invalid risk level(s): {', '.join(invalid)}")
    return values


def coerce_permission_profile_overrides(
    value: Any,
    *,
    default: dict[str, ToolPermissionProfileOverrideConfig],
    all_risk_levels: set[str] | frozenset[str],
) -> dict[str, ToolPermissionProfileOverrideConfig]:
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(text="profile_overrides must be a JSON object")
    allowed_profiles = {"chat", "research", "coding", "media", "ops"}
    result: dict[str, ToolPermissionProfileOverrideConfig] = {}
    for profile, raw_override in value.items():
        profile_name = str(profile or "").strip().lower()
        if profile_name not in allowed_profiles:
            raise web.HTTPBadRequest(text=f"profile_overrides contains unknown profile: {profile}")
        if not isinstance(raw_override, dict):
            raise web.HTTPBadRequest(text=f"profile_overrides.{profile_name} must be a JSON object")
        try:
            override = ToolPermissionProfileOverrideConfig.model_validate(raw_override)
        except ValidationError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        invalid = sorted(
            (set(override.allowed_risk_levels) | set(override.denied_risk_levels) | set(override.approval_required_risk_levels))
            - all_risk_levels
        )
        if invalid:
            raise web.HTTPBadRequest(
                text=f"profile_overrides.{profile_name} contains invalid risk level(s): {', '.join(invalid)}"
            )
        result[profile_name] = override
    return result


def coerce_log_level(value: Any, *, default_log_level: str, log_levels: tuple[str, ...] | list[str]) -> str:
    level = str(value or default_log_level).strip().upper()
    if level not in log_levels:
        raise web.HTTPBadRequest(text=f"level must be one of: {', '.join(log_levels)}")
    return level


def coerce_positive_int(value: Any, *, field: str, default: int, minimum: int = 0, maximum: int = 3650) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"{field} must be an integer") from exc
    if number < minimum:
        raise web.HTTPBadRequest(text=f"{field} must be at least {minimum}")
    if number > maximum:
        raise web.HTTPBadRequest(text=f"{field} must be at most {maximum}")
    return number


def coerce_float_range(value: Any, *, field: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"{field} must be a number") from exc
    if number < minimum:
        raise web.HTTPBadRequest(text=f"{field} must be at least {minimum}")
    if number > maximum:
        raise web.HTTPBadRequest(text=f"{field} must be at most {maximum}")
    return number


def coerce_bool(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise web.HTTPBadRequest(text=f"{field} must be a boolean")


def coerce_browser_backend(value: Any, *, default_backend: str, backends: tuple[str, ...] | list[str]) -> str:
    backend = str(value or default_backend).strip() or default_backend
    if backend not in backends:
        raise web.HTTPBadRequest(text=f"backend must be one of: {', '.join(backends)}")
    return backend


def coerce_web_search_provider(value: Any, *, default_provider: str, providers: tuple[str, ...] | list[str]) -> str:
    provider = str(value or default_provider).strip().lower() or default_provider
    if provider not in providers:
        raise web.HTTPBadRequest(text=f"provider must be one of: {', '.join(providers)}")
    return provider


def coerce_web_search_freshness(value: Any, *, default_freshness: str, freshness_values: tuple[str, ...] | list[str]) -> str:
    freshness = str(value or default_freshness).strip().lower() or default_freshness
    if freshness not in freshness_values:
        raise web.HTTPBadRequest(text=f"freshness must be one of: {', '.join(freshness_values)}")
    return freshness


def normalize_searxng_engine_options(engines: Any) -> list[dict[str, Any]]:
    if not isinstance(engines, list):
        return []
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for engine in engines:
        if isinstance(engine, str):
            engine_id = engine.strip()
            label = engine_id
            shortcut = ""
            categories: list[str] = []
            enabled = None
        elif isinstance(engine, dict):
            engine_id = str(engine.get("name") or engine.get("id") or "").strip()
            label = str(engine.get("display_name") or engine.get("displayName") or engine_id).strip()
            shortcut = str(engine.get("shortcut") or "").strip()
            categories = coerce_text_list(engine.get("categories", []), field="categories", default=[])
            enabled = engine.get("enabled") if isinstance(engine.get("enabled"), bool) else None
        else:
            continue
        if not engine_id or engine_id in seen:
            continue
        seen.add(engine_id)
        options.append({"id": engine_id, "label": label or engine_id, "shortcut": shortcut, "categories": categories, "enabled": enabled})
    return options


def normalize_searxng_category_options(categories: Any) -> list[dict[str, str]]:
    if isinstance(categories, dict):
        candidates = list(categories.keys())
    else:
        candidates = categories
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for category in coerce_text_list(candidates, field="categories", default=[]):
        if category in seen:
            continue
        seen.add(category)
        options.append({"id": category, "label": category})
    return options


def searxng_options_payload(config_payload: dict[str, Any], *, url: str) -> dict[str, Any]:
    engines = normalize_searxng_engine_options(config_payload.get("engines"))
    categories = normalize_searxng_category_options(config_payload.get("categories"))
    if not categories:
        category_names: list[str] = []
        for engine in engines:
            category_names.extend(engine.get("categories") or [])
        categories = normalize_searxng_category_options(category_names)
    return {"url": url, "engines": engines, "categories": categories, "fallback": False, "warning": ""}


def fallback_searxng_options_payload(*, url: str, warning: str, fallback_engines: tuple[str, ...], fallback_categories: tuple[str, ...]) -> dict[str, Any]:
    return {
        "url": url,
        "engines": [{"id": engine, "label": engine, "shortcut": "", "categories": [], "enabled": None} for engine in fallback_engines],
        "categories": [{"id": category, "label": category} for category in fallback_categories],
        "fallback": True,
        "warning": warning,
    }


def searxng_config_url(searxng_url: str) -> str:
    base = str(searxng_url or "").strip().rstrip("/")
    if base.lower().endswith("/search"):
        base = base[:-len("/search")]
    return join_url_path(base, "/config")
