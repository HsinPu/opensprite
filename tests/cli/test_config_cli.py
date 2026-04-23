import json

from typer.testing import CliRunner

from opensprite.cli.commands import app


runner = CliRunner()


def _write_split_config(root):
    (root / "opensprite.json").write_text(
        json.dumps(
            {
                "llm": {
                    "providers_file": "llm.providers.json",
                    "default": "openai",
                    "temperature": 0.7,
                    "max_tokens": 2048,
                },
                "storage": {"type": "memory", "path": "memory.db"},
                "channels_file": "channels.json",
                "search_file": "search.json",
                "media_file": "media.json",
                "messages_file": "messages.json",
                "log": {
                    "enabled": True,
                    "retention_days": 365,
                    "level": "INFO",
                    "log_system_prompt": True,
                    "log_system_prompt_lines": 0,
                },
                "tools": {"mcp_servers_file": "mcp_servers.json"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "llm.providers.json").write_text(
        json.dumps(
            {
                "openai": {
                    "api_key": "key",
                    "enabled": True,
                    "model": "gpt-4.1-mini",
                    "base_url": "https://api.openai.com/v1",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "channels.json").write_text(
        json.dumps({"telegram": {"enabled": False, "token": ""}, "web": {"enabled": True}, "console": {"enabled": True}}, indent=2),
        encoding="utf-8",
    )
    (root / "search.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "history_top_k": 5,
                "knowledge_top_k": 5,
                "embedding": {
                    "enabled": False,
                    "provider": "openai",
                    "api_key": "",
                    "model": "",
                    "base_url": None,
                    "batch_size": 16,
                    "candidate_count": 20,
                    "candidate_strategy": "vector",
                    "vector_backend": "auto",
                    "vector_candidate_count": 50,
                    "retry_failed_on_startup": False,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "media.json").write_text(
        json.dumps(
            {
                "vision": {"enabled": False, "provider": "minimax", "api_key": "", "model": "", "base_url": None},
                "speech": {"enabled": False, "provider": "minimax", "api_key": "", "model": "", "base_url": None},
                "video": {"enabled": False, "provider": "minimax", "api_key": "", "model": "", "base_url": None},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "messages.json").write_text(
        json.dumps(
            {
                "agent": {
                    "empty_response_fallback": "抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。",
                    "llm_not_configured": "尚未設定 LLM，請先設定後再試。可執行 opensprite onboard，或在 llm.providers.json 設定預設 provider 的 api_key。",
                },
                "queue": {
                    "stop_cancelled": "已停止目前這段對話。",
                    "stop_idle": "目前沒有正在執行的對話可停止。",
                    "reset_done": "已重置目前這段對話。",
                    "reset_done_with_cancelled": "已重置目前這段對話。 進行中的任務也已停止。"
                },
                "cron": {
                    "help_text": "排程命令:\n/cron add every <seconds> <message> [--no-deliver]\n/cron add at <iso-datetime> <message> [--no-deliver]\n/cron add cron \"<expr>\" [--tz <timezone>] <message> [--no-deliver]\n/cron list\n/cron pause <job_id>\n/cron enable <job_id>\n/cron run <job_id>\n/cron remove <job_id>\n/cron help",
                    "unavailable": "排程功能目前不可用。",
                    "error_prefix": "Error: {message}",
                    "error_invalid_quoting": "Invalid quoting in /cron command",
                    "error_add_usage": "Usage: /cron add every <seconds> <message>",
                    "error_message_required": "A non-empty message is required",
                    "error_every_requires_integer": "every requires an integer number of seconds",
                    "error_every_requires_positive": "every requires a value greater than 0",
                    "error_tz_only_for_cron": "--tz can only be used with cron schedules",
                    "error_at_requires_iso": "at requires ISO format like 2026-04-10T09:00:00",
                    "error_unknown_schedule_mode": "Unknown schedule mode. Use every, at, or cron",
                    "error_job_id_required_pause": "Error: job_id is required. Usage: /cron pause <job_id>",
                    "error_job_id_required_enable": "Error: job_id is required. Usage: /cron enable <job_id>",
                    "error_job_id_required_run": "Error: job_id is required. Usage: /cron run <job_id>",
                    "error_job_id_required_remove": "Error: job_id is required. Usage: /cron remove <job_id>",
                    "error_manager_unavailable": "Error: cron manager is unavailable",
                    "error_no_active_session": "Error: no active session context",
                    "error_message_required_for_add": "Error: message is required for add",
                    "error_invalid_iso_datetime": "Error: invalid ISO datetime format '{value}'. Expected YYYY-MM-DDTHH:MM:SS",
                    "error_schedule_required": "Error: either every_seconds, cron_expr, or at is required",
                    "error_unknown_action": "Unknown action: {action}",
                    "no_jobs": "No scheduled jobs.",
                    "jobs_header": "Scheduled jobs:",
                    "job_list_item": "- {name} (id: {job_id}, {timing})",
                    "next_run_label": "Next run: {timestamp}",
                    "created_job": "Created job '{name}' (id: {job_id})",
                    "removed_job": "Removed job {job_id}",
                    "paused_job": "Paused job {job_id}",
                    "enabled_job": "Enabled job {job_id}",
                    "ran_job": "Ran job {job_id}",
                    "job_not_found": "Job {job_id} not found",
                    "job_not_found_or_paused": "Job {job_id} not found or already paused",
                    "job_not_found_or_enabled": "Job {job_id} not found or already enabled"
                },
                "telegram": {
                    "empty_message_fallback": "抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。"
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "mcp_servers.json").write_text("{}\n", encoding="utf-8")
    return root / "opensprite.json"


def test_config_validate_reports_valid_split_config(tmp_path):
    config_path = _write_split_config(tmp_path)

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "OpenSprite Config Validation" in result.stdout
    assert "Valid: yes" in result.stdout
    assert "Enabled channels: web, console" in result.stdout
    assert "MCP servers: none" in result.stdout


def test_config_validate_reports_missing_external_file(tmp_path):
    config_path = _write_split_config(tmp_path)
    (tmp_path / "media.json").unlink()

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Valid: no" in result.stdout
    assert "Missing config files: media" in result.stdout


def test_config_validate_json_output_includes_error_details(tmp_path):
    config_path = _write_split_config(tmp_path)
    (tmp_path / "search.json").write_text("[]", encoding="utf-8")

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert payload["config_exists"] is True
    assert any(entry["name"] == "search" and entry["valid_json"] is False for entry in payload["files"])
    assert "JSON root must be an object" in payload["error"]
