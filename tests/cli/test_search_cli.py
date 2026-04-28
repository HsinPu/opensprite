import asyncio
import json
from types import SimpleNamespace

from typer.testing import CliRunner

from opensprite.cli.commands import app
from opensprite.search.base import SearchHit
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
                    "backend": "sqlite",
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
    assert "Rebuilt search index for all sessions." in result.stdout
    assert "Sessions: 1" in result.stdout
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
    assert "Search: enabled=yes backend=sqlite (history_top_k=7, knowledge_top_k=9)" in result.stdout


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
    assert "Search status for all sessions." in result.stdout
    assert "Messages: 1" in result.stdout
    assert "Chunks: 1" in result.stdout
    assert "Embedding: enabled=no provider=openai model=<unset> candidate_strategy=vector vector_backend=auto retry_failed_on_startup=no" in result.stdout
    assert "Embedding jobs: total=0 queued=0 pending=0 processing=0 completed=0 failed=0 missing=0 stale=0" in result.stdout
    assert "Vector backend: requested=auto effective=exact" in result.stdout
    assert "Queue worker: running=no owner=<none> expires=never" in result.stdout
    assert "Last queue run: mode=<none> started=never finished=never refreshed=0 processed=0 failed=0" in result.stdout


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
        async def retry_failed_embeddings(self, session_id=None, wait=True):
            assert session_id == "telegram:user-a"
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
        ["search", "retry-embeddings", "--config", str(config_path), "--session-id", "telegram:user-a"],
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
        async def refresh_embeddings(self, session_id=None, force=False, wait=True):
            assert session_id == "telegram:user-a"
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
        ["search", "refresh-embeddings", "--config", str(config_path), "--session-id", "telegram:user-a", "--force"],
    )

    assert result.exit_code == 0
    assert "Refreshed embeddings for telegram:user-a." in result.stdout
    assert "Refreshed: 3" in result.stdout
    assert "provider=openai model=text-embedding-3-small force=yes" in result.stdout
    assert "Embedding jobs: total=4 queued=0 pending=0 processing=0 completed=4 failed=0 missing=0 stale=0" in result.stdout


def test_search_run_queue_cli_reports_queue_run(monkeypatch, tmp_path):
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
        async def run_queue(self, once=True, poll_interval=5.0, idle_exit_seconds=None, force_refresh=False):
            assert once is True
            assert poll_interval == 5.0
            assert idle_exit_seconds is None
            assert force_refresh is True
            return {
                "embedding_total": 5,
                "queued": 0,
                "pending": 0,
                "processing": 0,
                "completed": 5,
                "failed": 0,
                "missing": 0,
                "stale": 0,
                "refreshed": 2,
                "processed_chunks": 3,
                "failed_chunks_run": 0,
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
        ["search", "run-queue", "--config", str(config_path), "--force-refresh"],
    )

    assert result.exit_code == 0
    assert "Ran search queue in once mode." in result.stdout
    assert "Embedding jobs: total=5 queued=0 pending=0 processing=0 completed=5 failed=0 missing=0 stale=0" in result.stdout
    assert "Queue run: refreshed=2 processed=3 failed=0" in result.stdout


