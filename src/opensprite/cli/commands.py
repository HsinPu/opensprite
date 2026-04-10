"""CLI entrypoints for OpenSprite."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import typer

from .. import __version__
from ..context.paths import get_chat_workspace, get_tool_workspace
from ..cron import CronSchedule, CronService
from ..runtime import gateway as run_gateway
from . import service_linux
from .onboard import run_onboard

app = typer.Typer(
    name="opensprite",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="OpenSprite CLI.",
)

service_app = typer.Typer(help="Manage the Linux systemd user service.")
app.add_typer(service_app, name="service")
cron_app = typer.Typer(help="Manage per-session scheduled jobs.")
app.add_typer(cron_app, name="cron")


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


def _start_gateway(config: str | None = None) -> None:
    """Start the OpenSprite gateway with optional config override."""
    try:
        run_gateway(config_path=config)
    except (FileNotFoundError, ValueError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _resolve_config_path(config: str | None = None) -> Path:
    """Resolve the OpenSprite config path without creating files."""
    if config:
        return Path(config).expanduser().resolve()
    return (Path.home() / ".opensprite" / "opensprite.json").resolve()


def _format_presence(value: bool) -> str:
    """Return a simple status label."""
    return "yes" if value else "no"


def _iter_channel_status(config_obj) -> list[tuple[str, bool]]:
    """Collect enabled/disabled channel flags from the loaded config."""
    channels = config_obj.channels.model_dump()
    results: list[tuple[str, bool]] = []
    for name, section in channels.items():
        enabled = False
        if isinstance(section, dict):
            enabled = bool(section.get("enabled", False))
        results.append((name, enabled))
    return results


def _emit_status(payload: dict[str, object], json_output: bool) -> None:
    """Render status output in text or JSON format."""
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.echo("OpenSprite Status")
    typer.echo(f"Version: {payload['version']}")
    typer.echo(
        f"Config: {payload['config_path']} [{_format_presence(bool(payload['config_exists']))}]"
    )
    typer.echo(
        f"App Home: {payload['app_home']} [{_format_presence(bool(payload['app_home_exists']))}]"
    )
    typer.echo(
        "Workspace Root: "
        f"{payload['workspace_root']} [{_format_presence(bool(payload['workspace_root_exists']))}]"
    )

    if not bool(payload["config_loaded"]):
        typer.echo(f"LLM Configured: {_format_presence(bool(payload['llm_configured']))}")
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

    typer.echo(f"LLM Configured: {_format_presence(bool(payload['llm_configured']))}")
    typer.echo(
        "Provider: "
        f"{provider['name'] or '<unset>'} "
        f"(enabled={_format_presence(bool(provider['enabled']))}, "
        f"api_key={_format_presence(bool(provider['api_key_configured']))})"
    )
    typer.echo(f"Model: {provider['model']}")
    typer.echo(f"Storage: {storage['type']} -> {storage['path']}")
    typer.echo(
        f"Search: {search['provider']} "
        f"(enabled={_format_presence(bool(search['enabled']))}) -> {search['path']}"
    )
    typer.echo(
        "Channels: " + (", ".join(enabled_channels) if enabled_channels else "none enabled")
    )


def _run_onboard(
    config: str | None = None,
    *,
    force: bool = False,
    no_input: bool = False,
) -> None:
    """Run the OpenSprite onboarding workflow and print next steps."""
    try:
        result = run_onboard(config_path=config, force=force, interactive=not no_input)
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("OpenSprite onboarding complete.")
    typer.echo(f"Config: {result.config_path}")
    typer.echo(f"App Home: {result.app_home}")

    if result.created_config:
        typer.echo("Config status: created")
    elif result.reset_config:
        typer.echo("Config status: reset to defaults")
    elif result.refreshed_config:
        typer.echo("Config status: refreshed with missing defaults")
    else:
        typer.echo("Config status: unchanged")

    if result.created_dirs:
        typer.echo("Created directories:")
        for path in result.created_dirs:
            typer.echo(f"- {path}")

    if result.template_files:
        typer.echo("Added template files:")
        for relative_path in result.template_files:
            typer.echo(f"- {relative_path}")

    if result.interactive:
        typer.echo("Interactive setup:")
        typer.echo(f"- LLM provider: {result.llm_provider or '<unset>'}")
        typer.echo(f"- Model: {result.llm_model or '<unset>'}")
        typer.echo(f"- Channel: {result.channel_name or '<unset>'}")

    typer.echo("Next steps:")
    if not result.llm_api_key_configured:
        typer.echo(f"1. Edit {result.config_path} and add your API key")
        typer.echo("2. Run `opensprite status` to verify the setup")
        typer.echo("3. Start the service with `opensprite gateway`")
    else:
        typer.echo("1. Run `opensprite status` to verify the setup")
        typer.echo("2. Start the service with `opensprite gateway`")


@app.command()
def onboard(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an OpenSprite JSON config file.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Reset the config to the packaged defaults instead of refreshing missing fields.",
    ),
    no_input: bool = typer.Option(
        False,
        "--no-input",
        help="Initialize files only and skip interactive prompts.",
    ),
) -> None:
    """Initialize or refresh OpenSprite and prompt for key settings by default."""
    _run_onboard(config=config, force=force, no_input=no_input)


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
    from ..config import Config

    config_path = _resolve_config_path(config)
    app_home = (Path.home() / ".opensprite").resolve()
    workspace_root = (app_home / "workspace").resolve()

    payload: dict[str, object] = {
        "version": __version__,
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
        payload["hint"] = "run `opensprite onboard` to create the default config and app directories."
        _emit_status(payload, json_output)
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
        provider_has_key = bool(active_provider.api_key)
        model_name = active_provider.model or "<unset>"
    else:
        provider_enabled = False
        provider_has_key = False
        model_name = "<unset>"
    storage_path = Path(loaded.storage.path).expanduser()
    search_path = Path(loaded.search.path).expanduser()
    channels = {name: enabled for name, enabled in _iter_channel_status(loaded)}

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
                "provider": loaded.search.provider,
                "enabled": loaded.search.enabled,
                "path": str(search_path),
            },
            "channels": channels,
        }
    )
    _emit_status(payload, json_output)


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
    _start_gateway(config=config)


def _handle_service_error(exc: Exception) -> None:
    """Render a service-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1) from exc


