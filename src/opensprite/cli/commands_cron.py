"""Cron command helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import typer

from ..context.paths import get_session_workspace
from ..cron import CronService
from ..cron.presentation import render_cron_jobs


def get_cron_service(session: str, *, resolve_workspace_root: Callable[[], Path]) -> CronService:
    """Open the cron service store for a session without starting a timer loop."""
    workspace = get_session_workspace(session, workspace_root=resolve_workspace_root())
    return CronService(workspace / "cron" / "jobs.json", session_id=session)


def load_cli_cron_messages(config: str | None, *, resolve_config_path: Callable[[str | None], Path]):
    from ..config import Config, CronMessagesConfig

    config_path = resolve_config_path(config)
    if not config_path.exists():
        return CronMessagesConfig()
    try:
        return Config.from_json(config_path).messages.cron
    except Exception:
        return CronMessagesConfig()


def render_cron_jobs_text(service: CronService, default_timezone: str = "UTC", *, messages=None) -> str:
    """Render the stored jobs for CLI list output."""
    return render_cron_jobs(service.list_jobs(include_disabled=True), messages, default_timezone=default_timezone)


def cron_list_command(*, session: str, config: str | None, get_cron_service: Callable[[str], CronService], load_cli_cron_messages: Callable[[str | None], object], render_cron_jobs_text: Callable[..., str]) -> None:
    service = get_cron_service(session)
    messages = load_cli_cron_messages(config)
    typer.echo(render_cron_jobs_text(service, messages=messages))


def cron_remove_command(*, session: str, job_id: str, config: str | None, get_cron_service: Callable[[str], CronService], load_cli_cron_messages: Callable[[str | None], object], handle_cron_error: Callable[[Exception | str], None]) -> None:
    messages = load_cli_cron_messages(config)
    service = get_cron_service(session)
    if not service.remove_job(job_id):
        handle_cron_error(messages.job_not_found.format(job_id=job_id))
    typer.echo(messages.removed_job.format(job_id=job_id))
