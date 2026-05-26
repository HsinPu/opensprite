"""Status, gateway, and config-validate command helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import typer


def start_gateway(*, config: str | None, run_gateway: Callable[..., None]) -> None:
    """Start the OpenSprite gateway with optional config override."""
    try:
        run_gateway(config_path=config)
    except (FileNotFoundError, ValueError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def build_config_validate_payload(
    config_path: Path,
    *,
    iter_channel_status: Callable[[object], list[tuple[str, bool]]],
) -> dict[str, object]:
    """Validate the main config and external split config files."""
    from ..config import Config

    payload: dict[str, object] = {
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "valid": False,
        "files": [],
    }

    if not config_path.exists():
        payload["error"] = f"Config file does not exist: {config_path}"
        return payload

    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw_data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        payload["error"] = str(exc)
        return payload

    if not isinstance(raw_data, dict):
        payload["error"] = f"Config file must contain a JSON object: {config_path}"
        return payload

    files = [
        ("main", config_path),
        ("llm.providers", Config.get_llm_providers_file_path(config_path, raw_data.get("llm", {}))),
        ("channels", Config.get_channels_file_path(config_path, raw_data)),
        ("search", Config.get_search_file_path(config_path, raw_data)),
        ("media", Config.get_media_file_path(config_path, raw_data)),
        ("messages", Config.get_messages_file_path(config_path, raw_data)),
        ("mcp_servers", Config.get_mcp_servers_file_path(config_path, raw_data.get("tools", {}))),
    ]

    file_payloads: list[dict[str, object]] = []
    missing_files: list[str] = []
    parse_errors: list[str] = []
    for label, file_path in files:
        exists = file_path.exists()
        entry: dict[str, object] = {
            "name": label,
            "path": str(file_path),
            "exists": exists,
            "valid_json": False,
        }
        if exists:
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                entry["valid_json"] = isinstance(loaded, dict)
                if not isinstance(loaded, dict):
                    entry["error"] = "JSON root must be an object"
                    parse_errors.append(f"{label}: JSON root must be an object")
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                entry["error"] = str(exc)
                parse_errors.append(f"{label}: {exc}")
        else:
            missing_files.append(label)
        file_payloads.append(entry)

    payload["files"] = file_payloads

    if missing_files:
        payload["error"] = "Missing config files: " + ", ".join(missing_files)
        return payload
    if parse_errors:
        payload["error"] = "; ".join(parse_errors)
        return payload

    try:
        loaded = Config.load(config_path)
    except (FileNotFoundError, ValueError) as exc:
        payload["error"] = str(exc)
        return payload

    payload.update(
        {
            "valid": True,
            "llm_default": loaded.llm.default,
            "enabled_channels": [name for name, enabled in iter_channel_status(loaded) if enabled],
            "search_enabled": loaded.search.enabled,
            "mcp_servers": sorted(loaded.tools.mcp_servers),
        }
    )
    return payload


def emit_config_validate(
    payload: dict[str, object],
    json_output: bool,
    *,
    format_presence: Callable[[bool], str],
) -> None:
    """Render config validation output."""
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.echo("OpenSprite Config Validation")
    typer.echo(f"Config: {payload['config_path']}")
    typer.echo(f"Exists: {format_presence(bool(payload.get('config_exists')))}")
    typer.echo(f"Valid: {format_presence(bool(payload.get('valid')))}")
    for entry in payload.get("files", []):
        if not isinstance(entry, dict):
            continue
        status = format_presence(bool(entry.get("exists")))
        valid_json = format_presence(bool(entry.get("valid_json")))
        typer.echo(f"- {entry.get('name')}: {entry.get('path')} [exists={status}, json={valid_json}]")
        error = entry.get("error")
        if isinstance(error, str) and error:
            typer.echo(f"  error: {error}")

    if payload.get("valid"):
        typer.echo(f"LLM default: {payload.get('llm_default') or '<unset>'}")
        enabled_channels = payload.get("enabled_channels") or []
        typer.echo("Enabled channels: " + (", ".join(enabled_channels) if enabled_channels else "none"))
        typer.echo(f"Search enabled: {format_presence(bool(payload.get('search_enabled')))}")
        typer.echo("MCP servers: " + (", ".join(payload.get("mcp_servers") or []) or "none"))
        return

    error = payload.get("error")
    if isinstance(error, str) and error:
        typer.echo(f"Error: {error}")


def config_validate_command(
    *,
    config_path: Path,
    json_output: bool,
    format_presence: Callable[[bool], str],
    iter_channel_status: Callable[[object], list[tuple[str, bool]]],
) -> None:
    """Validate the main config and all split external config files."""
    payload = build_config_validate_payload(config_path, iter_channel_status=iter_channel_status)
    emit_config_validate(payload, json_output, format_presence=format_presence)
    if not bool(payload.get("valid")):
        raise typer.Exit(code=1)


def emit_status(
    payload: dict[str, object],
    json_output: bool,
    *,
    format_presence: Callable[[bool], str],
) -> None:
    """Render status output in text or JSON format."""
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.echo("OpenSprite Status")
    typer.echo(f"Version: {payload['version']}")
    typer.echo(f"Config: {payload['config_path']} [{format_presence(bool(payload['config_exists']))}]")
    typer.echo(f"App Home: {payload['app_home']} [{format_presence(bool(payload['app_home_exists']))}]")
    typer.echo(
        "Workspace Root: "
        f"{payload['workspace_root']} [{format_presence(bool(payload['workspace_root_exists']))}]"
    )

    if not bool(payload["config_loaded"]):
        typer.echo(f"LLM Configured: {format_presence(bool(payload['llm_configured']))}")
        typer.echo("Channels: unavailable (config file missing)")
        hint = payload.get("hint")
        if isinstance(hint, str) and hint:
            typer.echo(f"Hint: {hint}")
        return

    provider = payload["provider"]
    storage = payload["storage"]
    search = payload["search"]
    channels = payload["channels"]
    enabled_channels = [name for name, enabled in channels.items() if enabled]

    typer.echo(f"LLM Configured: {format_presence(bool(payload['llm_configured']))}")
    typer.echo(
        "Provider: "
        f"{provider['name'] or '<unset>'} "
        f"(enabled={format_presence(bool(provider['enabled']))}, "
        f"api_key={format_presence(bool(provider['api_key_configured']))})"
    )
    typer.echo(f"Model: {provider['model']}")
    typer.echo(f"Storage: {storage['type']} -> {storage['path']}")
    typer.echo(
        "Search: "
        f"enabled={format_presence(bool(search['enabled']))} "
        f"backend={search['backend']} "
        f"(history_top_k={search['history_top_k']})"
    )
    typer.echo("Channels: " + (", ".join(enabled_channels) if enabled_channels else "none enabled"))


def _provider_has_configured_key(provider: object, *, provider_name: str, app_home: Path) -> bool:
    if bool(getattr(provider, "api_key", "")):
        return True
    if str(getattr(provider, "auth_type", "api_key") or "api_key") != "api_key":
        return False

    try:
        from ..auth.credentials import CredentialNotFoundError, resolve_credential
    except Exception:
        return bool(getattr(provider, "credential_id", ""))

    configured_provider = str(getattr(provider, "provider", "") or provider_name or "").strip()
    credential_id = str(getattr(provider, "credential_id", "") or "").strip()
    try:
        resolve_credential(
            provider=configured_provider,
            credential_id=credential_id or None,
            capability="llm.chat",
            app_home=app_home,
        )
        return True
    except CredentialNotFoundError:
        return False


def status_command(
    *,
    config_path: Path,
    json_output: bool,
    home_root: Path,
    version: str,
    format_presence: Callable[[bool], str],
    iter_channel_status: Callable[[object], list[tuple[str, bool]]],
) -> None:
    """Show OpenSprite configuration and runtime status."""
    from ..config import Config

    app_home = (home_root / ".opensprite").resolve()
    workspace_root = (app_home / "workspace").resolve()

    payload: dict[str, object] = {
        "version": version,
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "config_loaded": False,
        "app_home": str(app_home),
        "app_home_exists": app_home.exists(),
        "workspace_root": str(workspace_root),
        "workspace_root_exists": workspace_root.exists(),
        "llm_configured": False,
    }

    if not config_path.exists():
        payload["hint"] = "run `opensprite gateway`; it creates defaults, then configure OpenSprite from the Web UI Settings."
        emit_status(payload, json_output, format_presence=format_presence)
        return

    try:
        loaded = Config.load(config_path)
    except (FileNotFoundError, ValueError) as exc:
        if json_output:
            payload["error"] = str(exc)
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    selected_provider = loaded.llm.default if loaded.llm.default in loaded.llm.providers else None
    if selected_provider is not None:
        active_provider = loaded.llm.providers[selected_provider]
        provider_enabled = bool(getattr(active_provider, "enabled", False))
        provider_has_key = _provider_has_configured_key(
            active_provider,
            provider_name=selected_provider,
            app_home=config_path.parent,
        )
        model_name = active_provider.model or "<unset>"
    else:
        provider_enabled = False
        provider_has_key = False
        model_name = "<unset>"
    storage_path = Path(loaded.storage.path).expanduser()
    channels = {name: enabled for name, enabled in iter_channel_status(loaded)}

    payload.update(
        {
            "config_loaded": True,
            "llm_configured": loaded.is_llm_configured,
            "provider": {
                "name": selected_provider,
                "enabled": provider_enabled,
                "api_key_configured": provider_has_key,
                "model": model_name,
            },
            "storage": {
                "type": loaded.storage.type,
                "path": str(storage_path),
            },
            "search": {
                "enabled": loaded.search.enabled,
                "backend": loaded.search.backend,
                "history_top_k": loaded.search.history_top_k,
            },
            "channels": channels,
        }
    )
    emit_status(payload, json_output, format_presence=format_presence)