def _handle_cron_error(exc: Exception | str) -> None:
    """Render a cron-management error and exit non-zero."""
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _resolve_workspace_root() -> Path:
    """Resolve the default workspace root used by cron CLI commands."""
    return get_tool_workspace()


def _get_cron_service(session: str) -> CronService:
    """Open the cron service store for a session without starting a timer loop."""
    workspace = get_chat_workspace(session, workspace_root=_resolve_workspace_root())
    return CronService(workspace / "cron" / "jobs.json", session_chat_id=session)


def _build_cli_schedule(
    *,
    every_seconds: int | None,
    cron_expr: str | None,
    tz: str | None,
    at: str | None,
    default_timezone: str = "UTC",
) -> tuple[CronSchedule, bool]:
    """Build a CronSchedule from CLI arguments."""
    provided = [every_seconds is not None, bool(cron_expr), bool(at)]
    if sum(provided) != 1:
        raise ValueError("provide exactly one of --every-seconds, --cron-expr, or --at")
    if tz and not cron_expr:
        raise ValueError("--tz can only be used with --cron-expr")

    if every_seconds is not None:
        if every_seconds <= 0:
            raise ValueError("--every-seconds must be greater than 0")
        return CronSchedule(kind="every", every_ms=every_seconds * 1000), False

    if cron_expr:
        return CronSchedule(kind="cron", expr=cron_expr, tz=tz or default_timezone), False

    try:
        dt = datetime.fromisoformat(at or "")
    except ValueError as exc:
        raise ValueError("--at must use ISO format like 2026-04-10T09:00:00") from exc

    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo

        dt = dt.replace(tzinfo=ZoneInfo(default_timezone))
    return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000)), True


def _format_cron_timestamp(ms: int, tz_name: str) -> str:
    """Format a scheduled timestamp for CLI output."""
    from zoneinfo import ZoneInfo

    dt = datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz_name))
    return f"{dt.isoformat()} ({tz_name})"


def _format_cron_timing(schedule: CronSchedule, default_timezone: str = "UTC") -> str:
    """Format a cron schedule in the same style as the runtime tool."""
    if schedule.kind == "cron":
        tz = f" ({schedule.tz})" if schedule.tz else ""
        return f"cron: {schedule.expr}{tz}"
    if schedule.kind == "every" and schedule.every_ms:
        if schedule.every_ms % 3_600_000 == 0:
            return f"every {schedule.every_ms // 3_600_000}h"
        if schedule.every_ms % 60_000 == 0:
            return f"every {schedule.every_ms // 60_000}m"
        if schedule.every_ms % 1000 == 0:
            return f"every {schedule.every_ms // 1000}s"
        return f"every {schedule.every_ms}ms"
    if schedule.kind == "at" and schedule.at_ms:
        return f"at {_format_cron_timestamp(schedule.at_ms, schedule.tz or default_timezone)}"
    return schedule.kind


