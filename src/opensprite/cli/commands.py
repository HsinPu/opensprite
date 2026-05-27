"""CLI entrypoints for OpenSprite."""

from __future__ import annotations

import json
from pathlib import Path
import platform

import typer

from .. import __version__
from ..context.paths import get_tool_workspace
from ..cron import CronService
from ..runtime import gateway as run_gateway
from . import (
    commands_auth,
    commands_chat,
    commands_chat_smoke,
    commands_trace,
    commands_cron,
    commands_search,
    commands_service,
    commands_status,
    commands_update,
    service_background,
    service_linux,
    update as update_cli,
)

app = typer.Typer(
    name="opensprite",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="OpenSprite CLI.",
)

service_app = typer.Typer(help="Manage the OpenSprite background gateway service.")
app.add_typer(service_app, name="service")
cron_app = typer.Typer(help="Manage per-session scheduled jobs.")
app.add_typer(cron_app, name="cron")
search_app = typer.Typer(help="Inspect the chat history search index.")
app.add_typer(search_app, name="search")
config_app = typer.Typer(help="Inspect and validate configuration files.")
app.add_typer(config_app, name="config")
auth_app = typer.Typer(help="Manage provider authentication.")
app.add_typer(auth_app, name="auth")
credential_app = typer.Typer(help="Manage stored API-key credentials.")
auth_app.add_typer(credential_app, name="credentials")


def version_callback(value: bool) -> None:
    """Print the package version and exit."""
    if value:
        typer.echo(f"opensprite {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show the OpenSprite version and exit.",
    ),
) -> None:
    """OpenSprite CLI."""
    return


@app.command("update")
def update_command(
    branch: str = typer.Option(
        "main",
        "--branch",
        help="Git branch to update from.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Only check whether updates are available.",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Reinstall development dependencies.",
    ),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="Restart the background gateway after a successful update.",
    ),
) -> None:
    """Update a source-checkout OpenSprite install."""
    commands_update.update_command(
        branch=branch,
        check=check,
        dev=dev,
        restart=restart,
        update_cli_module=update_cli,
        use_linux_service=_use_linux_service,
        service_linux_module=service_linux,
        service_background_module=service_background,
        handle_service_error=_handle_service_error,
    )


def _resolve_config_path(config: str | None = None) -> Path:
    """Resolve the OpenSprite config path without creating files."""
    if config:
        return Path(config).expanduser().resolve()
    return (Path.home() / ".opensprite" / "opensprite.json").resolve()


def _resolve_app_home(config: str | None = None) -> Path:
    return _resolve_config_path(config).parent


def _format_presence(value: bool) -> str:
    """Return a simple status label."""
    return "yes" if value else "no"


def _emit_credential_listing(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    credentials = payload.get("credentials")
    if not isinstance(credentials, dict) or not credentials:
        typer.echo("No credentials stored.")
        return
    for provider, entries in credentials.items():
        if not isinstance(entries, list) or not entries:
            continue
        typer.echo(f"{provider}:")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            marker = " default" if entry.get("is_default") else ""
            label = entry.get("label") or entry.get("id")
            preview = entry.get("secret_preview") or "configured"
            typer.echo(f"  {entry.get('id')}  {label}  {preview}{marker}")


def _use_linux_service() -> bool:
    """Return whether service commands should use an installed Linux systemd unit."""
    return platform.system() == "Linux" and service_linux.get_service_file_path().exists()


def _iter_channel_status(config_obj) -> list[tuple[str, bool]]:
    """Collect enabled/disabled channel flags from the loaded config."""
    channels = config_obj.channels.model_dump()
    instances = channels.get("instances") if isinstance(channels, dict) else None
    if isinstance(instances, dict):
        channels = instances
    results: list[tuple[str, bool]] = []
    for name, section in channels.items():
        enabled = False
        if isinstance(section, dict):
            enabled = bool(section.get("enabled", False))
        results.append((name, enabled))
    return results


@app.command()
def status(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output status as JSON.",
    ),
) -> None:
    """Show OpenSprite configuration and runtime status."""
    commands_status.status_command(
        config_path=_resolve_config_path(config),
        json_output=json_output,
        home_root=Path.home(),
        version=__version__,
        format_presence=_format_presence,
        iter_channel_status=_iter_channel_status,
    )