def test_search_benchmark_cli_reports_both_strategies(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"

    loaded = SimpleNamespace(
        storage=SimpleNamespace(path=str(db_path)),
        search=SimpleNamespace(
            enabled=True,
            embedding=SimpleNamespace(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
            ),
        ),
    )

    class FakeStore:
        def __init__(self, strategy):
            self.strategy = strategy

    def fake_load(path):
        return loaded

    def fake_build(loaded_config, *, candidate_strategy=None, vector_backend=None, embedding_provider_override=None):
        return FakeStore(candidate_strategy)

    def fake_benchmark(store, **kwargs):
        hit = SearchHit(
            id=f"{store.strategy}-1",
            session_id="telegram:user-a",
            source_type="web_fetch",
            title=f"{store.strategy} result",
            content="content",
            created_at=1.0,
            score=0.9,
            url="https://example.com",
        )
        return (12.5 if store.strategy == "fts" else 7.5, [hit])

    monkeypatch.setattr("opensprite.config.Config.load", classmethod(lambda cls, path=None: fake_load(path)))
    monkeypatch.setattr("opensprite.cli.commands._build_sqlite_search_store", fake_build)
    monkeypatch.setattr("opensprite.cli.commands._benchmark_one_strategy", fake_benchmark)

    result = runner.invoke(
        app,
        [
            "search",
            "benchmark",
            "--session-id",
            "telegram:user-a",
            "--query",
            "sqlite guide",
            "--strategy",
            "both",
        ],
    )

    assert result.exit_code == 0
    assert "Search benchmark for telegram:user-a (knowledge)." in result.stdout
    assert "Strategy: fts" in result.stdout
    assert "Strategy: vector" in result.stdout
    assert "Elapsed: avg=" in result.stdout
    assert "fts result" in result.stdout
    assert "vector result" in result.stdout
    assert "Comparison: fts vs vector | overlap=" in result.stdout
    assert "Top hits: fts=" in result.stdout


def test_search_benchmark_cli_skips_vector_when_embeddings_disabled(monkeypatch):
    loaded = SimpleNamespace(
        storage=SimpleNamespace(path="sessions.db"),
        search=SimpleNamespace(
            enabled=True,
            embedding=SimpleNamespace(
                enabled=False,
                provider="openai",
                model="",
            ),
        ),
    )

    def fake_load(path):
        return loaded

    def fake_build(loaded_config, *, candidate_strategy=None, vector_backend=None, embedding_provider_override=None):
        return SimpleNamespace(strategy=candidate_strategy)

    def fake_benchmark(store, **kwargs):
        return (5.0, [])

    monkeypatch.setattr("opensprite.config.Config.load", classmethod(lambda cls, path=None: fake_load(path)))
    monkeypatch.setattr("opensprite.cli.commands._build_sqlite_search_store", fake_build)
    monkeypatch.setattr("opensprite.cli.commands._benchmark_one_strategy", fake_benchmark)

    result = runner.invoke(
        app,
        [
            "search",
            "benchmark",
            "--session-id",
            "telegram:user-a",
            "--query",
            "sqlite guide",
            "--strategy",
            "both",
        ],
    )

    assert result.exit_code == 0
    assert "Vector benchmark skipped because embeddings are disabled." in result.stdout
    assert "Strategy: fts" in result.stdout
    assert "Strategy: vector" not in result.stdout


def test_search_benchmark_cli_can_emit_json(monkeypatch):
    loaded = SimpleNamespace(
        storage=SimpleNamespace(path="sessions.db"),
        search=SimpleNamespace(
            enabled=True,
            embedding=SimpleNamespace(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
            ),
        ),
    )

    class FakeStore:
        def __init__(self, strategy):
            self.strategy = strategy

    def fake_load(path):
        return loaded

    def fake_build(loaded_config, *, candidate_strategy=None, vector_backend=None, embedding_provider_override=None):
        return FakeStore(candidate_strategy)

    def fake_benchmark(store, **kwargs):
        hit = SearchHit(
            id="hit-1",
            session_id="telegram:user-a",
            source_type="web_fetch",
            title="vector result",
            content="content",
            created_at=1.0,
            score=0.7,
            url="https://example.com",
        )
        return (6.0, [hit])

    monkeypatch.setattr("opensprite.config.Config.load", classmethod(lambda cls, path=None: fake_load(path)))
    monkeypatch.setattr("opensprite.cli.commands._build_sqlite_search_store", fake_build)
    monkeypatch.setattr("opensprite.cli.commands._benchmark_one_strategy", fake_benchmark)

    result = runner.invoke(
        app,
        [
            "search",
            "benchmark",
            "--session-id",
            "telegram:user-a",
            "--query",
            "sqlite guide",
            "--strategy",
            "vector",
            "--repeat",
            "2",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_id"] == "telegram:user-a"
    assert payload["repeat"] == 2
    assert payload["strategies"][0]["strategy"] == "vector"
    assert payload["strategies"][0]["summary"]["avg_ms"] == 6.0
    assert payload["strategies"][0]["hits"][0]["title"] == "vector result"
    assert payload["comparison"] == {}


def test_search_seed_demo_cli_seeds_benchmark_ready_data(tmp_path):
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "opensprite.json"
    _write_config(config_path, db_path, search_enabled=True)

    seed_result = runner.invoke(
        app,
        ["search", "seed-demo", "--config", str(config_path), "--session-id", "demo:bench"],
    )

    assert seed_result.exit_code == 0
    assert "Seeded demo search data for demo:bench." in seed_result.stdout
    assert "Messages: 7" in seed_result.stdout
    assert "Knowledge sources: 4" in seed_result.stdout

    benchmark_result = runner.invoke(
        app,
        [
            "search",
            "benchmark",
            "--config",
            str(config_path),
            "--session-id",
            "demo:bench",
            "--query",
            "orchard irrigation",
            "--strategy",
            "fts",
        ],
    )

    assert benchmark_result.exit_code == 0
    assert "Search benchmark for demo:bench (knowledge)." in benchmark_result.stdout
    assert "Strategy: fts" in benchmark_result.stdout
    assert "Orchard Irrigation Guide" in benchmark_result.stdout


def test_search_benchmark_cli_can_use_demo_embeddings(monkeypatch):
    loaded = SimpleNamespace(
        storage=SimpleNamespace(path="sessions.db"),
        search=SimpleNamespace(
            enabled=True,
            embedding=SimpleNamespace(
                enabled=False,
                provider="openai",
                model="",
            ),
        ),
    )

    class FakeStore:
        def __init__(self, strategy, embedding_provider_override=None):
            self.strategy = strategy
            self.embedding_provider = embedding_provider_override
            self.vector_backend_requested = "exact"
            self.vector_backend_effective = "exact"

        async def refresh_embeddings(self, force=False, wait=True):
            return {
                "refreshed": 2,
                "embedding_total": 2,
                "queued": 0,
                "pending": 0,
                "processing": 0,
                "completed": 2,
                "failed": 0,
                "missing": 0,
                "stale": 0,
            }

    def fake_load(path):
        return loaded

    def fake_build(loaded_config, *, candidate_strategy=None, vector_backend=None, embedding_provider_override=None):
        return FakeStore(candidate_strategy, embedding_provider_override=embedding_provider_override)

    def fake_benchmark(store, **kwargs):
        hit = SearchHit(
            id="hit-1",
            session_id="demo:bench",
            source_type="web_fetch",
            title="demo vector result",
            content="content",
            created_at=1.0,
            score=0.8,
            url="https://example.com",
        )
        return (4.0, [hit])

    monkeypatch.setattr("opensprite.config.Config.load", classmethod(lambda cls, path=None: fake_load(path)))
    monkeypatch.setattr("opensprite.cli.commands._build_sqlite_search_store", fake_build)
    monkeypatch.setattr("opensprite.cli.commands._benchmark_one_strategy", fake_benchmark)

    result = runner.invoke(
        app,
        [
            "search",
            "benchmark",
            "--session-id",
            "demo:bench",
            "--query",
            "orchard irrigation",
            "--strategy",
            "vector",
            "--demo-embeddings",
        ],
    )

    assert result.exit_code == 0
    assert "Strategy: vector" in result.stdout
    assert "demo vector result" in result.stdout


def test_search_benchmark_cli_passes_vector_backend_override(monkeypatch):
    loaded = SimpleNamespace(
        storage=SimpleNamespace(path="sessions.db"),
        search=SimpleNamespace(
            enabled=True,
            embedding=SimpleNamespace(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
                vector_backend="exact",
            ),
        ),
    )

    class FakeStore:
        def __init__(self, strategy, backend):
            self.strategy = strategy
            self.vector_backend_requested = backend
            self.vector_backend_effective = backend

    def fake_load(path):
        return loaded

    def fake_build(loaded_config, *, candidate_strategy=None, vector_backend=None, embedding_provider_override=None):
        return FakeStore(candidate_strategy, vector_backend)

    def fake_benchmark(store, **kwargs):
        hit = SearchHit(
            id="hit-1",
            session_id="telegram:user-a",
            source_type="web_fetch",
            title="sqlite vec result",
            content="content",
            created_at=1.0,
            score=0.9,
            url="https://example.com",
        )
        return (8.0, [hit])

    monkeypatch.setattr("opensprite.config.Config.load", classmethod(lambda cls, path=None: fake_load(path)))
    monkeypatch.setattr("opensprite.cli.commands._build_sqlite_search_store", fake_build)
    monkeypatch.setattr("opensprite.cli.commands._benchmark_one_strategy", fake_benchmark)

    result = runner.invoke(
        app,
        [
            "search",
            "benchmark",
            "--session-id",
            "telegram:user-a",
            "--query",
            "sqlite guide",
            "--strategy",
            "vector",
            "--vector-backend",
            "sqlite_vec",
        ],
    )

    assert result.exit_code == 0
    assert "Vector backend override: sqlite_vec" in result.stdout
    assert "requested=sqlite_vec effective=sqlite_vec" in result.stdout


def test_search_benchmark_cli_can_compare_vector_backends(monkeypatch):
    loaded = SimpleNamespace(
        storage=SimpleNamespace(path="sessions.db"),
        search=SimpleNamespace(
            enabled=True,
            embedding=SimpleNamespace(
                enabled=True,
                provider="openai",
                model="text-embedding-3-small",
                vector_backend="exact",
            ),
        ),
    )

    class FakeStore:
        def __init__(self, strategy, backend):
            self.strategy = strategy
            self.vector_backend_requested = backend
            self.vector_backend_effective = backend

    def fake_load(path):
        return loaded

    def fake_build(loaded_config, *, candidate_strategy=None, vector_backend=None, embedding_provider_override=None):
        return FakeStore(candidate_strategy, vector_backend)

    def fake_benchmark(store, **kwargs):
        title = "sqlite vec result" if store.vector_backend_requested == "sqlite_vec" else "exact result"
        hit = SearchHit(
            id=f"{store.vector_backend_requested}-1",
            session_id="telegram:user-a",
            source_type="web_fetch",
            title=title,
            content="content",
            created_at=1.0,
            score=0.9,
            url="https://example.com",
        )
        return (8.0, [hit])

    monkeypatch.setattr("opensprite.config.Config.load", classmethod(lambda cls, path=None: fake_load(path)))
    monkeypatch.setattr("opensprite.cli.commands._build_sqlite_search_store", fake_build)
    monkeypatch.setattr("opensprite.cli.commands._benchmark_one_strategy", fake_benchmark)

    result = runner.invoke(
        app,
        [
            "search",
            "benchmark",
            "--session-id",
            "telegram:user-a",
            "--query",
            "sqlite guide",
            "--strategy",
            "vector",
            "--vector-backend",
            "both",
        ],
    )

    assert result.exit_code == 0
    assert "Vector backend override: both" in result.stdout
    assert "Strategy: vector:exact" in result.stdout
    assert "Strategy: vector:sqlite_vec" in result.stdout
    assert "Comparison: vector:exact vs vector:sqlite_vec" in result.stdout
