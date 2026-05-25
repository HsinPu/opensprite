"""CLI entrypoints for OpenSprite."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import platform

import typer

from .. import __version__
from ..context.paths import get_session_workspace, get_tool_workspace
from ..cron import CronSchedule, CronService
from ..cron.presentation import format_cron_timestamp, format_cron_timing, render_cron_jobs
from ..runtime import gateway as run_gateway
from . import (
    commands_auth,
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
search_app = typer.Typer(help="Inspect and rebuild the chat history search index.")
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


@credential_app.command("add")
def auth_credentials_add(
    provider: str = typer.Argument(..., help="Provider id, for example openrouter or openai."),
    secret: str | None = typer.Option(None, "--secret", "--api-key", help="API key value. If omitted, prompts securely."),
    label: str | None = typer.Option(None, "--label", help="Display label for this credential."),
    base_url: str | None = typer.Option(None, "--base-url", help="Optional runtime base URL."),
    capability: list[str] | None = typer.Option(None, "--capability", help="Capability this credential can satisfy. Repeatable."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
    json_output: bool = typer.Option(False, "--json", help="Output created credential as JSON."),
) -> None:
    """Store an API key in the local credential vault."""
    commands_auth.auth_credentials_add_command(
        provider=provider,
        secret=secret,
        label=label,
        base_url=base_url,
        capability=capability,
        config=config,
        json_output=json_output,
        resolve_app_home=_resolve_app_home,
    )


@credential_app.command("remove")
def auth_credentials_remove(
    provider: str = typer.Argument(..., help="Provider id."),
    credential_id: str = typer.Argument(..., help="Credential id to remove."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
) -> None:
    """Remove one stored credential."""
    commands_auth.auth_credentials_remove_command(
        provider=provider,
        credential_id=credential_id,
        config=config,
        resolve_app_home=_resolve_app_home,
        resolve_config_path=_resolve_config_path,
    )


@credential_app.command("default")
def auth_credentials_default(
    credential_id: str = typer.Argument(..., help="Credential id to use by default."),
    provider: str | None = typer.Option(None, "--provider", help="Set the default credential for this provider."),
    capability: str | None = typer.Option(None, "--capability", help="Set the default credential for this capability."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to an OpenSprite JSON config file."),
    json_output: bool = typer.Option(False, "--json", help="Output updated default as JSON."),
) -> None:
    """Set a provider or capability default credential."""
    commands_auth.auth_credentials_default_command(
        credential_id=credential_id,
        provider=provider,
        capability=capability,
        config=config,
        json_output=json_output,
        resolve_app_home=_resolve_app_home,
    )


@search_app.command("rebuild")
def search_rebuild(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Optional session id to rebuild instead of rebuilding every chat history index.",
    ),
) -> None:
    """Rebuild the chat history search index from stored messages."""
    commands_search.search_rebuild_command(
        config=config,
        session_id=session_id,
        load_sqlite_search_store=_load_sqlite_search_store,
        handle_search_error=_handle_search_error,
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
    """Show chat history search index and embedding status."""
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


def _build_cli_schedule(
    *,
    every_seconds: int | None,
    cron_expr: str | None,
    tz: str | None,
    at: str | None,
    default_timezone: str = "UTC",
) -> tuple[CronSchedule, bool]:
    """Build a CronSchedule from CLI arguments."""
    return commands_cron.build_cli_schedule(
        every_seconds=every_seconds,
        cron_expr=cron_expr,
        tz=tz,
        at=at,
        default_timezone=default_timezone,
    )


def _format_cron_timestamp(ms: int, tz_name: str) -> str:
    """Format a scheduled timestamp for CLI output."""
    return format_cron_timestamp(ms, tz_name)


def _format_cron_timing(schedule: CronSchedule, default_timezone: str = "UTC") -> str:
    """Format a cron schedule in the same style as the runtime tool."""
    return format_cron_timing(schedule, default_timezone)


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
    """Install OpenSprite as a Linux systemd user service."""
    commands_service.service_install_command(
        config=config,
        start=start,
        resolve_config_path=_resolve_config_path,
        service_linux_module=service_linux,
        handle_service_error=_handle_service_error,
    )


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Uninstall the OpenSprite Linux systemd user service."""
    commands_service.service_uninstall_command(
        service_linux_module=service_linux,
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


@cron_app.command("add")
def cron_add(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    message: str = typer.Option(
        ...,
        "--message",
        help="Instruction to execute when the job triggers.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Optional short label for the job.",
    ),
    every_seconds: int | None = typer.Option(
        None,
        "--every-seconds",
        help="Fixed recurring interval in seconds.",
    ),
    cron_expr: str | None = typer.Option(
        None,
        "--cron-expr",
        help="Cron expression like '0 9 * * *'.",
    ),
    tz: str | None = typer.Option(
        None,
        "--tz",
        help="Optional IANA timezone for cron expressions.",
    ),
    at: str | None = typer.Option(
        None,
        "--at",
        help="ISO datetime for one-time execution, e.g. 2026-04-10T09:00:00.",
    ),
    deliver: bool = typer.Option(
        True,
        "--deliver/--no-deliver",
        help="Whether the job should send its result back to the original chat.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Add a scheduled job to one session."""
    commands_cron.cron_add_command(
        session=session,
        message=message,
        name=name,
        every_seconds=every_seconds,
        cron_expr=cron_expr,
        tz=tz,
        at=at,
        deliver=deliver,
        config=config,
        get_cron_service=_get_cron_service,
        build_cli_schedule=_build_cli_schedule,
        load_cli_cron_messages=_load_cli_cron_messages,
        handle_cron_error=_handle_cron_error,
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


@cron_app.command("pause")
def cron_pause(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to pause.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Pause one scheduled job in a session without deleting it."""
    commands_cron.cron_pause_command(
        session=session,
        job_id=job_id,
        config=config,
        get_cron_service=_get_cron_service,
        load_cli_cron_messages=_load_cli_cron_messages,
        handle_cron_error=_handle_cron_error,
    )


@cron_app.command("enable")
def cron_enable(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to re-enable.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Re-enable a paused scheduled job in a session."""
    commands_cron.cron_enable_command(
        session=session,
        job_id=job_id,
        config=config,
        get_cron_service=_get_cron_service,
        load_cli_cron_messages=_load_cli_cron_messages,
        handle_cron_error=_handle_cron_error,
    )


@cron_app.command("run")
def cron_run(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to execute immediately.",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
) -> None:
    """Run one scheduled job immediately in a session."""
    commands_cron.cron_run_command(
        session=session,
        job_id=job_id,
        config=config,
        get_cron_service=_get_cron_service,
        load_cli_cron_messages=_load_cli_cron_messages,
        handle_cron_error=_handle_cron_error,
    )


if __name__ == "__main__":
    app()
