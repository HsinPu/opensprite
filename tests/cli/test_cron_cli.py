import json

from typer.testing import CliRunner

from opensprite.cli.commands import app
from opensprite.context.paths import get_session_workspace
from opensprite.cron import CronSchedule, CronService


runner = CliRunner()


def _write_cron_messages_config(root):
    config_path = root / "opensprite.json"
    messages_path = root / "messages.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "key", "model": "gpt", "temperature": 0.7, "max_tokens": 2048},
                "storage": {"type": "memory", "path": "memory.db"},
                "channels": {
                    "instances": {
                        "telegram": {"type": "telegram", "enabled": False},
                        "web": {"type": "web", "enabled": True},
                    }
                },
                "messages_file": "messages.json",
            }
        ),
        encoding="utf-8",
    )
    messages_path.write_text(
        json.dumps(
            {
                "cron": {
                    "no_jobs": "No configured cron jobs.",
                    "removed_job": "Removed configured job {job_id}.",
                }
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _seed_cron_job(workspace_root, session="telegram:user-a", *, name="weather-check"):
    jobs_path = get_session_workspace(session, workspace_root=workspace_root) / "cron" / "jobs.json"
    service = CronService(jobs_path, session_id=session)
    return service.add_job(
        name=name,
        schedule=CronSchedule(kind="every", every_ms=300_000),
        message="Check weather and report back",
        deliver=True,
        channel="telegram",
        external_chat_id="user-a",
    )


def test_cron_cli_lists_and_removes_existing_jobs(monkeypatch, tmp_path):
    workspace_root = tmp_path / "workspace"
    monkeypatch.setattr("opensprite.cli.commands._resolve_workspace_root", lambda: workspace_root)
    job = _seed_cron_job(workspace_root)

    list_result = runner.invoke(app, ["cron", "list", "--session", "telegram:user-a"])
    assert list_result.exit_code == 0
    assert "Scheduled jobs:" in list_result.stdout
    assert "weather-check" in list_result.stdout
    assert "every 5m" in list_result.stdout

    remove_result = runner.invoke(
        app,
        ["cron", "remove", "--session", "telegram:user-a", "--job-id", job.id],
    )
    assert remove_result.exit_code == 0
    assert f"Removed job {job.id}" in remove_result.stdout

    empty_list = runner.invoke(app, ["cron", "list", "--session", "telegram:user-a"])
    assert empty_list.exit_code == 0
    assert empty_list.stdout.strip() == "No scheduled jobs."


def test_cron_cli_help_only_exposes_inspection_and_cleanup_commands():
    result = runner.invoke(app, ["cron", "--help"])

    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "remove" in result.stdout
    assert "add" not in result.stdout
    assert "pause" not in result.stdout
    assert "enable" not in result.stdout
    assert "run" not in result.stdout


def test_cron_cli_uses_configured_messages_for_list_and_remove(monkeypatch, tmp_path):
    workspace_root = tmp_path / "workspace"
    monkeypatch.setattr("opensprite.cli.commands._resolve_workspace_root", lambda: workspace_root)
    config_path = _write_cron_messages_config(tmp_path)
    job = _seed_cron_job(workspace_root)

    empty_list = runner.invoke(app, ["cron", "list", "--session", "telegram:user-b", "--config", str(config_path)])
    assert empty_list.exit_code == 0
    assert empty_list.stdout.strip() == "No configured cron jobs."

    remove_result = runner.invoke(
        app,
        ["cron", "remove", "--session", "telegram:user-a", "--job-id", job.id, "--config", str(config_path)],
    )
    assert remove_result.exit_code == 0
    assert f"Removed configured job {job.id}." in remove_result.stdout