def _render_cron_jobs(service: CronService, default_timezone: str = "UTC") -> str:
    """Render the stored jobs for CLI list output."""
    jobs = service.list_jobs(include_disabled=True)
    if not jobs:
        return "No scheduled jobs."

    lines = []
    for job in jobs:
        line = f"- {job.name} (id: {job.id}, {_format_cron_timing(job.schedule, default_timezone)})"
        if job.state.next_run_at_ms:
            line += f"\n  Next run: {_format_cron_timestamp(job.state.next_run_at_ms, job.schedule.tz or default_timezone)}"
        lines.append(line)
    return "Scheduled jobs:\n" + "\n".join(lines)


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
    try:
        config_path = _resolve_config_path(config)
        service_file = service_linux.install_service(config_path, start=start)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _handle_service_error(exc)

    typer.echo(f"Installed service: {service_file}")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Started: {'yes' if start else 'no'}")
    typer.echo("Tip: run `loginctl enable-linger $USER` if you want the user service to stay up after logout.")


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Uninstall the OpenSprite Linux systemd user service."""
    try:
        removed = service_linux.uninstall_service()
    except RuntimeError as exc:
        _handle_service_error(exc)

    if removed:
        typer.echo("Removed OpenSprite service.")
    else:
        typer.echo("OpenSprite service is not installed.")


@service_app.command("start")
def service_start() -> None:
    """Start the installed OpenSprite Linux systemd user service."""
    try:
        service_linux.start_service()
    except (FileNotFoundError, RuntimeError) as exc:
        _handle_service_error(exc)
    typer.echo("Started OpenSprite service.")


@service_app.command("stop")
def service_stop() -> None:
    """Stop the installed OpenSprite Linux systemd user service."""
    try:
        service_linux.stop_service()
    except (FileNotFoundError, RuntimeError) as exc:
        _handle_service_error(exc)
    typer.echo("Stopped OpenSprite service.")


@service_app.command("restart")
def service_restart() -> None:
    """Restart the installed OpenSprite Linux systemd user service."""
    try:
        service_linux.restart_service()
    except (FileNotFoundError, RuntimeError) as exc:
        _handle_service_error(exc)
    typer.echo("Restarted OpenSprite service.")


@service_app.command("status")
def service_status() -> None:
    """Show OpenSprite Linux systemd user service status."""
    try:
        status = service_linux.get_service_status()
    except RuntimeError as exc:
        _handle_service_error(exc)

    typer.echo("OpenSprite Service")
    typer.echo(f"Service File: {status.service_file}")
    typer.echo(f"Installed: {_format_presence(status.installed)}")
    typer.echo(f"Enabled: {_format_presence(status.enabled)}")
    typer.echo(f"Active: {_format_presence(status.active)}")


@cron_app.command("list")
def cron_list(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
    ),
) -> None:
    """List scheduled jobs for one session."""
    service = _get_cron_service(session)
    typer.echo(_render_cron_jobs(service))


@cron_app.command("add")
def cron_add(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
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
) -> None:
    """Add a scheduled job to one session."""
    try:
        schedule, delete_after = _build_cli_schedule(
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            tz=tz,
            at=at,
        )
        service = _get_cron_service(session)
        if ":" in session:
            channel, chat_id = session.split(":", 1)
        else:
            channel, chat_id = "default", session
        job = service.add_job(
            name=name or message[:30],
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            chat_id=chat_id,
            delete_after_run=delete_after,
        )
    except ValueError as exc:
        _handle_cron_error(exc)

    typer.echo(f"Created job '{job.name}' (id: {job.id})")


@cron_app.command("remove")
def cron_remove(
    session: str = typer.Option(
        ...,
        "--session",
        help="Session chat id, for example telegram:user-a.",
    ),
    job_id: str = typer.Option(
        ...,
        "--job-id",
        help="The job id to remove.",
    ),
) -> None:
    """Remove one scheduled job from a session."""
    service = _get_cron_service(session)
    if not service.remove_job(job_id):
        _handle_cron_error(f"job {job_id} not found")
    typer.echo(f"Removed job {job_id}")


if __name__ == "__main__":
    app()
