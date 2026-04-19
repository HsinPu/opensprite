"""Onboarding helpers for the OpenSprite CLI."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import questionary
except ModuleNotFoundError:  # pragma: no cover - exercised when dependency is absent
    questionary = None

from ..config import Config
from ..context.paths import (
    BOOTSTRAP_DIRNAME,
    MEMORY_DIRNAME,
    SKILLS_DIRNAME,
    WORKSPACE_DIRNAME,
    sync_templates,
)


LOGS_DIRNAME = "logs"
SKIP_CHOICE = "Skip"
CUSTOM_MODEL_CHOICE = "Custom..."
PROVIDER_ORDER = ("openrouter", "openai", "minimax")
PROVIDER_MODEL_CHOICES = {
    "openrouter": [
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-haiku",
        "google/gemini-2.0-flash-001",
    ],
    "openai": [
        "gpt-4.1-mini",
        "gpt-4o-mini",
        "gpt-4.1",
    ],
    "minimax": [
        "MiniMax-M2.5",
    ],
}


@dataclass
class OnboardResult:
    """Structured result for an onboarding run."""

    config_path: Path
    app_home: Path
    created_config: bool = False
    refreshed_config: bool = False
    reset_config: bool = False
    created_dirs: list[Path] = field(default_factory=list)
    template_files: list[str] = field(default_factory=list)
    interactive: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key_configured: bool = False
    channel_name: str | None = None
    channel_token_configured: bool = False


def _resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve a config path or fall back to the default app config."""
    if config_path is None:
        return (Path.home() / ".opensprite" / "opensprite.json").resolve()
    return Path(config_path).expanduser().resolve()


def _ensure_dir(path: Path, created_dirs: list[Path]) -> Path:
    """Ensure a directory exists while tracking newly created paths."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        created_dirs.append(path)
    return path


def _merge_missing_defaults(existing: Any, defaults: Any) -> Any:
    """Recursively fill missing keys from defaults without overwriting values."""
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return existing

    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
        else:
            merged[key] = _merge_missing_defaults(merged[key], value)
    return merged


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file as a dictionary."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON dictionary to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _prompt_choice(prompt: str, choices: list[str], default: str | None = None) -> str:
    """Prompt the user to choose one item from a select menu."""
    q = _get_questionary()
    answer = q.select(
        prompt,
        choices=list(choices),
        default=default,
        qmark=">",
    ).ask()
    if answer is None:
        raise KeyboardInterrupt
    return str(answer)


def _prompt_text(prompt: str, default: str | None = None, *, allow_empty: bool = True) -> str:
    """Prompt for plain text input."""
    q = _get_questionary()
    while True:
        value = q.text(prompt, default=default or "", qmark=">").ask()
        if value is None:
            raise KeyboardInterrupt
        value = value.strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ""


def _prompt_visible_value(prompt: str, current_value: str = "", *, required: bool = False) -> str:
    """Prompt for a visible value while allowing Enter to keep the current one."""
    q = _get_questionary()
    instruction = prompt
    if current_value:
        instruction = f"{prompt} (press Enter to keep current value)"
    elif not required:
        instruction = f"{prompt} (press Enter to skip)"

    while True:
        value = q.text(instruction, default="", qmark=">").ask()
        if value is None:
            raise KeyboardInterrupt
        value = value.strip()
        if value:
            return value
        if current_value:
            return current_value
        if not required:
            return ""


def _prompt_yes_no(prompt: str, default: bool) -> bool:
    """Prompt for a yes/no answer."""
    q = _get_questionary()
    value = q.confirm(prompt, default=default, qmark=">").ask()
    if value is None:
        raise KeyboardInterrupt
    return bool(value)


def _get_questionary():
    """Return questionary or raise a clear error when it's unavailable."""
    if questionary is None:
        raise RuntimeError(
            "Interactive onboarding requires the `questionary` dependency. "
            "Reinstall OpenSprite so interactive prompts are available, or run `opensprite onboard --no-input`."
        )
    return questionary