@app.command()
def gateway(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Start the OpenSprite gateway."""
    commands_status.start_gateway(config=config, run_gateway=run_gateway)


@app.command("chat")
def chat(
    message: str = typer.Argument(..., help="Message text to send through the one-shot CLI channel."),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    external_chat_id: str = typer.Option(
        "default",
        "--external-chat-id",
        help="CLI external chat id used to build the session id.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Optional explicit session id, for example cli:smoke.",
    ),
    sender_name: str = typer.Option(
        "OpenSprite CLI",
        "--sender-name",
        help="Sender name attached to the test message.",
    ),
    timeout_seconds: float = typer.Option(
        120.0,
        "--timeout",
        help="Maximum seconds to wait for a reply.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON.",
    ),
    via_web: bool = typer.Option(
        False,
        "--via-web",
        help="Send through an already-running Web gateway instead of the local one-shot CLI channel.",
    ),
    gateway_url: str = typer.Option(
        "http://127.0.0.1:8765",
        "--gateway-url",
        help="Gateway base URL used with --via-web.",
    ),
    ws_url: str | None = typer.Option(
        None,
        "--ws-url",
        help="Explicit WebSocket URL used with --via-web.",
    ),
    access_token: str | None = typer.Option(
        None,
        "--access-token",
        help="Web auth token used with --via-web when auth_token is configured.",
    ),
) -> None:
    """Send one message through a local one-shot CLI channel."""
    commands_chat.chat_command(
        message=message,
        config=config,
        external_chat_id=external_chat_id,
        session_id=session_id,
        sender_name=sender_name,
        timeout_seconds=timeout_seconds,
        json_output=json_output,
        via_web=via_web,
        gateway_url=gateway_url,
        ws_url=ws_url,
        access_token=access_token,
    )


@app.command("chat-smoke")
def chat_smoke(
    gateway_url: str = typer.Option(
        "http://127.0.0.1:8765",
        "--gateway-url",
        help="Running Web gateway base URL.",
    ),
    ws_url: str | None = typer.Option(
        None,
        "--ws-url",
        help="Explicit WebSocket URL when it differs from --gateway-url.",
    ),
    access_token: str | None = typer.Option(
        None,
        "--access-token",
        help="Web auth token used when auth_token is configured.",
    ),
    timeout_seconds: float = typer.Option(
        180.0,
        "--timeout",
        help="Maximum seconds to wait for each case.",
    ),
    external_chat_prefix: str = typer.Option(
        "cli-trace-smoke",
        "--external-chat-prefix",
        help="Prefix for generated Web external chat ids.",
    ),
    db_path: str | None = typer.Option(
        None,
        "--db-path",
        help="SQLite sessions.db path to inspect. Defaults to ~/.opensprite/data/sessions.db.",
    ),
    case_ids: list[str] | None = typer.Option(
        None,
        "--case",
        help="Run only the named case id. May be provided more than once.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON.",
    ),
) -> None:
    """Run the 10-case Web chat trace smoke suite."""
    commands_chat_smoke.chat_smoke_command(
        gateway_url=gateway_url,
        ws_url=ws_url,
        access_token=access_token,
        timeout_seconds=timeout_seconds,
        external_chat_prefix=external_chat_prefix,
        db_path=db_path,
        case_ids=case_ids,
        json_output=json_output,
    )


@app.command("trace")
def trace(
    run_id: str = typer.Argument(..., help="Run id to inspect."),
    session_id: str = typer.Option(
        ...,
        "--session-id",
        help="Session id that owns the run, for example web:my-chat.",
    ),
    db_path: str | None = typer.Option(
        None,
        "--db-path",
        help="SQLite sessions.db path to inspect. Defaults to ~/.opensprite/data/sessions.db.",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Include serialized events, parts, file changes, diff summary, and artifacts.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON.",
    ),
) -> None:
    """Inspect one persisted run trace."""
    commands_trace.trace_command(
        run_id=run_id,
        session_id=session_id,
        db_path=db_path,
        full=full,
        json_output=json_output,
    )


@config_app.command("validate")
def config_validate(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output validation details as JSON.",
    ),
) -> None:
    """Validate the main config and all split external config files."""
    commands_status.config_validate_command(
        config_path=_resolve_config_path(config),
        json_output=json_output,
        format_presence=_format_presence,
        iter_channel_status=_iter_channel_status,
    )


@auth_app.command("status")
def auth_status(
    provider: str = typer.Argument("openai-codex", help="Provider id."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
    json_output: bool = typer.Option(False, "--json", help="Output status as JSON."),
) -> None:
    """Show provider authentication status."""
    commands_auth.auth_status_command(
        provider=provider,
        config=config,
        json_output=json_output,
        resolve_app_home=_resolve_app_home,
        format_presence=_format_presence,
    )


@auth_app.command("logout")
def auth_logout(
    provider: str = typer.Argument("openai-codex", help="Provider id."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
) -> None:
    """Remove stored provider credentials."""
    commands_auth.auth_logout_command(
        provider=provider,
        config=config,
        resolve_app_home=_resolve_app_home,
    )


@auth_app.command("login")
def auth_login(
    provider: str = typer.Argument("openai-codex", help="Provider id."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
    timeout_seconds: float = typer.Option(900.0, "--timeout", help="Maximum seconds to wait for browser authorization."),
) -> None:
    """Start provider login."""
    commands_auth.auth_login_command(
        provider=provider,
        config=config,
        timeout_seconds=timeout_seconds,
        resolve_app_home=_resolve_app_home,
    )


@credential_app.command("list")
def auth_credentials_list(
    provider: str | None = typer.Argument(None, help="Optional provider id filter."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
    json_output: bool = typer.Option(False, "--json", help="Output credentials as JSON."),
) -> None:
    """List stored API-key credentials without revealing secrets."""
    commands_auth.auth_credentials_list_command(
        provider=provider,
        config=config,
        json_output=json_output,
        resolve_app_home=_resolve_app_home,
        emit_credential_listing=_emit_credential_listing,
    )


@search_app.command("status")
def search_status(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Optional session id to inspect instead of the full chat history search index.",
    ),
) -> None:
    """Show chat history search index status."""
    commands_search.search_status_command(
        config=config,
        session_id=session_id,
        load_sqlite_search_store=_load_sqlite_search_store,
        handle_search_error=_handle_search_error,
        format_presence=_format_presence,
    )


def _handle_service_error(exc: Exception) -> None:
    """Render a service-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1) from exc


