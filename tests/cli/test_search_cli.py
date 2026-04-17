import asyncio
import json
from types import SimpleNamespace

from typer.testing import CliRunner

from opensprite.cli.commands import app
from opensprite.search.sqlite_store import SQLiteSearchStore
from opensprite.storage.base import StoredMessage
from opensprite.storage.sqlite import SQLiteStorage


runner = CliRunner()


def _write_config(path, db_path, *, search_enabled=True, history_top_k=5, knowledge_top_k=5, embedding=None):
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
                    "telegram": {"enabled": False},
                    "console": {"enabled": True},
                },
                "search": {
                    "enabled": search_enabled,
                    "history_top_k": history_top_k,
                    "knowledge_top_k": knowledge_top_k,
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


def test_search_rebuild_cli_rebuilds_index_from_messages(tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(config_path, db_path, search_enabled=True)

    async def scenario():
        storage = SQLiteStorage(db_path)
        await storage.add_message(
            "chat-a",
            StoredMessage(role="user", content="Please keep sqlite docs handy", timestamp=10.0),
        )
        await storage.add_message(
            "chat-a",
            StoredMessage(
                role="tool",
                content=json.dumps(
                    {
                        "type": "web_search",
                        "query": "sqlite fts5",
                        "url": "",
                        "final_url": "",
                        "title": "",
                        "content": "",
                        "summary": "Search results for: sqlite fts5",
                        "provider": "duckduckgo",
                        "extractor": "search",
                        "status": None,
                        "content_type": "application/json",
                        "items": [
                            {
                                "title": "SQLite FTS5",
                                "url": "https://sqlite.org/fts5.html",
                                "content": "Official full text search docs",
                            }
                        ],
                    }
                ),
                timestamp=11.0,
                tool_name="web_search",
            ),
        )

    asyncio.run(scenario())

    result = runner.invoke(app, ["search", "rebuild", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Rebuilt search index for all chats." in result.stdout
    assert "Chats: 1" in result.stdout
    assert "Messages: 2" in result.stdout
    assert "Knowledge sources: 1" in result.stdout

    async def verify():
        search = SQLiteSearchStore(db_path)
        history_hits = await search.search_history("chat-a", "keep sqlite handy")
        knowledge_hits = await search.search_knowledge("chat-a", "official full text docs")
        return history_hits, knowledge_hits

    history_hits, knowledge_hits = asyncio.run(verify())

    assert history_hits
    assert knowledge_hits
    assert knowledge_hits[0].source_type == "web_search"


def test_status_command_renders_search_top_k_values(tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(config_path, db_path, search_enabled=True, history_top_k=7, knowledge_top_k=9)

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Search: enabled=yes (history_top_k=7, knowledge_top_k=9)" in result.stdout


def test_search_status_cli_reports_index_and_embedding_counts(tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(config_path, db_path, search_enabled=True)

    async def scenario():
        storage = SQLiteStorage(db_path)
        search = SQLiteSearchStore(db_path, history_top_k=5, knowledge_top_k=5)
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
    assert "Search status for all chats." in result.stdout
    assert "Messages: 1" in result.stdout
    assert "Chunks: 1" in result.stdout
    assert "Embedding: enabled=no provider=openai model=<unset> retry_failed_on_startup=no" in result.stdout
    assert "Embedding jobs: total=0 queued=0 pending=0 processing=0 completed=0 failed=0 missing=0 stale=0" in result.stdout


def test_search_retry_embeddings_cli_reports_retried_jobs(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(
        config_path,
        db_path,
        search_enabled=True,
        embedding={
            "enabled": True,
            "provider": "openai",
            "api_key": "key",
            "model": "text-embedding-3-small",
            "base_url": None,
            "batch_size": 16,
            "candidate_count": 20,
            "retry_failed_on_startup": True,
        },
    )

    class FakeSearchStore:
        async def retry_failed_embeddings(self, chat_id=None, wait=True):
            assert chat_id == "telegram:user-a"
            assert wait is True
            return {
                "retried": 2,
                "embedding_total": 4,
                "queued": 0,
                "pending": 0,
                "processing": 0,
                "completed": 4,
                "failed": 0,
                "missing": 0,
                "stale": 0,
            }

    loaded = SimpleNamespace(
        storage=SimpleNamespace(path=str(db_path)),
        search=SimpleNamespace(
            embedding=SimpleNamespace(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
                retry_failed_on_startup=True,
            )
        ),
    )

    monkeypatch.setattr(
        "opensprite.cli.commands._load_sqlite_search_store",
        lambda config=None: (loaded, FakeSearchStore()),
    )

    result = runner.invoke(
        app,
        ["search", "retry-embeddings", "--config", str(config_path), "--chat-id", "telegram:user-a"],
    )

    assert result.exit_code == 0
    assert "Retried failed embeddings for telegram:user-a." in result.stdout
    assert "Retried: 2" in result.stdout
    assert "Embedding jobs: total=4 queued=0 pending=0 processing=0 completed=4 failed=0 missing=0 stale=0" in result.stdout


def test_search_refresh_embeddings_cli_reports_refreshed_jobs(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(
        config_path,
        db_path,
        search_enabled=True,
        embedding={
            "enabled": True,
            "provider": "openai",
            "api_key": "key",
            "model": "text-embedding-3-small",
            "base_url": None,
            "batch_size": 16,
            "candidate_count": 20,
            "retry_failed_on_startup": False,
        },
    )

    class FakeSearchStore:
        async def refresh_embeddings(self, chat_id=None, force=False, wait=True):
            assert chat_id == "telegram:user-a"
            assert force is True
            assert wait is True
            return {
                "refreshed": 3,
                "embedding_total": 4,
                "queued": 0,
                "pending": 0,
                "processing": 0,
                "completed": 4,
                "failed": 0,
                "missing": 0,
                "stale": 0,
            }

    loaded = SimpleNamespace(
        storage=SimpleNamespace(path=str(db_path)),
        search=SimpleNamespace(
            embedding=SimpleNamespace(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
                retry_failed_on_startup=False,
            )
        ),
    )

    monkeypatch.setattr(
        "opensprite.cli.commands._load_sqlite_search_store",
        lambda config=None: (loaded, FakeSearchStore()),
    )

    result = runner.invoke(
        app,
        ["search", "refresh-embeddings", "--config", str(config_path), "--chat-id", "telegram:user-a", "--force"],
    )

    assert result.exit_code == 0
    assert "Refreshed embeddings for telegram:user-a." in result.stdout
    assert "Refreshed: 3" in result.stdout
    assert "provider=openai model=text-embedding-3-small force=yes" in result.stdout
    assert "Embedding jobs: total=4 queued=0 pending=0 processing=0 completed=4 failed=0 missing=0 stale=0" in result.stdout