def _require_tty() -> None:
    """Ensure interactive onboarding only runs on a real terminal."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError(
            "Interactive onboarding requires a TTY. Re-run with `opensprite onboard --no-input` for automation."
        )


def _prepare_config_data(result: OnboardResult, config_path: Path, force: bool) -> dict[str, Any]:
    """Create or refresh the config scaffold and return the working data."""
    defaults = Config.load_template_data()
    if config_path.exists():
        existing = _load_json(config_path)
        if force:
            data = copy.deepcopy(defaults)
            result.reset_config = True
        else:
            data = _merge_missing_defaults(existing, defaults)
            if data != existing:
                result.refreshed_config = True
    else:
        data = copy.deepcopy(defaults)
        result.created_config = True

    _persist_config_data(config_path, data)
    loaded = Config.from_json(config_path)
    hydrated = copy.deepcopy(data)
    hydrated.setdefault("llm", {})["providers"] = {
        name: provider.model_dump() for name, provider in loaded.llm.providers.items()
    }
    hydrated["channels"] = loaded.channels.model_dump()
    hydrated["search"] = loaded.search.model_dump()
    hydrated["vision"] = loaded.vision.model_dump()
    hydrated["speech"] = loaded.speech.model_dump()
    hydrated["video"] = loaded.video.model_dump()
    return hydrated


def _persist_config_data(config_path: Path, config_data: dict[str, Any]) -> None:
    """Persist config data while keeping external config sections split out."""
    main_data = copy.deepcopy(config_data)
    llm_data = main_data.get("llm")
    providers_data = llm_data.pop("providers", None) if isinstance(llm_data, dict) else None
    channels_data = main_data.pop("channels", None)
    search_data = main_data.pop("search", None)
    vision_data = main_data.pop("vision", None)
    speech_data = main_data.pop("speech", None)
    video_data = main_data.pop("video", None)

    _write_json(config_path, main_data)
    Config.ensure_llm_providers_file(config_path, main_data)
    if isinstance(providers_data, dict):
        Config.write_llm_providers_file(config_path, providers_data, llm_data)
    Config.ensure_channels_file(config_path, main_data)
    if isinstance(channels_data, dict):
        Config.write_channels_file(config_path, channels_data, main_data)
    Config.ensure_search_file(config_path, main_data)
    if isinstance(search_data, dict):
        Config.write_search_file(config_path, search_data, main_data)
    Config.ensure_media_file(config_path, {**main_data, "vision": vision_data, "speech": speech_data, "video": video_data})
    if any(isinstance(section, dict) for section in (vision_data, speech_data, video_data)):
        Config.write_media_file(
            config_path,
            {
                "vision": vision_data if isinstance(vision_data, dict) else {},
                "speech": speech_data if isinstance(speech_data, dict) else {},
                "video": video_data if isinstance(video_data, dict) else {},
            },
            main_data,
        )
    Config.ensure_mcp_servers_file(config_path, main_data)


def _get_selected_provider(config_data: dict[str, Any]) -> str | None:
    """Return the currently selected provider, if valid."""
    llm = config_data.get("llm", {})
    providers = llm.get("providers", {})
    default = llm.get("default")
    if isinstance(default, str) and default in providers:
        return default

    for provider_name in PROVIDER_ORDER:
        provider = providers.get(provider_name, {})
        if isinstance(provider, dict) and (provider.get("enabled") or provider.get("api_key")):
            return provider_name
    return None


def _get_provider_choices(config_data: dict[str, Any]) -> list[str]:
    """Build the provider selection list with a stable order."""
    providers = config_data.get("llm", {}).get("providers", {})
    ordered = [name for name in PROVIDER_ORDER if name in providers]
    extras = sorted(name for name in providers if name not in ordered)
    return ordered + extras


def _get_model_choices(provider_name: str, current_model: str | None) -> tuple[list[str], str | None]:
    """Return model choices and the default selection for a provider."""
    choices = list(PROVIDER_MODEL_CHOICES.get(provider_name, []))
    if current_model and current_model not in choices:
        choices.insert(0, current_model)
    if CUSTOM_MODEL_CHOICE not in choices:
        choices.append(CUSTOM_MODEL_CHOICE)
    default = current_model or (choices[0] if choices else None)
    return choices, default


def _get_selected_channel(config_data: dict[str, Any]) -> str | None:
    """Return the currently enabled external channel, if any."""
    channels = config_data.get("channels", {})
    for channel_name, channel_data in channels.items():
        if channel_name == "console":
            continue
        if isinstance(channel_data, dict) and channel_data.get("enabled"):
            return channel_name
    return None


def _get_channel_choices(config_data: dict[str, Any]) -> list[str]:
    """Build the channel selection list with console excluded."""
    channels = config_data.get("channels", {})
    ordered = [name for name in channels if name != "console"]
    return ordered


def _show_summary(config_data: dict[str, Any]) -> None:
    """Print a short configuration summary before saving."""
    llm = config_data.get("llm", {})
    providers = llm.get("providers", {})
    provider_name = _get_selected_provider(config_data)
    provider = providers.get(provider_name, {}) if provider_name else {}
    channels = config_data.get("channels", {})
    channel_name = _get_selected_channel(config_data)
    channel = channels.get(channel_name, {}) if channel_name else {}

    print("\nOpenSprite configuration summary")
    print(f"- LLM provider: {provider_name or '<unset>'}")
    print(f"- Model: {provider.get('model') or '<unset>'}")
    print(f"- API key: {'configured' if provider.get('api_key') else 'not set'}")
    print(f"- Channel: {channel_name or '<unset>'}")
    print(f"- Channel token: {'configured' if channel.get('token') else 'not set'}")
    print("")


def _run_interactive_setup(config_data: dict[str, Any]) -> dict[str, Any]:
    """Interactively collect the minimum required runtime settings."""
    updated = copy.deepcopy(config_data)
    llm = updated.setdefault("llm", {})
    providers = llm.setdefault("providers", {})
    for provider_name in PROVIDER_ORDER:
        providers.setdefault(provider_name, {})

    current_provider = _get_selected_provider(updated)
    provider_choice = _prompt_choice(
        "Choose the LLM provider you want OpenSprite to use:",
        _get_provider_choices(updated) + [SKIP_CHOICE],
        default=current_provider,
    )

    provider_name = current_provider
    if provider_choice != SKIP_CHOICE:
        provider_name = provider_choice
        llm["default"] = provider_name
        for name, provider in providers.items():
            if isinstance(provider, dict):
                provider["enabled"] = name == provider_name

        selected = providers[provider_name]
        if not isinstance(selected, dict):
            raise ValueError(f"Invalid provider configuration for {provider_name}")

        model_choices, default_model = _get_model_choices(provider_name, selected.get("model"))
        model_choice = _prompt_choice(
            f"Choose the model for {provider_name}:",
            model_choices,
            default=default_model,
        )
        if model_choice == CUSTOM_MODEL_CHOICE:
            selected["model"] = _prompt_text("Custom model", default=selected.get("model"), allow_empty=False)
        else:
            selected["model"] = model_choice
        selected["api_key"] = _prompt_visible_value(
            "API key",
            str(selected.get("api_key", "")),
            required=False,
        )

    channels = updated.setdefault("channels", {})
    channel_default = _get_selected_channel(updated)
    channel_choice = _prompt_choice(
        "Choose the chat channel to configure:",
        _get_channel_choices(updated) + [SKIP_CHOICE],
        default=channel_default,
    )
    if channel_choice != SKIP_CHOICE:
        for name, channel in channels.items():
            if name == "console" or not isinstance(channel, dict):
                continue
            channel["enabled"] = name == channel_choice

        selected_channel = channels.get(channel_choice, {})
        if isinstance(selected_channel, dict) and "token" in selected_channel:
            token_label = f"{channel_choice.capitalize()} token"
            selected_channel["token"] = _prompt_visible_value(
                token_label,
                str(selected_channel.get("token", "")),
                required=False,
            )

    _show_summary(updated)
    if not _prompt_yes_no("Save these settings?", True):
        print("Interactive changes discarded; keeping the current config file.\n")
        return config_data

    return updated


def _apply_result_snapshot(result: OnboardResult, config_data: dict[str, Any], interactive: bool) -> None:
    """Populate summary fields on the onboarding result."""
    llm = config_data.get("llm", {})
    providers = llm.get("providers", {})
    provider_name = _get_selected_provider(config_data)
    provider = providers.get(provider_name, {}) if provider_name else {}
    channels = config_data.get("channels", {})

    result.interactive = interactive
    result.llm_provider = provider_name
    if isinstance(provider, dict):
        result.llm_model = provider.get("model") or None
    else:
        result.llm_model = None
    result.llm_api_key_configured = bool(provider.get("api_key")) if isinstance(provider, dict) else False
    selected_channel = _get_selected_channel(config_data)
    result.channel_name = selected_channel
    selected_channel_data = channels.get(selected_channel, {}) if selected_channel else {}
    if isinstance(selected_channel_data, dict):
        result.channel_token_configured = bool(selected_channel_data.get("token"))
    else:
        result.channel_token_configured = False


def run_onboard(
    config_path: str | Path | None = None,
    *,
    force: bool = False,
    interactive: bool = True,
) -> OnboardResult:
    """Initialize or refresh the default OpenSprite app directories and config."""
    resolved_config = _resolve_config_path(config_path)
    app_home = (Path.home() / ".opensprite").resolve()
    result = OnboardResult(config_path=resolved_config, app_home=app_home)

    _ensure_dir(app_home, result.created_dirs)
    _ensure_dir(app_home / LOGS_DIRNAME, result.created_dirs)
    _ensure_dir(app_home / BOOTSTRAP_DIRNAME, result.created_dirs)
    _ensure_dir(app_home / MEMORY_DIRNAME, result.created_dirs)
    _ensure_dir(app_home / SKILLS_DIRNAME, result.created_dirs)
    _ensure_dir(app_home / WORKSPACE_DIRNAME, result.created_dirs)

    config_data = _prepare_config_data(result, resolved_config, force)
    result.template_files = sync_templates(app_home)

    if interactive:
        _require_tty()
        try:
            updated = _run_interactive_setup(config_data)
        except (EOFError, KeyboardInterrupt) as exc:
            raise RuntimeError("Interactive onboarding cancelled.") from exc
        if updated != config_data:
            _persist_config_data(resolved_config, updated)
        config_data = updated

    _apply_result_snapshot(result, config_data, interactive=interactive)
    return result