def _handle_cron_error(exc: Exception | str) -> None:
    """Render a cron-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _handle_search_error(exc: Exception | str) -> None:
    """Render a search-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _resolve_workspace_root() -> Path:
    """Resolve the default workspace root used by cron CLI commands."""
    return get_tool_workspace()


def _load_sqlite_search_store(config: str | None = None):
    """Load the configured SQLite search store or fail with a clear message."""
    return commands_search.load_sqlite_search_store(config, resolve_config_path=_resolve_config_path)


def _get_cron_service(session: str) -> CronService:
    """Open the cron service store for a session without starting a timer loop."""
    return commands_cron.get_cron_service(session, resolve_workspace_root=_resolve_workspace_root)


def _load_cli_cron_messages(config: str | None = None):
    return commands_cron.load_cli_cron_messages(config, resolve_config_path=_resolve_config_path)


def _render_cron_jobs(service: CronService, default_timezone: str = "UTC", *, messages=None) -> str:
    """Render the stored jobs for CLI list output."""
    messages = messages or _load_cli_cron_messages()
    return commands_cron.render_cron_jobs_text(service, default_timezone=default_timezone, messages=messages)


@service_app.command("install")
def service_install(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    start: bool = typer.Option(
        True,
        "--start/--no-start",
        help="Start the service immediately after installation.",
    ),
) -> None:
    """Install OpenSprite background startup integration."""
    commands_service.service_install_command(
        config=config,
        start=start,
        resolve_config_path=_resolve_config_path,
        service_linux_module=service_linux,
        service_background_module=service_background,
        handle_service_error=_handle_service_error,
    )


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Uninstall OpenSprite background startup integration."""
    commands_service.service_uninstall_command(
        service_linux_module=service_linux,
        service_background_module=service_background,
        handle_service_error=_handle_service_error,
    )


@service_app.command("start")
def service_start(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file for detached process mode.",
    ),
) -> None:
    """Start the OpenSprite gateway in the background."""
    commands_service.service_start_command(
        config=config,
        use_linux_service=_use_linux_service(),
        service_linux_module=service_linux,
        service_background_module=service_background,
        handle_service_error=_handle_service_error,
    )


@service_app.command("stop")
def service_stop() -> None:
    """Stop the OpenSprite background gateway."""
    commands_service.service_stop_command(
        use_linux_service=_use_linux_service(),
        service_linux_module=service_linux,
        service_background_module=service_background,
        handle_service_error=_handle_service_error,
    )


@service_app.command("restart")
def service_restart(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file for detached process mode.",
    ),
) -> None:
    """Restart the OpenSprite background gateway."""
    commands_service.service_restart_command(
        config=config,
        use_linux_service=_use_linux_service(),
        service_linux_module=service_linux,
        service_background_module=service_background,
        handle_service_error=_handle_service_error,
    )


@service_app.command("status")
def service_status() -> None:
    """Show OpenSprite background gateway status."""
    commands_service.service_status_command(
        use_linux_service=_use_linux_service(),
        service_linux_module=service_linux,
        service_background_module=service_background,
        format_presence=_format_presence,
        handle_service_error=_handle_service_error,
    )


@cron_app.command("list")
def cron_list(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """List scheduled jobs for one session."""
    commands_cron.cron_list_command(
        session=session,
        config=config,
        get_cron_service=_get_cron_service,
        load_cli_cron_messages=_load_cli_cron_messages,
        render_cron_jobs_text=_render_cron_jobs,
    )


@cron_app.command("remove")
def cron_remove(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to remove.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Remove one scheduled job from a session."""
    commands_cron.cron_remove_command(
        session=session,
        job_id=job_id,
        config=config,
        get_cron_service=_get_cron_service,
        load_cli_cron_messages=_load_cli_cron_messages,
        handle_cron_error=_handle_cron_error,
    )


if __name__ == "__main__":
    app()
