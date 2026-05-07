from agent_test_helpers import make_agent_loop
import opensprite.agent.agent as agent_module
import opensprite.agent.prompt_logging as prompt_logging_module
from opensprite.agent.prompt_logging import PromptLoggingService


def _make_logging_agent(tmp_path):
    agent = make_agent_loop(tmp_path / "workspace")
    agent.app_home = tmp_path / "home"
    return agent


def test_system_prompt_logging_writes_one_file_per_prompt(tmp_path):
    agent = _make_logging_agent(tmp_path)

    agent._write_full_system_prompt_log("telegram:user-a", "first prompt")
    agent._write_full_system_prompt_log("telegram:user-a", "second prompt")

    dated_dirs = list((agent.app_home / "logs" / "system-prompts").iterdir())
    assert len(dated_dirs) == 1

    log_files = sorted(dated_dirs[0].glob("*.md"))
    assert len(log_files) == 2
    assert all("telegram-user-a" in file.name for file in log_files)
    assert log_files[0].read_text(encoding="utf-8") != log_files[1].read_text(encoding="utf-8")


def test_subagent_system_prompt_logging_uses_separate_directory(tmp_path):
    agent = _make_logging_agent(tmp_path)

    agent._write_full_system_prompt_log("telegram:user-a:subagent:implementer", "subagent prompt")

    subagent_root = agent.app_home / "logs" / "system-prompts" / "subagents"
    dated_dirs = list(subagent_root.iterdir())
    assert len(dated_dirs) == 1
    log_files = list(dated_dirs[0].glob("*.md"))
    assert len(log_files) == 1
    assert "subagent" in log_files[0].name


def test_main_prompt_logging_includes_available_subagent_summary(tmp_path, monkeypatch):
    agent = _make_logging_agent(tmp_path)
    info_messages: list[str] = []

    monkeypatch.setattr(agent_module.logger, "info", lambda message: info_messages.append(message))
    monkeypatch.setattr(prompt_logging_module.logger, "info", lambda message: info_messages.append(message))

    agent._log_prepared_messages(
        "telegram:user-a",
        [
            {
                "role": "system",
                "content": "# Available Subagents\n\n- `writer`: drafting helper\n- `researcher`: research helper\n",
            }
        ],
    )

    assert any(
        "[telegram:user-a] prompt.subagents | count=2 names=writer, researcher" in message
        for message in info_messages
    )


def test_format_log_preview_redacts_common_secret_shapes():
    preview = PromptLoggingService.format_log_preview(
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz "
        "Authorization: Bearer github_pat_abcdefghijklmnopqrstuvwxyz "
        "https://example.test/cb?code=oauth-code&state=ok "
        'payload={"token": "ghp_abcdefghijklmnopqrstuvwxyz"}',
        max_chars=500,
    )

    assert "sk-proj-abcdefghijklmnopqrstuvwxyz" not in preview
    assert "github_pat_abcdefghijklmnopqrstuvwxyz" not in preview
    assert "oauth-code" not in preview
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in preview
    assert "OPENAI_API_KEY=sk-pro...wxyz" in preview
    assert "Authorization: Bearer github...wxyz" in preview
    assert "code=***&state=ok" in preview
    assert '"token": "ghp_ab...wxyz"' in preview


def test_format_log_preview_redacts_private_key_blocks():
    preview = PromptLoggingService.format_log_preview(
        "before -----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY----- after",
        max_chars=500,
    )

    assert "secret" not in preview
    assert "[REDACTED PRIVATE KEY]" in preview
