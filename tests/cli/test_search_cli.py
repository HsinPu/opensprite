import asyncio
import json
from types import SimpleNamespace

from typer.testing import CliRunner

from opensprite.cli.commands import app
from opensprite.search.sqlite_store import SQLiteSearchStore
from opensprite.storage.base import StoredMessage
from opensprite.storage.sqlite import SQLiteStorage


runner = CliRunner()


def _write_config(path, db_path, *, search_enabled=True, history_top_k=5, embedding=None):
    path.write_text(
        json.dumps(
            {
                "llm": {
                    "api_key": "key",
                    "model": "gpt",
                    "temperature": 0.7,
                    "max_tokens": 2048,
                },
                "storage": {
                    "type": "sqlite",
                    "path": str(db_path),
                },
                "channels": {
                    "instances": {
                        "telegram": {"type": "telegram", "enabled": False},
                        "web": {"type": "web", "enabled": True},
                    }
                },
                "search": {
                    "enabled": search_enabled,
                    "backend": "sqlite",
                    "history_top_k": history_top_k,
                    "embedding": embedding
                    or {
                        "enabled": False,
                        "provider": "openai",
                        "api_key": "",
                        "model": "",
                        "base_url": None,
                        "batch_size": 16,
                        "candidate_count": 20,
                        "retry_failed_on_startup": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_status_command_renders_search_top_k_values(tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(config_path, db_path, search_enabled=True, history_top_k=7)

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Search: enabled=yes backend=sqlite (history_top_k=7)" in result.stdout


def test_search_status_cli_reports_index_and_embedding_counts(tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(config_path, db_path, search_enabled=True)

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(db_path, history_top_k=5)
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="Please keep sqlite docs handy", timestamp=10.0),
        )
        await search.index_message(
            "chat-a",
            role="user",
            content="Please keep sqlite docs handy",
            created_at=10.0,
        )

    asyncio.run(scenario())

    result = runner.invoke(app, ["search", "status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Chat history search status for all sessions." in result.stdout
    assert "Messages: 1" in result.stdout
    assert "Chunks: 1" in result.stdout
    assert "Embedding: enabled=no provider=openai model=<unset> candidate_strategy=vector vector_backend=auto retry_failed_on_startup=no" in result.stdout
    assert "Embedding jobs: total=0 queued=0 pending=0 processing=0 completed=0 failed=0 missing=0 stale=0" in result.stdout
    assert "Vector backend: requested=auto effective=exact" in result.stdout
    assert "Queue worker: running=no owner=<none> expires=never" in result.stdout
    assert "Last queue run: mode=<none> started=never finished=never refreshed=0 processed=0 failed=0" in result.stdout


def test_search_help_only_exposes_status_command():
    result = runner.invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    assert "status" in result.stdout
    assert "rebuild" not in result.stdout
