import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from aiohttp import ClientSession, WSServerHandshakeError, web

from opensprite.bus.dispatcher import MessageQueue
from opensprite.bus.events import RunEvent, SessionStatusEvent
from opensprite.bus.message import AssistantMessage
from opensprite.channels.web import WebAdapter
from opensprite.channels.web_routes import register_web_routes
from opensprite.config import Config, ProviderConfig
from opensprite.auth.codex import CodexToken, delete_codex_token, save_codex_token
import opensprite.auth.codex as codex_module
from opensprite.context.paths import get_session_workspace
from opensprite.cron import CronManager, CronSchedule, CronService
from opensprite.runs.events import (
    COMPLETION_GATE_EVALUATED_EVENT,
    TASK_INTENT_DETECTED_EVENT,
    TOOL_RESULT_EVENT,
    TOOL_STARTED_EVENT,
    WORKTREE_CLEANUP_COMPLETED_EVENT,
    WORKTREE_CLEANUP_STARTED_EVENT,
)
from opensprite.runs.lifecycle import RUN_FINISHED_EVENT, RUN_STARTED_EVENT
from opensprite.storage import MemoryStorage, StoredDelegatedTask, StoredMessage, StoredWorkState
from opensprite.tools.registry import ToolRegistry
from opensprite.storage.base import StoredBackgroundProcess
from opensprite.tools.process_runtime import BackgroundProcessManager


class EchoAgent:
    def __init__(self):
        self.seen_messages = []

    async def process(self, user_message):
        self.seen_messages.append(user_message)
        return AssistantMessage(
            text=f"echo:{user_message.text}",
            channel="web",
            external_chat_id=user_message.external_chat_id,
            session_id=user_message.session_id,
            metadata={"source": "test"},
        )


class FakeWebSocket:
    def __init__(self):
        self.closed = False
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def test_web_adapter_broadcasts_same_session_replies_and_events_to_all_sockets():
    adapter = WebAdapter(mq=None, config={"frontend_auto_build": False})
    session_id = "web:same-session"
    first = FakeWebSocket()
    second = FakeWebSocket()

    adapter._bind_session(session_id, first)
    adapter._bind_session(session_id, second)

    asyncio.run(
        adapter.send(
            AssistantMessage(
                text="done",
                channel="web",
                external_chat_id="same-session",
                session_id=session_id,
            )
        )
    )
    asyncio.run(
        adapter.send_run_event(
            RunEvent(
                channel="web",
                external_chat_id="same-session",
                run_id="run_123",
                session_id=session_id,
                event_type=RUN_FINISHED_EVENT,
                payload={"status": "completed"},
            )
        )
    )

    assert [item["type"] for item in first.sent] == ["message", "run_event"]
    assert [item["type"] for item in second.sent] == ["message", "run_event"]

    adapter._unbind_socket(first)
    asyncio.run(
        adapter.send(
            AssistantMessage(
                text="after",
                channel="web",
                external_chat_id="same-session",
                session_id=session_id,
            )
        )
    )

    assert len(first.sent) == 2
    assert second.sent[-1]["text"] == "after"


class _FakeSearxngConfigResponse:
    def __init__(self, *, error: Exception | None = None):
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error
        return None

    def json(self):
        return {
            "engines": [
                {"name": "google", "shortcut": "go", "categories": ["general"], "enabled": True},
                {"name": "bing", "shortcut": "bi", "categories": ["general", "news"], "enabled": True},
            ],
            "categories": ["general", "news"],
        }


class _FakeSearxngConfigClient:
    def __init__(self, *, error: Exception | None = None):
        self.requests = []
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers=None, timeout=None):
        self.requests.append((url, headers, timeout))
        return _FakeSearxngConfigResponse(error=self.error)


async def _stub_handler(request):
    return web.json_response({"ok": True})


class _FakeWebApi:
    def __getattr__(self, name):
        if name.startswith("handle_"):
            return _stub_handler
        raise AttributeError(name)


class _FakeRouteAdapter:
    def __init__(self):
        self.app = web.Application()
        self._api = _FakeWebApi()
        self._frontend_dir = None

    def __getattr__(self, name):
        if name.startswith("_handle_"):
            return _stub_handler
        raise AttributeError(name)


def test_register_web_routes_keeps_core_entrypoints():
    adapter = _FakeRouteAdapter()

    register_web_routes(adapter, ws_path="/ws", health_path="/healthz")

    routes = {(route.method, route.resource.canonical) for route in adapter.app.router.routes()}

    assert ("GET", "/ws") in routes
    assert ("GET", "/healthz") in routes
    assert ("GET", "/api/runs") in routes
    assert ("GET", "/api/settings/llm") in routes
    assert ("POST", "/api/settings/update") in routes
    assert ("GET", "/") in routes


async def _run_web_roundtrip():
    agent = EchoAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload == {"ok": True, "channel": "web", "channel_type": "web"}

            async with session.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                session_frame = await ws.receive_json(timeout=2)
                assert session_frame["type"] == "session"
                assert session_frame["channel"] == "web"
                assert session_frame["channel_type"] == "web"
                assert session_frame["session_id"].startswith("web:")

                await queue.bus.publish_run_event(
                    RunEvent(
                        channel="web",
                        external_chat_id=session_frame["external_chat_id"],
                        session_id=session_frame["session_id"],
                        run_id="run-test",
                        event_type=RUN_STARTED_EVENT,
                        payload={"status": "running"},
                        created_at=123.0,
                    )
                )
                run_frame = await ws.receive_json(timeout=2)
                assert run_frame == {
                    "type": "run_event",
                    "schema_version": 1,
                    "channel": "web",
                    "channel_type": "web",
                    "external_chat_id": session_frame["external_chat_id"],
                    "session_id": session_frame["session_id"],
                    "run_id": "run-test",
                    "event_type": RUN_STARTED_EVENT,
                    "kind": "run",
                    "status": "running",
                    "payload": {"status": "running"},
                    "artifact": None,
                    "created_at": 123.0,
                }

                auto_status_frame = await ws.receive_json(timeout=2)
                assert auto_status_frame["type"] == "session_status"
                assert auto_status_frame["session_id"] == session_frame["session_id"]
                assert auto_status_frame["status"] == "thinking"
                assert auto_status_frame["metadata"]["run_id"] == "run-test"

                await queue.bus.publish_run_event(
                    RunEvent(
                        channel="telegram",
                        external_chat_id="chat-42",
                        session_id="telegram:chat-42",
                        run_id="run-telegram",
                        event_type=RUN_STARTED_EVENT,
                        payload={"status": "running"},
                        created_at=125.0,
                    )
                )
                external_run_frame = await ws.receive_json(timeout=2)
                assert external_run_frame["type"] == "run_event"
                assert external_run_frame["channel"] == "telegram"
                assert external_run_frame["external_chat_id"] == "chat-42"
                assert external_run_frame["session_id"] == "telegram:chat-42"
                assert external_run_frame["run_id"] == "run-telegram"

                external_status_frame = await ws.receive_json(timeout=2)
                assert external_status_frame["type"] == "session_status"
                assert external_status_frame["channel"] == "telegram"
                assert external_status_frame["session_id"] == "telegram:chat-42"
                assert external_status_frame["metadata"]["run_id"] == "run-telegram"

                await queue.bus.publish_session_status(
                    SessionStatusEvent(
                        session_id=session_frame["session_id"],
                        status="thinking",
                        metadata={"channel": "web", "external_chat_id": session_frame["external_chat_id"]},
                        updated_at=124.0,
                    )
                )
                status_frame = await ws.receive_json(timeout=2)
                assert status_frame == {
                    "type": "session_status",
                    "channel": "web",
                    "session_id": session_frame["session_id"],
                    "status": "thinking",
                    "updated_at": 124.0,
                    "metadata": {"channel": "web", "external_chat_id": session_frame["external_chat_id"]},
                }

                async def receive_message_frame():
                    for _ in range(5):
                        frame = await ws.receive_json(timeout=2)
                        if frame.get("type") == "message":
                            return frame
                    raise AssertionError("message frame not received")

                await ws.send_str("hello from browser")
                reply = await receive_message_frame()
                assert reply == {
                    "type": "message",
                    "channel": "web",
                    "channel_type": "web",
                    "external_chat_id": session_frame["external_chat_id"],
                    "session_id": session_frame["session_id"],
                    "text": "echo:hello from browser",
                    "metadata": {"source": "test"},
                }

                await ws.send_json({"external_chat_id": "browser-2", "text": "second round"})
                second_reply = await receive_message_frame()
                assert second_reply["external_chat_id"] == "browser-2"
                assert second_reply["session_id"] == "web:browser-2"
                assert second_reply["text"] == "echo:second round"

                await ws.send_json({
                    "external_chat_id": "browser-2",
                    "text": "continue",
                    "metadata": {
                        "quick_action": "resume_follow_up",
                        "follow_up_workflow": "implement_then_review",
                        "follow_up_step_id": "review",
                    },
                })
                third_reply = await receive_message_frame()
                assert third_reply["text"] == "echo:continue"

        seen_sessions = [message.session_id for message in agent.seen_messages]
        assert seen_sessions == [session_frame["session_id"], "web:browser-2", "web:browser-2"]
        assert agent.seen_messages[2].metadata["quick_action"] == "resume_follow_up"
        assert agent.seen_messages[2].metadata["follow_up_workflow"] == "implement_then_review"
        assert agent.seen_messages[2].metadata["follow_up_step_id"] == "review"
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_roundtrip():
    asyncio.run(_run_web_roundtrip())


def test_web_adapter_refuses_non_loopback_without_auth_token():
    adapter = WebAdapter(
        mq=MessageQueue(EchoAgent()),
        config={"host": "0.0.0.0", "frontend_auto_build": False},
    )

    try:
        adapter._validate_bind_auth_config("0.0.0.0")
    except RuntimeError as exc:
        assert "non-loopback host without auth_token" in str(exc)
    else:
        raise AssertionError("expected non-loopback bind without auth_token to fail")


def test_web_adapter_allows_loopback_without_auth_token():
    adapter = WebAdapter(
        mq=MessageQueue(EchoAgent()),
        config={"host": "127.0.0.1", "frontend_auto_build": False},
    )

    adapter._validate_bind_auth_config("127.0.0.1")
    adapter._validate_bind_auth_config("localhost")
    adapter._validate_bind_auth_config("::1")


async def _run_web_auth_token_guard():
    agent = EchoAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
            "auth_token": "test-token",
        },
    )
    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200

            async with session.get(f"http://127.0.0.1:{port}/api/commands") as resp:
                assert resp.status == 401

            async with session.get(
                f"http://127.0.0.1:{port}/api/commands",
                headers={"Authorization": "Bearer test-token"},
            ) as resp:
                assert resp.status == 200

            try:
                await session.ws_connect(f"ws://127.0.0.1:{port}/ws")
            except WSServerHandshakeError as exc:
                assert exc.status == 401
            else:
                raise AssertionError("expected websocket without token to be rejected")

            async with session.ws_connect(f"ws://127.0.0.1:{port}/ws?access_token=test-token") as ws:
                session_frame = await ws.receive_json(timeout=2)
                assert session_frame["type"] == "session"
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_auth_token_guard():
    asyncio.run(_run_web_auth_token_guard())


def test_web_frontend_command_decodes_utf8_output(monkeypatch, tmp_path):
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="✓ built", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = WebAdapter(mq=MessageQueue(EchoAgent()), config={"frontend_auto_build": False})

    result = adapter._run_frontend_command(tmp_path, ["npm.cmd", "run", "build"], 10)

    assert result.stdout == "✓ built"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    if os.name == "nt":
        assert captured["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "startupinfo" in captured


async def _run_web_command_catalog_api():
    agent = EchoAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/commands") as resp:
                assert resp.status == 200
                payload = await resp.json()

        commands = {item["name"]: item for item in payload["commands"]}
        assert commands["help"]["usage"] == "/help [command]"
        assert commands["goal"]["usage"] == "/goal <objective>"
        assert commands["goal"]["category"] == "Work"
        assert commands["curator"]["subcommands"] == ["status", "history", "run", "pause", "resume", "help"]
        assert commands["curator"]["category"] == "Maintenance"
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_command_catalog_api():
    asyncio.run(_run_web_command_catalog_api())


async def _run_web_curator_api():
    class CuratorAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.curator_calls = []

        async def get_curator_status(self, session_id):
            self.curator_calls.append(("status", session_id))
            return {
                "session_id": session_id,
                "state": "idle",
                "running": False,
                "queued": False,
                "paused": False,
                "rerun_pending": False,
                "jobs": [],
                "run_count": 2,
                "last_run_summary": "No curator changes.",
            }

        async def get_curator_history(self, session_id, *, limit=10):
            self.curator_calls.append(("history", session_id, limit))
            return [
                {
                    "run_id": "run-2",
                    "run_at": "2026-05-01T00:00:02Z",
                    "jobs": ["skills"],
                    "changed": ["skills"],
                    "summary": "Updated skills.",
                    "error": None,
                    "status": "completed",
                }
            ]

        async def run_curator_now(self, session_id, *, scope=None, channel=None, external_chat_id=None):
            if scope not in {None, "memory"}:
                raise ValueError(f"Unknown curator scope: {scope}")
            self.curator_calls.append(("run", session_id, scope, channel, external_chat_id))
            return {
                "session_id": session_id,
                "state": "queued",
                "running": False,
                "queued": True,
                "paused": False,
                "rerun_pending": False,
                "jobs": ["memory"],
                "scheduled": True,
            }

        async def pause_curator(self, session_id):
            self.curator_calls.append(("pause", session_id))
            return {"session_id": session_id, "state": "paused", "paused": True}

        async def resume_curator(self, session_id):
            self.curator_calls.append(("resume", session_id))
            return {"session_id": session_id, "state": "idle", "paused": False}

    agent = CuratorAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None
        session_id = "web:browser-1"

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/curator/status?session_id={session_id}") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["status"]["run_count"] == 2
                assert payload["status"]["last_run_summary"] == "No curator changes."

            async with session.get(f"http://127.0.0.1:{port}/api/curator/history?session_id={session_id}&limit=1") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert len(payload["history"]) == 1
                assert payload["history"][0]["run_id"] == "run-2"

            async with session.post(f"http://127.0.0.1:{port}/api/curator/run?session_id={session_id}") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["action"] == "run"
                assert payload["status"]["scheduled"] is True

            async with session.post(f"http://127.0.0.1:{port}/api/curator/run?session_id={session_id}&scope=memory") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["status"]["scheduled"] is True

            async with session.post(f"http://127.0.0.1:{port}/api/curator/run?session_id={session_id}&scope=nope") as resp:
                assert resp.status == 400
                assert "Unknown curator scope: nope" in await resp.text()

            telegram_session_id = "telegram:chat-1"
            async with session.post(f"http://127.0.0.1:{port}/api/curator/run?session_id={telegram_session_id}") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["status"]["scheduled"] is True

            async with session.post(f"http://127.0.0.1:{port}/api/curator/pause?session_id={session_id}") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["status"]["paused"] is True

            async with session.post(f"http://127.0.0.1:{port}/api/curator/resume?session_id={session_id}") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["status"]["paused"] is False

        assert agent.curator_calls == [
            ("status", session_id),
            ("history", session_id, 1),
            ("run", session_id, None, "web", "browser-1"),
            ("run", session_id, "memory", "web", "browser-1"),
            ("run", telegram_session_id, None, "telegram", "chat-1"),
            ("pause", session_id),
            ("resume", session_id),
        ]
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_curator_api():
    asyncio.run(_run_web_curator_api())


async def _run_web_static_serving(tmp_path: Path):
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "index.html").write_text(
        "<!doctype html><html><body><h1>OpenSprite static shell</h1><script src=\"./app.js\"></script></body></html>",
        encoding="utf-8",
    )
    (frontend_dir / "app.js").write_text("window.testLoaded = true;", encoding="utf-8")

    agent = EchoAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "static_dir": str(frontend_dir),
            "frontend_auto_build": False,
        },
    )
    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "OpenSprite static shell" in html

            async with session.get(f"http://127.0.0.1:{port}/app.js") as resp:
                assert resp.status == 200
                script = await resp.text()
                assert "window.testLoaded = true;" in script

            async with session.get(f"http://127.0.0.1:{port}/missing.js") as resp:
                assert resp.status == 404
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_serves_static_frontend(tmp_path):
    asyncio.run(_run_web_static_serving(tmp_path))


async def _run_web_source_static_dir_serves_dist(tmp_path: Path):
    source_dir = tmp_path / "web"
    dist_dir = source_dir / "dist"
    dist_dir.mkdir(parents=True)
    (source_dir / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (source_dir / "index.html").write_text(
        '<!doctype html><html><body><script type="module" src="/src/main.js"></script></body></html>',
        encoding="utf-8",
    )
    (dist_dir / "index.html").write_text(
        "<!doctype html><html><body><h1>OpenSprite built shell</h1></body></html>",
        encoding="utf-8",
    )

    agent = EchoAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "static_dir": str(source_dir),
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "OpenSprite built shell" in html
                assert "/src/main.js" not in html
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_static_source_dir_serves_dist(tmp_path):
    asyncio.run(_run_web_source_static_dir_serves_dist(tmp_path))


async def _run_web_frontend_unavailable_response(tmp_path: Path):
    missing_frontend = tmp_path / "missing-frontend"
    agent = EchoAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "static_dir": str(missing_frontend),
            "frontend_auto_build": False,
        },
    )
    adapter._frontend_dir = None

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 503
                body = await resp.text()
                assert "OpenSprite web frontend is not built yet" in body
                assert "Node.js 20.19+ or 22.12+" in body
                assert "run build" in body
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_root_explains_missing_frontend(tmp_path):
    asyncio.run(_run_web_frontend_unavailable_response(tmp_path))


def test_effective_llm_request_uses_provider_profile_request_options(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    config = Config.from_json(config_path)
    config.llm.providers = {
        "openrouter": ProviderConfig(
            provider="openrouter",
            api_key="router-key",
            model="anthropic/claude-sonnet-4.6",
            enabled=True,
            provider_sort="latency",
            require_parameters=True,
        ),
        "openai": ProviderConfig(
            provider="openai",
            api_key="openai-key",
            model="gpt-5.5",
            enabled=True,
            provider_sort="latency",
            require_parameters=True,
        ),
    }

    config.llm.default = "openrouter"
    openrouter_payload = WebAdapter._effective_llm_request_payload(config)
    assert openrouter_payload["reasoning"]["source"] == "openrouter"
    assert openrouter_payload["reasoning"]["payload"] == {"effort": "medium"}
    assert openrouter_payload["provider_options"] == {"sort": "latency", "require_parameters": True}

    config.llm.default = "openai"
    openai_payload = WebAdapter._effective_llm_request_payload(config)
    assert openai_payload["reasoning"]["source"] == "none"
    assert openai_payload["reasoning"]["payload"] == {}
    assert openai_payload["provider_options"] == {}


def test_effective_llm_request_uses_provider_profile_api_mode(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    config = Config.from_json(config_path)
    config.llm.providers = {
        "minimax": ProviderConfig(
            provider="minimax",
            api_key="minimax-key",
            model="MiniMax-M2.7",
            enabled=True,
            reasoning_enabled=True,
            reasoning_effort="high",
        )
    }
    config.llm.default = "minimax"

    payload = WebAdapter._effective_llm_request_payload(config)

    assert payload["provider"] == "minimax"
    assert payload["api_mode"] == "anthropic_messages"
    assert payload["context_window_tokens"] == 204800
    assert payload["reasoning"]["source"] == "anthropic_messages"
    assert payload["reasoning"]["payload"] == {
        "thinking": {"type": "enabled", "budget_tokens": 16000},
        "temperature": 1,
        "max_tokens": 32768,
    }


async def _run_web_network_settings_roundtrip(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    agent = EchoAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/network") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["network"]["no_proxy"] == "127.0.0.1,localhost"

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/network",
                json={
                    "http_proxy": "http://proxy.local:8080",
                    "https_proxy": "http://proxy.local:8443",
                    "no_proxy": "127.0.0.1,localhost,.internal",
                },
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["restart_required"] is False
                assert payload["network"]["https_proxy"] == "http://proxy.local:8443"

        loaded = Config.from_json(config_path)
        assert loaded.network.http_proxy == "http://proxy.local:8080"
        assert loaded.network.https_proxy == "http://proxy.local:8443"
        assert loaded.network.no_proxy == "127.0.0.1,localhost,.internal"
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_network_settings_roundtrip(tmp_path):
    asyncio.run(_run_web_network_settings_roundtrip(tmp_path))


async def _run_web_permission_settings_roundtrip(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    agent = EchoAgent()
    agent.config_path = config_path
    agent.tools = ToolRegistry()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/permissions") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["permissions"]["enabled"] is True
                assert payload["permissions"]["risk_level_options"] == [
                    "read",
                    "write",
                    "execute",
                    "network",
                    "external_side_effect",
                    "configuration",
                    "delegation",
                    "memory",
                    "mcp",
                ]
                assert payload["permissions"]["approval_mode_options"] == ["auto", "ask", "block"]
                assert payload["permissions"]["profile_overrides"] == {}

            async with session.get(f"http://127.0.0.1:{port}/api/settings/harness-policy-preview") as resp:
                assert resp.status == 200
                payload = await resp.json()
                preview = payload["harness_policy_preview"]
                assert preview["schema_version"] == 1
                assert preview["user_permissions"]["approval_mode"] == "auto"
                policies = {row["policy"]["name"]: row for row in preview["rows"]}
                assert "chat_read_policy" in policies
                assert "workspace_change_policy" in policies
                assert policies["chat_read_policy"]["profile_override"] == {}
                assert set(policies["chat_read_policy"]["user"]["allowed_risk_levels"]) >= {"read", "network", "write"}
                assert set(policies["chat_read_policy"]["effective"]["allowed_risk_levels"]) >= {"read", "network", "write"}
                assert policies["workspace_change_policy"]["effective"]["denied_risk_levels"] == []
                assert policies["operations_approval_policy"]["profile_override"] == {}
                assert "mcp" in policies["operations_approval_policy"]["policy"]["approval_required_risk_levels"]

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/permissions",
                json={
                    "enabled": True,
                    "approval_mode": "ask",
                    "allowed_tools": ["*"],
                    "denied_tools": ["dangerous_tool"],
                    "allowed_risk_levels": ["read", "write", "execute", "network", "external_side_effect", "configuration"],
                    "denied_risk_levels": ["mcp"],
                    "approval_required_tools": ["credential_store"],
                    "approval_required_risk_levels": ["external_side_effect", "configuration"],
                    "profile_overrides": {
                        "research": {
                            "allowed_risk_levels": ["read"],
                            "denied_risk_levels": ["network"],
                        },
                        "ops": {
                            "approval_mode": "ask",
                            "approval_required_risk_levels": ["configuration", "mcp"],
                        },
                    },
                },
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["restart_required"] is False
                assert payload["runtime_reloaded"] is True
                assert payload["permissions"]["approval_mode"] == "ask"
                assert payload["permissions"]["denied_tools"] == ["dangerous_tool"]
                assert payload["permissions"]["approval_required_risk_levels"] == ["external_side_effect", "configuration"]
                assert payload["permissions"]["profile_overrides"]["research"]["denied_risk_levels"] == ["network"]
                assert payload["operation_audit"]["operation_type"] == "settings.permissions.update"
                assert payload["operation_audit"]["target"] == "tools.permissions"
                assert payload["operation_audit"]["rollback_available"] is True
                assert payload["operation_audit"]["before"]["approval_mode"] == "auto"
                assert payload["operation_audit"]["after"]["approval_mode"] == "ask"

            async with session.get(f"http://127.0.0.1:{port}/api/settings/harness-policy-preview") as resp:
                assert resp.status == 200
                payload = await resp.json()
                policies = {row["policy"]["name"]: row for row in payload["harness_policy_preview"]["rows"]}
                assert policies["operations_approval_policy"]["effective"]["user_approval_mode"] == "ask"
                assert "configuration" in policies["operations_approval_policy"]["effective"]["approval_required_risk_levels"]
                assert "mcp" in policies["operations_approval_policy"]["effective"]["denied_risk_levels"]
                assert policies["research_source_policy"]["user"]["allowed_risk_levels"] == ["read"]
                assert "network" in policies["research_source_policy"]["user"]["denied_risk_levels"]
                assert policies["research_source_policy"]["effective"]["allowed_risk_levels"] == ["read"]

        loaded = Config.load(config_path)
        assert loaded.tools.permissions.approval_mode == "ask"
        assert loaded.tools.permissions.denied_tools == ["dangerous_tool"]
        assert loaded.tools.permissions.denied_risk_levels == ["mcp"]
        assert loaded.tools.permissions.approval_required_tools == ["credential_store"]
        assert loaded.tools.permissions.profile_overrides["research"].denied_risk_levels == ["network"]
        assert agent.tools_config.permissions.approval_required_risk_levels == ["external_side_effect", "configuration"]
        decision = agent.tools.permission_policy.check("browser_click", {})
        assert decision.allowed is False
        assert decision.requires_approval is True
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_permission_settings_roundtrip(tmp_path):
    asyncio.run(_run_web_permission_settings_roundtrip(tmp_path))


async def _run_web_browser_settings_roundtrip(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    class BrowserReloadAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.reloads = []

        def reload_browser_from_config(self, config):
            browser = config.tools.browser
            self.reloads.append({
                "enabled": browser.enabled,
                "backend": browser.backend,
                "command_timeout": browser.command_timeout,
                "session_timeout": browser.session_timeout,
                "launch_args": browser.launch_args,
            })
            return {
                "enabled": browser.enabled,
                "backend": browser.backend,
                "command_timeout": browser.command_timeout,
                "session_timeout": browser.session_timeout,
                "launch_args": browser.launch_args,
                "tool_updated": browser.enabled,
                "tool_removed": False,
            }

    agent = BrowserReloadAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/browser") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["browser"]["enabled"] is False
                assert payload["browser"]["backend"] == "agent-browser"
                assert payload["browser"]["backends"] == ["agent-browser", "browserbase", "browser-use", "firecrawl"]
                assert payload["browser"]["cloud"]["browserbase"]["configured"] is False
                assert "install_hint" in payload["browser"]["runtime"]

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/browser",
                json={
                    "enabled": True,
                    "backend": "agent-browser",
                    "command_timeout": 45,
                    "session_timeout": 600,
                    "cdp_url": "http://127.0.0.1:9222",
                    "launch_args": "--no-sandbox",
                    "allow_private_urls": True,
                },
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["restart_required"] is False
                assert payload["runtime_reloaded"] is True
                assert payload["runtime"] == {
                    "enabled": True,
                    "backend": "agent-browser",
                    "command_timeout": 45,
                    "session_timeout": 600,
                    "launch_args": "--no-sandbox",
                    "tool_updated": True,
                    "tool_removed": False,
                }
                assert payload["browser"]["enabled"] is True
                assert payload["browser"]["command_timeout"] == 45
                assert payload["browser"]["cdp_url"] == "http://127.0.0.1:9222"
                assert payload["browser"]["launch_args"] == "--no-sandbox"

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/browser",
                json={"cdp_url": ""},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["restart_required"] is False
                assert payload["browser"]["cdp_url"] == ""

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/browser",
                json={
                    "backend": "browserbase",
                    "browserbase_api_key": "bb-key",
                    "browserbase_project_id": "project-1",
                    "browserbase_proxies": False,
                    "browserbase_advanced_stealth": True,
                    "browserbase_keep_alive": False,
                    "browser_use_api_key": "bu-key",
                    "firecrawl_api_key": "fc-key",
                },
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["restart_required"] is False
                assert payload["runtime_reloaded"] is True
                assert payload["browser"]["backend"] == "browserbase"
                assert payload["browser"]["cloud"]["browserbase"]["configured"] is True
                assert payload["browser"]["cloud"]["browserbase"]["api_key_configured"] is True
                assert payload["browser"]["cloud"]["browserbase"]["project_id"] == "project-1"
                assert "bb-key" not in json.dumps(payload)

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/browser",
                json={"backend": "unknown"},
            ) as resp:
                assert resp.status == 400

        loaded = Config.from_json(config_path)
        assert loaded.tools.browser.enabled is True
        assert loaded.tools.browser.backend == "browserbase"
        assert loaded.tools.browser.command_timeout == 45
        assert loaded.tools.browser.session_timeout == 600
        assert loaded.tools.browser.cdp_url == ""
        assert loaded.tools.browser.launch_args == "--no-sandbox"
        assert loaded.tools.browser.allow_private_urls is True
        assert loaded.tools.browser.browserbase_api_key == "bb-key"
        assert loaded.tools.browser.browserbase_project_id == "project-1"
        assert loaded.tools.browser.browserbase_proxies is False
        assert loaded.tools.browser.browserbase_advanced_stealth is True
        assert loaded.tools.browser.browserbase_keep_alive is False
        assert loaded.tools.browser.browser_use_api_key == "bu-key"
        assert loaded.tools.browser.firecrawl_api_key == "fc-key"
        assert len(agent.reloads) == 3
        assert agent.reloads[-1]["backend"] == "browserbase"
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_browser_settings_roundtrip(tmp_path):
    asyncio.run(_run_web_browser_settings_roundtrip(tmp_path))


async def _run_web_browser_settings_manual_test(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    config = Config.from_json(config_path)
    config.tools.browser.enabled = True
    config.save(config_path)

    calls = []

    async def fake_run(self, *, session_key, command, args=None, timeout=None):
        calls.append({"session_key": session_key, "command": command, "args": args or [], "timeout": timeout})
        if command == "open":
            return {"success": True, "url": (args or [""])[0]}
        if command == "snapshot":
            return {"success": True, "text": "Quotes to Scrape @e1"}
        return {"success": False, "error": "unexpected command"}

    monkeypatch.setattr("opensprite.channels.web.AgentBrowserRuntime.run", fake_run)

    agent = EchoAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/browser/test",
                json={"url": "https://quotes.toscrape.com/js/"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["ok"] is True
                assert payload["diagnostic_code"] == "ok"
                assert payload["url"] == "https://quotes.toscrape.com/js/"
                assert payload["open"]["success"] is True
                assert payload["open"]["diagnostic_code"] == "ok"
                assert payload["snapshot"]["success"] is True
                assert payload["snapshot"]["diagnostic_code"] == "ok"
                assert payload["browser"]["enabled"] is True

        assert [call["command"] for call in calls] == ["open", "snapshot"]
        assert calls[0]["args"] == ["https://quotes.toscrape.com/js/"]
        assert calls[1]["args"] == ["-c"]
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_browser_settings_manual_test(tmp_path, monkeypatch):
    asyncio.run(_run_web_browser_settings_manual_test(tmp_path, monkeypatch))


def test_browser_diagnostic_classifies_common_failures():
    sandbox = WebAdapter._with_browser_diagnostic(
        {
            "ok": False,
            "stderr": "FATAL: No usable sandbox! Hint: try --args \"--no-sandbox\"",
        }
    )
    missing = WebAdapter._with_browser_diagnostic(
        {
            "ok": False,
            "stderr": "Executable doesn't exist at /tmp/chromium",
        }
    )
    deps = WebAdapter._with_browser_diagnostic(
        {
            "ok": False,
            "stderr": "Missing dependencies. Please run install --with-deps.",
        }
    )

    assert sandbox["diagnostic_code"] == "sandbox_unavailable"
    assert "--no-sandbox" in sandbox["suggestion"]
    assert missing["diagnostic_code"] == "browser_missing"
    assert "agent-browser install" in missing["suggestion"]
    assert deps["diagnostic_code"] == "system_deps_missing"
    assert "install --with-deps" in deps["suggestion"]


async def _run_web_browser_settings_doctor(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    doctor_calls = []

    async def fake_doctor_command(args, *, timeout=20, launch_args=""):
        doctor_calls.append({"args": list(args), "launch_args": launch_args})
        if args == ["--version"]:
            return {"ok": True, "exit_code": 0, "stdout": "agent-browser 1.2.3", "stderr": ""}
        if args == ["doctor"]:
            return {"ok": True, "exit_code": 0, "stdout": "Browser install looks good", "stderr": ""}
        return {"ok": False, "exit_code": 2, "stdout": "", "stderr": "unexpected command"}

    monkeypatch.setattr(WebAdapter, "_browser_command_prefix", staticmethod(lambda: ["agent-browser"]))
    monkeypatch.setattr(WebAdapter, "_run_browser_doctor_command", staticmethod(fake_doctor_command))

    agent = EchoAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(f"http://127.0.0.1:{port}/api/settings/browser/doctor") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["ok"] is True
                assert payload["runtime"]["available"] is True
                assert payload["checks"] == [
                    {
                        "name": "version",
                        "command": "agent-browser --version",
                        "ok": True,
                        "exit_code": 0,
                        "stdout": "agent-browser 1.2.3",
                        "stderr": "",
                    },
                    {
                        "name": "doctor",
                        "command": "agent-browser doctor",
                        "ok": True,
                        "exit_code": 0,
                        "stdout": "Browser install looks good",
                        "stderr": "",
                    },
                ]
        assert doctor_calls == [
            {"args": ["--version"], "launch_args": ""},
            {"args": ["doctor"], "launch_args": "--no-sandbox"},
        ]
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_browser_settings_doctor(tmp_path, monkeypatch):
    asyncio.run(_run_web_browser_settings_doctor(tmp_path, monkeypatch))


async def _run_web_browser_settings_install(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    doctor_calls = []

    async def fake_doctor_command(args, *, timeout=20, launch_args=""):
        doctor_calls.append({"args": list(args), "launch_args": launch_args})
        if len(doctor_calls) == 1:
            return WebAdapter._with_browser_diagnostic(
                {"ok": False, "exit_code": 1, "stdout": "", "stderr": "Executable doesn't exist at /tmp/chromium"}
            )
        return WebAdapter._with_browser_diagnostic(
            {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "No usable sandbox! Hint: try --args \"--no-sandbox\"",
            }
        )

    async def fake_install_command(*, timeout=300):
        return WebAdapter._with_browser_diagnostic(
            {"ok": True, "exit_code": 0, "stdout": "Installed Chromium", "stderr": ""}
        )

    monkeypatch.setattr(WebAdapter, "_browser_command_prefix", staticmethod(lambda: ["agent-browser"]))
    monkeypatch.setattr(WebAdapter, "_run_browser_doctor_command", staticmethod(fake_doctor_command))
    monkeypatch.setattr(WebAdapter, "_run_browser_install_command", staticmethod(fake_install_command))

    agent = EchoAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(f"http://127.0.0.1:{port}/api/settings/browser/install") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["ok"] is True
                assert payload["installed"] is True
                assert payload["doctor_warning"] is True
                assert payload["already_installed"] is False
                assert payload["before"]["diagnostic_code"] == "browser_missing"
                assert payload["install"]["diagnostic_code"] == "ok"
                assert payload["after"]["diagnostic_code"] == "sandbox_unavailable"
        assert doctor_calls == [
            {"args": ["doctor"], "launch_args": "--no-sandbox"},
            {"args": ["doctor"], "launch_args": "--no-sandbox"},
        ]
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_browser_settings_install(tmp_path, monkeypatch):
    asyncio.run(_run_web_browser_settings_install(tmp_path, monkeypatch))


async def _run_web_search_settings_roundtrip(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    class SearchReloadAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.reloads = []

        def reload_web_search_from_config(self, config):
            search = config.tools.web_search
            self.reloads.append(search)
            return {
                "provider": search.provider,
                "freshness": search.freshness,
                "max_results": search.max_results,
                "searxng_max_pages": search.searxng_max_pages,
                "searxng_engines": list(search.searxng_engines),
                "searxng_categories": list(search.searxng_categories),
                "tool_updated": True,
                "research_tool_updated": True,
            }

    agent = SearchReloadAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/search") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["search"]["provider"] == "duckduckgo"
                assert payload["search"]["freshness"] == "auto"
                assert payload["search"]["providers"] == ["duckduckgo", "searxng", "jina"]
                assert payload["search"]["searxng_max_pages"] == 5
                assert payload["search"]["searxng_engines"] == []
                assert payload["search"]["searxng_categories"] == []

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/search",
                json={
                    "provider": "searxng",
                    "freshness": "week",
                    "max_results": 12,
                    "duckduckgo_max_pages": 3,
                    "searxng_max_pages": 4,
                    "searxng_url": "https://search.example.test",
                    "searxng_engines": ["google", "bing", "google"],
                    "searxng_categories": "general, news",
                    "proxy": "http://proxy.local:8080",
                },
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["restart_required"] is False
                assert payload["runtime_reloaded"] is True
                assert payload["runtime"] == {
                    "provider": "searxng",
                    "freshness": "week",
                    "max_results": 12,
                    "searxng_max_pages": 4,
                    "searxng_engines": ["google", "bing"],
                    "searxng_categories": ["general", "news"],
                    "tool_updated": True,
                    "research_tool_updated": True,
                }
                assert payload["search"]["provider"] == "searxng"
                assert payload["search"]["freshness"] == "week"
                assert payload["search"]["max_results"] == 12
                assert payload["search"]["duckduckgo_max_pages"] == 3
                assert payload["search"]["searxng_max_pages"] == 4
                assert payload["search"]["searxng_engines"] == ["google", "bing"]
                assert payload["search"]["searxng_categories"] == ["general", "news"]

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/search",
                json={"provider": "unknown"},
            ) as resp:
                assert resp.status == 400

        loaded = Config.from_json(config_path)
        assert loaded.tools.web_search.provider == "searxng"
        assert loaded.tools.web_search.freshness == "week"
        assert loaded.tools.web_search.max_results == 12
        assert loaded.tools.web_search.duckduckgo_max_pages == 3
        assert loaded.tools.web_search.searxng_max_pages == 4
        assert loaded.tools.web_search.searxng_engines == ["google", "bing"]
        assert loaded.tools.web_search.searxng_categories == ["general", "news"]
        assert loaded.tools.web_search.searxng_url == "https://search.example.test"
        assert loaded.tools.web_search.proxy == "http://proxy.local:8080"
        assert agent.reloads[-1].provider == "searxng"
        assert agent.reloads[-1].freshness == "week"
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_search_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    asyncio.run(_run_web_search_settings_roundtrip(tmp_path))


async def _run_web_search_searxng_options(tmp_path: Path, fake_client: _FakeSearxngConfigClient):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    agent = EchoAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/search/searxng-options?url=https://searx.test/search") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["searxng"]["url"] == "https://searx.test/search"
                assert payload["searxng"]["categories"] == [
                    {"id": "general", "label": "general"},
                    {"id": "news", "label": "news"},
                ]
                assert payload["searxng"]["fallback"] is False
                assert payload["searxng"]["warning"] == ""
                assert payload["searxng"]["engines"][0] == {
                    "id": "google",
                    "label": "google",
                    "shortcut": "go",
                    "categories": ["general"],
                    "enabled": True,
                }
        assert fake_client.requests[0][0] == "https://searx.test/config"
        assert fake_client.requests[0][1]["User-Agent"]
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_search_searxng_options(tmp_path, monkeypatch):
    fake_client = _FakeSearxngConfigClient()
    monkeypatch.setattr("opensprite.channels.web.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    asyncio.run(_run_web_search_searxng_options(tmp_path, fake_client))


async def _run_web_search_searxng_options_fallback(tmp_path: Path, fake_client: _FakeSearxngConfigClient):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    agent = EchoAgent()
    agent.config_path = config_path
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )

    processor = asyncio.create_task(queue.process_queue())
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/search/searxng-options?url=https://searx.be") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["searxng"]["fallback"] is True
                assert "Unable to load" in payload["searxng"]["warning"]
                assert {entry["id"] for entry in payload["searxng"]["engines"]} >= {"duckduckgo", "google", "bing"}
                assert {entry["id"] for entry in payload["searxng"]["categories"]} >= {"general", "news"}
        assert fake_client.requests[0][0] == "https://searx.be/config"
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await queue.stop()
        await asyncio.wait_for(processor, timeout=2)


def test_web_adapter_search_searxng_options_fallback(tmp_path, monkeypatch):
    fake_client = _FakeSearxngConfigClient(error=RuntimeError("403 Forbidden"))
    monkeypatch.setattr("opensprite.channels.web.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    asyncio.run(_run_web_search_searxng_options_fallback(tmp_path, fake_client))


async def _run_web_run_events_api():
    storage = MemoryStorage()
    await storage.add_message(
        "web:browser-1",
        StoredMessage(role="user", content="inspect run timeline", timestamp=99.0, metadata={"sender_name": "Tester"}),
    )
    await storage.add_message(
        "web:browser-1",
        StoredMessage(role="assistant", content="timeline inspected", timestamp=105.0),
    )
    await storage.create_run(
        "web:browser-1",
        "run-1",
        status="completed",
        metadata={"objective": Path("notes.txt")},
        created_at=100.0,
    )
    await storage.add_run_event(
        "web:browser-1",
        "run-1",
        TASK_INTENT_DETECTED_EVENT,
        payload={"objective": "inspect run timeline", "path": Path("notes.txt")},
        created_at=101.0,
    )
    await storage.add_run_event(
        "web:browser-1",
        "run-1",
        COMPLETION_GATE_EVALUATED_EVENT,
        payload={"status": "complete"},
        created_at=102.0,
    )
    await storage.add_run_event(
        "web:browser-1",
        "run-1",
        TOOL_STARTED_EVENT,
        payload={"tool_name": "apply_patch", "tool_call_id": "call-1", "args_preview": "apply patch"},
        created_at=102.5,
    )
    await storage.add_run_event(
        "web:browser-1",
        "run-1",
        TOOL_RESULT_EVENT,
        payload={"tool_name": "apply_patch", "tool_call_id": "call-1", "ok": True, "result_preview": "done"},
        created_at=102.75,
    )
    await storage.add_run_part(
        "web:browser-1",
        "run-1",
        "tool_call",
        content="apply patch",
        tool_name="apply_patch",
        metadata={"path": Path("notes.txt"), "tool_call_id": "call-1"},
        created_at=102.25,
    )
    await storage.add_run_file_change(
        "web:browser-1",
        "run-1",
        "apply_patch",
        "notes.txt",
        "modify",
        before_sha256="before",
        after_sha256="after",
        before_content="old\n",
        after_content="new\n",
        diff="--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n",
        metadata={"verified": True},
        created_at=104.0,
    )
    await storage.create_run("web:browser-1", "run-2", created_at=200.0)
    await storage.upsert_background_process(
        StoredBackgroundProcess(
            process_session_id="proc-running",
            owner_session_id="web:browser-1",
            owner_run_id="run-1",
            owner_channel="web",
            owner_external_chat_id="browser-1",
            pid=1234,
            command="npm run dev",
            cwd="C:/repo",
            state="running",
            notify_mode="agent_summary",
            output_tail="server ready",
            metadata={"source": "test"},
            started_at=150.0,
            updated_at=151.0,
        )
    )
    await storage.upsert_background_process(
        StoredBackgroundProcess(
            process_session_id="proc-lost",
            owner_session_id="web:browser-1",
            owner_run_id="run-1",
            command="python worker.py",
            state="lost",
            termination_reason="runtime_restart",
            metadata={"recovery_reason": "runtime_restart", "reattach_supported": False},
            started_at=140.0,
            updated_at=160.0,
            finished_at=160.0,
        )
    )
    await storage.upsert_background_process(
        StoredBackgroundProcess(
            process_session_id="proc-completed",
            owner_session_id="web:browser-1",
            owner_run_id="run-1",
            command="npm run build",
            state="completed",
            exit_code=0,
            started_at=130.0,
            updated_at=155.0,
            finished_at=155.0,
        )
    )
    await storage.upsert_background_process(
        StoredBackgroundProcess(
            process_session_id="proc-other",
            owner_session_id="web:browser-2",
            command="python other.py",
            state="running",
            started_at=170.0,
            updated_at=170.0,
        )
    )

    agent = EchoAgent()
    agent.storage = storage
    agent.background_process_manager = BackgroundProcessManager(storage=storage)
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/run-1/events",
                params={"session_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            assert payload["run_id"] == "run-1"
            assert payload["session_id"] == "web:browser-1"
            assert payload["event_counts"] == {
                "total": 4,
                "returned": 4,
                "compacted": 0,
                "text_total": 0,
                "text_returned": 0,
                "max_events": 80,
                "max_text_events": 24,
            }
            assert [event["event_type"] for event in payload["events"]] == [
                TASK_INTENT_DETECTED_EVENT,
                COMPLETION_GATE_EVALUATED_EVENT,
                TOOL_STARTED_EVENT,
                TOOL_RESULT_EVENT,
            ]
            assert payload["events"][0]["event_id"] == 1
            assert payload["events"][0]["payload"] == {
                "objective": "inspect run timeline",
                "path": "notes.txt",
            }
            assert payload["events"][1]["created_at"] == 102.0
            assert payload["events"][2]["artifact"]["artifact_id"] == "tool:call-1"
            assert payload["events"][2]["artifact"]["status"] == "running"
            assert payload["events"][3]["artifact"]["artifact_id"] == "tool:call-1"
            assert payload["events"][3]["artifact"]["status"] == "completed"

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs",
                params={"session_id": "web:browser-1", "limit": "1"},
            ) as resp:
                assert resp.status == 200
                runs_payload = await resp.json()

            assert runs_payload["session_id"] == "web:browser-1"
            assert [run["run_id"] for run in runs_payload["runs"]] == ["run-2"]

            async with session.get(
                f"http://127.0.0.1:{port}/api/background-processes",
                params={"session_id": "web:browser-1", "states": "running,lost", "limit": "10"},
            ) as resp:
                assert resp.status == 200
                processes_payload = await resp.json()

            assert processes_payload["session_id"] == "web:browser-1"
            assert processes_payload["states"] == ["running", "lost"]
            assert processes_payload["counts"] == {"lost": 1, "running": 1}
            assert [process["process_session_id"] for process in processes_payload["processes"]] == [
                "proc-lost",
                "proc-running",
            ]
            assert processes_payload["processes"][0]["termination_reason"] == "runtime_restart"
            assert processes_payload["processes"][0]["metadata"]["reattach_supported"] is False
            assert processes_payload["processes"][1]["command"] == "npm run dev"
            assert processes_payload["processes"][1]["metadata"] == {"source": "test"}

            async with session.get(f"http://127.0.0.1:{port}/api/evals/long-task") as resp:
                assert resp.status == 200
                eval_status_payload = await resp.json()

            assert eval_status_payload["ready"] is True
            assert eval_status_payload["background_process_counts"] == {"completed": 1, "lost": 1, "running": 2}
            assert "completion_rate" in {metric["id"] for metric in eval_status_payload["recommended_metrics"]}
            assert "restart_recovery" in {scenario["id"] for scenario in eval_status_payload["recommended_scenarios"]}

            async with session.post(f"http://127.0.0.1:{port}/api/evals/long-task/smoke") as resp:
                assert resp.status == 200
                eval_smoke_payload = await resp.json()

            assert eval_smoke_payload["ok"] is True
            assert [check["id"] for check in eval_smoke_payload["checks"]] == [
                "storage_available",
                "background_process_api",
                "run_event_schema",
            ]
            assert eval_smoke_payload["background_process_counts"] == {"completed": 1, "lost": 1, "running": 2}

            async with session.post(f"http://127.0.0.1:{port}/api/evals/long-task/controlled") as resp:
                assert resp.status == 200
                controlled_payload = await resp.json()

            assert controlled_payload["ok"] is True, controlled_payload["checks"]
            assert [check["id"] for check in controlled_payload["checks"]] == [
                "process_record_created",
                "process_completed",
                "started_event_recorded",
                "completed_event_recorded",
                "output_tail_captured",
            ]
            assert controlled_payload["process"]["state"] == "exited"
            assert controlled_payload["process"]["exit_code"] == 0
            assert "background_process.started" in controlled_payload["event_types"]
            assert "background_process.completed" in controlled_payload["event_types"]

            async with session.post(f"http://127.0.0.1:{port}/api/evals/harness/controlled") as resp:
                assert resp.status == 200
                harness_payload = await resp.json()

            assert harness_payload["ok"] is True
            assert harness_payload["kind"] == "controlled_harness_scenarios"
            assert harness_payload["summary"]["passed_cases"] == 5
            assert harness_payload["trace"]["session_id"] == "web:evaluations"
            assert harness_payload["trace"]["part_type"] == "harness_eval_result"
            harness_parts = await storage.get_run_parts("web:evaluations", harness_payload["trace"]["run_id"])
            assert [part.part_type for part in harness_parts] == ["harness_eval_result"]
            assert harness_parts[0].metadata["kind"] == "controlled_harness_scenarios"
            harness_events = await storage.get_run_events("web:evaluations", harness_payload["trace"]["run_id"])
            assert [event.event_type for event in harness_events] == ["harness_eval.completed"]

            async with session.post(f"http://127.0.0.1:{port}/api/evals/task-completion/smoke") as resp:
                assert resp.status == 200
                task_completion_payload = await resp.json()

            assert task_completion_payload["ok"] is True
            assert task_completion_payload["summary"] == {
                "passed_cases": 2,
                "total_cases": 2,
                "passed_checks": 15,
                "total_checks": 15,
            }
            assert {case["id"] for case in task_completion_payload["cases"]} == {
                "web_smoke_question",
                "task_completion_question",
            }

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/run-1",
                params={"session_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 200
                trace_payload = await resp.json()

            assert trace_payload["run"]["run_id"] == "run-1"
            assert trace_payload["run"]["metadata"] == {"objective": "notes.txt"}
            assert trace_payload["event_counts"]["total"] == 4
            assert trace_payload["event_counts"]["returned"] == 4
            assert trace_payload["event_counts"]["compacted"] == 0
            assert [event["event_type"] for event in trace_payload["events"]] == [
                TASK_INTENT_DETECTED_EVENT,
                COMPLETION_GATE_EVALUATED_EVENT,
                TOOL_STARTED_EVENT,
                TOOL_RESULT_EVENT,
            ]
            assert trace_payload["parts"] == [
                {
                    "schema_version": 1,
                    "part_id": 1,
                    "run_id": "run-1",
                    "session_id": "web:browser-1",
                    "part_type": "tool_call",
                    "kind": "tool",
                    "state": "running",
                    "content": "apply patch",
                    "tool_name": "apply_patch",
                    "metadata": {"path": "notes.txt", "tool_call_id": "call-1"},
                    "artifact": {
                        "schema_version": 1,
                        "artifact_id": "tool:call-1",
                        "artifact_type": "tool",
                        "kind": "tool",
                        "status": "running",
                        "phase": "tool_call",
                        "tool_name": "apply_patch",
                        "tool_call_id": "call-1",
                        "iteration": None,
                        "title": "apply_patch",
                        "detail": "",
                        "metadata": {"path": "notes.txt", "tool_call_id": "call-1"},
                    },
                    "created_at": 102.25,
                }
            ]
            assert trace_payload["file_changes"][0]["change_id"] == 1
            assert trace_payload["file_changes"][0]["path"] == "notes.txt"
            assert trace_payload["file_changes"][0]["before_content"] == "old\n"
            assert trace_payload["file_changes"][0]["after_content"] == "new\n"
            assert trace_payload["diff_summary"] == {
                "schema_version": 1,
                "changed_files": 1,
                "change_count": 1,
                "additions": 1,
                "deletions": 1,
                "paths": ["notes.txt"],
                "actions": {"modify": 1},
            }
            assert [artifact["kind"] for artifact in trace_payload["artifacts"]] == ["tool", "file"]
            assert trace_payload["artifacts"][0]["artifact_id"] == "tool:call-1"
            assert trace_payload["artifacts"][0]["status"] == "completed"
            assert trace_payload["artifacts"][0]["sources"] == ["part", "event"]
            assert trace_payload["artifacts"][1]["path"] == "notes.txt"
            assert trace_payload["entries"] == [
                {
                    "schema_version": 1,
                    "entry_id": "run:run-1",
                    "entry_type": "assistant",
                    "role": "assistant",
                    "run_id": "run-1",
                    "session_id": "web:browser-1",
                    "status": "completed",
                    "created_at": 100.0,
                    "updated_at": 100.0,
                    "content": [
                        {
                            "type": "tool",
                            "status": "completed",
                            "title": "apply_patch",
                            "detail": "done",
                            "artifact": trace_payload["artifacts"][0],
                            "created_at": 102.75,
                        },
                        {
                            "type": "file",
                            "status": "completed",
                            "title": "notes.txt",
                            "detail": "notes.txt",
                            "artifact": trace_payload["artifacts"][1],
                            "created_at": 104.0,
                        },
                    ],
                    "metadata": {"objective": "notes.txt"},
                }
            ]

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions/timeline",
                params={"session_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 200
                timeline_payload = await resp.json()

            assert timeline_payload["session_id"] == "web:browser-1"
            assert [message["role"] for message in timeline_payload["messages"]] == ["user", "assistant"]
            assert [run["run_id"] for run in timeline_payload["runs"]] == ["run-2", "run-1"]
            assert [entry["entry_id"] for entry in timeline_payload["entries"]] == [
                "message:1",
                "run:run-1",
                "message:2",
                "run:run-2",
            ]
            assert timeline_payload["entries"][0]["text"] == "inspect run timeline"
            assert timeline_payload["entries"][1]["content"][0]["type"] == "tool"
            assert timeline_payload["entries"][1]["content"][1]["type"] == "file"
            assert timeline_payload["entries"][2]["content"][0]["text"] == "timeline inspected"

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/run-1/summary",
                params={"session_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 200
                summary_payload = await resp.json()

            assert summary_payload == {
                "schema_version": 1,
                "run_id": "run-1",
                "session_id": "web:browser-1",
                "status": "completed",
                "objective": "inspect run timeline",
                "created_at": 100.0,
                "updated_at": 100.0,
                "finished_at": None,
                "duration_seconds": None,
                "tools": [{"name": "apply_patch", "count": 1}],
                "file_changes": [
                    {
                        "change_id": 1,
                        "path": "notes.txt",
                        "action": "modify",
                        "tool_name": "apply_patch",
                        "diff_len": 54,
                        "diff": "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n",
                        "snapshots_available": {"before": True, "after": True},
                    }
                ],
                "diff_summary": {
                    "schema_version": 1,
                    "changed_files": 1,
                    "change_count": 1,
                    "additions": 1,
                    "deletions": 1,
                    "paths": ["notes.txt"],
                    "actions": {"modify": 1},
                },
                "verification": {
                    "attempted": False,
                    "passed": False,
                    "status": "not_attempted",
                    "name": None,
                    "summary": "",
                },
                "review": {
                    "required": False,
                    "attempted": False,
                    "passed": False,
                    "status": "not_required",
                    "summary": "",
                    "prompt_types": [],
                    "finding_count": 0,
                },
                "structured_subagents": {
                    "total": 0,
                    "by_prompt_type": {},
                    "by_status": {},
                    "total_sections": 0,
                    "total_items": 0,
                    "total_findings": 0,
                    "total_questions": 0,
                    "total_residual_risks": 0,
                    "results": [],
                },
                "workflows": {
                    "total": 0,
                    "by_workflow": {},
                    "by_status": {},
                    "results": [],
                },
                "parallel_delegation": {
                    "group_count": 0,
                    "task_count": 0,
                    "groups": [],
                },
                "structured_subagents": {
                    "total": 0,
                    "by_prompt_type": {},
                    "by_status": {},
                    "total_sections": 0,
                    "total_items": 0,
                    "total_findings": 0,
                    "total_questions": 0,
                    "total_residual_risks": 0,
                    "results": [],
                },
                "artifact_counts": {
                    "total": 2,
                    "tool": 1,
                    "file": 1,
                    "verification": 0,
                },
                "harness_scorecard": {
                    "present": False,
                    "status": "missing",
                    "profile": "",
                    "task_type": "",
                    "sensor_counts": {"pass": 0, "warn": 0, "fail": 0, "not_applicable": 0},
                    "failing_sensors": [],
                    "warning_sensors": [],
                },
                "completion": {"status": "complete"},
                "next_action": None,
                "warnings": [],
                "counts": {
                    "events": 4,
                    "parts": 1,
                    "tool_calls": 1,
                    "file_changes": 1,
                },
            }

            async with session.get(f"http://127.0.0.1:{port}/api/runs/run-1/events") as resp:
                assert resp.status == 400

            async with session.get(f"http://127.0.0.1:{port}/api/runs") as resp:
                assert resp.status == 400

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/timeline") as resp:
                assert resp.status == 400
                assert await resp.text() == "session_id is required"

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs",
                params={"session_id": "web:browser-1", "limit": "not-a-number"},
            ) as resp:
                assert resp.status == 400

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/missing-run/events",
                params={"session_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 404

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/missing-run",
                params={"session_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 404

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/missing-run/summary",
                params={"session_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 404
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_run_events_api():
    asyncio.run(_run_web_run_events_api())


async def _run_web_task_completion_live_eval_api():
    storage = MemoryStorage()

    class LiveEvalAgent:
        def __init__(self):
            self.storage = storage
            self.seen_messages = []
            self.eval_model_info = {
                "provider_id": "test-provider",
                "provider": "test",
                "model": "test-model",
                "configured": True,
            }

        async def process(self, user_message):
            self.seen_messages.append(user_message)
            case_id = user_message.metadata["eval_case_id"]
            run_id = f"run-{case_id}"
            response_text = {
                "literal_instruction": "alpha beta gamma",
                "multi_step_completion": (
                    "1. 問題：這是格式遵循測試。\n"
                    "2. 可能原因：模型可能漏掉步驟；輸出格式可能不穩定。\n"
                    "3. 結論：已完成三步驟回答"
                ),
                "exact_two_line_output": "狀態：完成\n代碼：A7-42",
                "exact_json_output": '{"status":"complete","items":["alpha","beta"]}',
            }[case_id]
            await storage.create_run(user_message.session_id, run_id, status="running", created_at=1.0)
            await storage.add_run_event(
                user_message.session_id,
                run_id,
                COMPLETION_GATE_EVALUATED_EVENT,
                payload={"status": "complete", "reason": "test"},
                created_at=2.0,
            )
            await storage.add_run_event(
                user_message.session_id,
                run_id,
                RUN_FINISHED_EVENT,
                payload={"status": "completed", "had_tool_error": False},
                created_at=3.0,
            )
            await storage.update_run_status(
                user_message.session_id,
                run_id,
                "completed",
                metadata={"had_tool_error": False},
                finished_at=3.0,
            )
            return AssistantMessage(
                text=response_text,
                channel="web",
                external_chat_id=user_message.external_chat_id,
                session_id=user_message.session_id,
                metadata={"source": "live-eval-test"},
            )

    agent = LiveEvalAgent()
    adapter = WebAdapter(
        mq=MessageQueue(agent),
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(f"http://127.0.0.1:{port}/api/evals/task-completion/run") as resp:
                assert resp.status == 200
                payload = await resp.json()

            async with session.get(f"http://127.0.0.1:{port}/api/evals/task-completion/history") as resp:
                assert resp.status == 200
                history_payload = await resp.json()

        assert payload["ok"] is True
        assert payload["live"] is True
        assert payload["model"] == agent.eval_model_info
        assert payload["batch_id"].startswith("eval_batch_")
        assert payload["summary"]["passed_cases"] == 4
        cases_by_id = {case["id"]: case for case in payload["cases"]}
        assert set(cases_by_id) == {
            "literal_instruction",
            "multi_step_completion",
            "exact_two_line_output",
            "exact_json_output",
        }
        assert cases_by_id["literal_instruction"]["run_id"] == "run-literal_instruction"
        assert cases_by_id["literal_instruction"]["eval_id"].startswith("eval_")
        assert {case["batch_id"] for case in cases_by_id.values()} == {payload["batch_id"]}
        assert cases_by_id["literal_instruction"]["completion_status"] == "complete"
        assert cases_by_id["literal_instruction"]["model"] == agent.eval_model_info
        assert cases_by_id["multi_step_completion"]["run_id"] == "run-multi_step_completion"
        assert cases_by_id["exact_two_line_output"]["run_id"] == "run-exact_two_line_output"
        assert cases_by_id["exact_json_output"]["run_id"] == "run-exact_json_output"
        history_by_case = {item["case_id"]: item for item in history_payload["history"]}
        assert history_by_case["literal_instruction"]["eval_id"] == cases_by_id["literal_instruction"]["eval_id"]
        assert history_by_case["literal_instruction"]["case_label"] == "Literal instruction answer"
        assert history_by_case["literal_instruction"]["expected_summary"] == "alpha beta gamma"
        assert history_by_case["literal_instruction"]["actual_response"] == "alpha beta gamma"
        assert history_by_case["literal_instruction"]["response_preview"] == "alpha beta gamma"
        assert history_by_case["literal_instruction"]["model"] == agent.eval_model_info
        assert {item["batch_id"] for item in history_payload["history"]} == {payload["batch_id"]}
        assert history_by_case["multi_step_completion"]["eval_id"] == cases_by_id["multi_step_completion"]["eval_id"]

        async with ClientSession() as session:
            literal_eval_id = cases_by_id["literal_instruction"]["eval_id"]
            async with session.delete(
                f"http://127.0.0.1:{port}/api/evals/task-completion/history/{literal_eval_id}"
            ) as resp:
                assert resp.status == 200
                delete_payload = await resp.json()

            async with session.get(f"http://127.0.0.1:{port}/api/evals/task-completion/history") as resp:
                assert resp.status == 200
                history_after_delete = await resp.json()

            async with session.delete(f"http://127.0.0.1:{port}/api/evals/task-completion/history") as resp:
                assert resp.status == 200
                clear_payload = await resp.json()

            async with session.get(f"http://127.0.0.1:{port}/api/evals/task-completion/history") as resp:
                assert resp.status == 200
                history_after_clear = await resp.json()

        assert delete_payload == {"ok": True, "eval_id": literal_eval_id, "deleted": 1}
        assert {item["case_id"] for item in history_after_delete["history"]} == {
            "multi_step_completion",
            "exact_two_line_output",
            "exact_json_output",
        }
        assert clear_payload == {"ok": True, "deleted": 3}
        assert history_after_clear["history"] == []
        assert agent.seen_messages[0].channel == "web"
        assert {message.metadata["eval_batch_id"] for message in agent.seen_messages} == {payload["batch_id"]}
        assert agent.seen_messages[0].external_chat_id.startswith("eval-task-completion-literal_instruction-")
        assert agent.seen_messages[1].external_chat_id.startswith("eval-task-completion-multi_step_completion-")
        assert agent.seen_messages[2].external_chat_id.startswith("eval-task-completion-exact_two_line_output-")
        assert agent.seen_messages[3].external_chat_id.startswith("eval-task-completion-exact_json_output-")
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_task_completion_live_eval_api():
    asyncio.run(_run_web_task_completion_live_eval_api())


async def _run_web_sessions_api():
    storage = MemoryStorage()
    await storage.add_message(
        "web:browser-old",
        StoredMessage(role="user", content="old hello", timestamp=100.0, metadata={"sender_name": "Tester"}),
    )
    await storage.add_message(
        "web:browser-old",
        StoredMessage(role="assistant", content="old reply", timestamp=101.0),
    )
    await storage.add_message(
        "web:browser-new",
        StoredMessage(role="user", content="new hello", timestamp=200.0, metadata={"sender_name": "Tester"}),
    )
    await storage.upsert_work_state(
        StoredWorkState(
            session_id="web:browser-new",
            objective="ship session work card",
            kind="implementation",
            status="active",
            steps=("inspect", "build", "verify"),
            current_step="build",
            next_step="verify",
            pending_steps=("build", "verify"),
            file_change_count=2,
            touched_paths=("apps/web/src/App.vue",),
            verification_attempted=True,
            verification_passed=False,
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_abc12345",
                    prompt_type="implementer",
                    status="completed",
                    selected=True,
                    summary="Delegated the UI update.",
                    child_session_id="web:browser-new:subagent:task_abc12345",
                    last_child_run_id="run_child_1",
                    created_at=195.0,
                    updated_at=200.0,
                ),
            ),
            resume_hint="Continue with frontend validation.",
            metadata={
                "follow_up_workflow": "implement_then_review",
                "follow_up_step_id": "review",
                "follow_up_step_label": "Code review",
                "follow_up_prompt_type": "code-reviewer",
                "verification_action": "pytest",
                "verification_path": ".",
                "verification_pytest_args": ["tests/test_ui.py::test_card"],
                "active_task_detail": "Resume with the Code review step in implement_then_review.",
            },
            created_at=190.0,
            updated_at=201.0,
        )
    )
    await storage.create_run(
        "web:browser-new",
        "run-new-latest",
        status="completed",
        metadata={"objective": "ship session work card"},
        created_at=202.0,
    )
    await storage.add_run_file_change(
        "web:browser-new",
        "run-new-latest",
        "apply_patch",
        "apps/web/src/App.vue",
        "modify",
        diff="@@ -1 +1 @@\n-old\n+new\n",
        created_at=203.0,
    )
    await storage.add_message(
        "telegram:123",
        StoredMessage(role="user", content="telegram should appear for all", timestamp=300.0),
    )
    await storage.create_run(
        "telegram:123",
        "run-telegram-latest",
        status="completed",
        metadata={"objective": "inspect telegram"},
        created_at=301.0,
    )
    await storage.add_message(
        "web:browser-new:subagent:task_abc12345",
        StoredMessage(role="user", content="hidden delegated task", timestamp=400.0),
    )
    await storage.create_run(
        "web:browser-new:subagent:task_abc12345",
        "run-subagent-latest",
        status="completed",
        metadata={"kind": "subagent", "objective": "hidden delegated task"},
        created_at=401.0,
    )

    agent = EchoAgent()
    agent.storage = storage
    queue = MessageQueue(agent)
    queue.session_status.set("web:browser-new", "thinking", {"channel": "web", "external_chat_id": "browser-new"})
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"limit": "1", "messages": "1"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"channel": "all", "limit": "3", "messages": "1"},
            ) as resp:
                assert resp.status == 200
                all_payload = await resp.json()

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"channel": "telegram", "limit": "3", "messages": "1"},
            ) as resp:
                assert resp.status == 200
                telegram_payload = await resp.json()

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/status") as resp:
                assert resp.status == 200
                status_payload = await resp.json()

            async with session.get(f"http://127.0.0.1:{port}/api/storage/status") as resp:
                assert resp.status == 200
                storage_status_payload = await resp.json()

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions/status",
                params={"session_id": "web:missing"},
            ) as resp:
                assert resp.status == 200
                idle_status_payload = await resp.json()

        assert [item["session_id"] for item in payload["sessions"]] == ["web:browser-new"]
        assert payload["channel"] == "web"
        assert payload["sessions"][0]["external_chat_id"] == "browser-new"
        assert payload["sessions"][0]["channel"] == "web"
        assert payload["sessions"][0]["title"] == "new hello"
        assert [item["session_id"] for item in all_payload["sessions"]] == [
            "telegram:123",
            "web:browser-new",
            "web:browser-old",
        ]
        assert all(":subagent:" not in item["session_id"] for item in all_payload["sessions"])
        assert all_payload["channel"] == "all"
        assert all_payload["sessions"][0]["channel"] == "telegram"
        assert all_payload["sessions"][0]["external_chat_id"] == "123"
        assert all_payload["sessions"][0]["runs"][0]["run_id"] == "run-telegram-latest"
        assert [item["session_id"] for item in telegram_payload["sessions"]] == ["telegram:123"]
        assert telegram_payload["channel"] == "telegram"
        assert payload["sessions"][0]["status"] == {
            "session_id": "web:browser-new",
            "status": "thinking",
            "updated_at": payload["sessions"][0]["status"]["updated_at"],
            "metadata": {"channel": "web", "external_chat_id": "browser-new"},
        }
        assert status_payload["statuses"] == [payload["sessions"][0]["status"]]
        assert idle_status_payload["status"]["session_id"] == "web:missing"
        assert idle_status_payload["status"]["status"] == "idle"
        assert idle_status_payload["status"]["metadata"] == {}
        assert storage_status_payload["storage"]["type"] in {"memory", "sqlite"}
        assert storage_status_payload["storage"]["provider"] == "MemoryStorage"
        assert storage_status_payload["counts"] == {
            "sessions": 3,
            "raw_sessions": 4,
            "messages": 5,
            "runs": 3,
        }
        assert payload["sessions"][0]["message_count"] == 1
        assert payload["sessions"][0]["runs"] == [
            {
                "run_id": "run-new-latest",
                "session_id": "web:browser-new",
                "status": "completed",
                "created_at": 202.0,
                "updated_at": 202.0,
                "finished_at": None,
                "metadata": {"objective": "ship session work card"},
            }
        ]
        assert payload["sessions"][0]["work_state"] == {
            "session_id": "web:browser-new",
            "objective": "ship session work card",
            "kind": "implementation",
            "status": "active",
            "steps": ["inspect", "build", "verify"],
            "constraints": [],
            "done_criteria": [],
            "long_running": False,
            "coding_task": False,
            "expects_code_change": False,
            "expects_verification": False,
            "current_step": "build",
            "next_step": "verify",
            "completed_steps": [],
            "pending_steps": ["build", "verify"],
            "blockers": [],
            "verification_targets": [],
            "resume_hint": "Continue with frontend validation.",
            "last_progress_signals": [],
            "file_change_count": 2,
            "touched_paths": ["apps/web/src/App.vue"],
            "verification_attempted": True,
            "verification_passed": False,
            "follow_up_workflow": "implement_then_review",
            "follow_up_step_id": "review",
            "follow_up_step_label": "Code review",
            "follow_up_prompt_type": "code-reviewer",
            "verification_action": "pytest",
            "verification_path": ".",
            "verification_pytest_args": ["tests/test_ui.py::test_card"],
            "active_task_detail": "Resume with the Code review step in implement_then_review.",
            "last_next_action": "",
            "delegated_tasks": [
                {
                    "task_id": "task_abc12345",
                    "prompt_type": "implementer",
                    "status": "completed",
                    "selected": True,
                    "summary": "Delegated the UI update.",
                    "error": "",
                    "child_session_id": "web:browser-new:subagent:task_abc12345",
                    "last_child_run_id": "run_child_1",
                    "metadata": {},
                    "created_at": 195.0,
                    "updated_at": 200.0,
                }
            ],
            "active_delegate_task_id": "task_abc12345",
            "active_delegate_prompt_type": "implementer",
            "metadata": {
                "follow_up_workflow": "implement_then_review",
                "follow_up_step_id": "review",
                "follow_up_step_label": "Code review",
                "follow_up_prompt_type": "code-reviewer",
                "verification_action": "pytest",
                "verification_path": ".",
                "verification_pytest_args": ["tests/test_ui.py::test_card"],
                "active_task_detail": "Resume with the Code review step in implement_then_review.",
            },
            "todos": [
                {
                    "id": "task:1",
                    "content": "build",
                    "status": "in_progress",
                    "priority": "high",
                    "updated_at": 201.0,
                },
                {
                    "id": "task:2",
                    "content": "verify",
                    "status": "pending",
                    "priority": "medium",
                    "updated_at": 201.0,
                },
                {
                    "id": "task:3",
                    "content": "inspect",
                    "status": "pending",
                    "priority": "medium",
                    "updated_at": 201.0,
                },
            ],
            "created_at": 190.0,
            "updated_at": 201.0,
        }
        assert payload["sessions"][0]["messages"] == [
            {
                "role": "user",
                "content": "new hello",
                "tool_name": None,
                "metadata": {"sender_name": "Tester"},
                "created_at": 200.0,
            }
        ]
        assert [entry["entry_id"] for entry in payload["sessions"][0]["entries"]] == [
            "message:1",
            "run:run-new-latest",
        ]
        assert payload["sessions"][0]["entries"][0]["entry_type"] == "user"
        assert payload["sessions"][0]["entries"][0]["text"] == "new hello"
        assert payload["sessions"][0]["entries"][1]["entry_type"] == "assistant"
        assert payload["sessions"][0]["entries"][1]["run_id"] == "run-new-latest"
        assert payload["sessions"][0]["diff_summary"] == {
            "schema_version": 1,
            "changed_files": 1,
            "change_count": 1,
            "additions": 1,
            "deletions": 1,
            "paths": ["apps/web/src/App.vue"],
            "actions": {"modify": 1},
        }

        async with ClientSession() as session:
            async with session.delete(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"session_id": "web:browser-old"},
            ) as resp:
                assert resp.status == 200
                delete_payload = await resp.json()

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"channel": "all", "limit": "5", "messages": "1"},
            ) as resp:
                assert resp.status == 200
                after_delete_payload = await resp.json()

            async with session.delete(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"session_id": "web:missing"},
            ) as resp:
                assert resp.status == 404

            async with session.delete(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"channel": "web"},
            ) as resp:
                assert resp.status == 200
                clear_payload = await resp.json()

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions",
                params={"channel": "all", "limit": "5", "messages": "1"},
            ) as resp:
                assert resp.status == 200
                after_clear_payload = await resp.json()

        assert delete_payload == {"ok": True, "session_id": "web:browser-old", "deleted": 1}
        assert [item["session_id"] for item in after_delete_payload["sessions"]] == ["telegram:123", "web:browser-new"]
        assert clear_payload == {"ok": True, "channel": "web", "deleted": 2}
        assert [item["session_id"] for item in after_clear_payload["sessions"]] == ["telegram:123"]
        assert await storage.get_messages("web:browser-new") == []
        assert await storage.get_messages("web:browser-new:subagent:task_abc12345") == []
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_sessions_api():
    asyncio.run(_run_web_sessions_api())


async def _run_web_run_cancel_api():
    storage = MemoryStorage()
    await storage.create_run("web_custom:browser-1", "run-1", status="running", created_at=100.0)
    await storage.create_run("web_custom:browser-1", "run-2", status="completed", created_at=101.0)
    cancel_calls = []

    class CancelAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.storage = storage

        async def request_run_cancel(self, session_id, run_id, *, channel=None, external_chat_id=None):
            cancel_calls.append((session_id, run_id, channel, external_chat_id))
            return run_id == "run-1"

    agent = CancelAgent()
    queue = MessageQueue(agent)
    cancelled_sessions = []

    async def fake_cancel_session(session_id, channel=None):
        cancelled_sessions.append((session_id, channel))
        return 1

    queue.cancel_session = fake_cancel_session
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "id": "web_custom",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/api/runs/run-1/cancel",
                params={"session_id": "web_custom:browser-1"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            assert payload == {
                "ok": True,
                "session_id": "web_custom:browser-1",
                "run_id": "run-1",
                "status": "cancelling",
            }
            assert cancel_calls[0] == ("web_custom:browser-1", "run-1", "web_custom", "browser-1")
            assert cancelled_sessions == [("web_custom:browser-1", None)]

            async with session.post(
                f"http://127.0.0.1:{port}/api/runs/run-2/cancel",
                params={"session_id": "web_custom:browser-1"},
            ) as resp:
                assert resp.status == 409

            async with session.post(
                f"http://127.0.0.1:{port}/api/runs/missing-run/cancel",
                params={"session_id": "web_custom:browser-1"},
            ) as resp:
                assert resp.status == 404
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_run_cancel_api():
    asyncio.run(_run_web_run_cancel_api())


async def _run_web_file_change_revert_api():
    calls = []

    class RevertAgent(EchoAgent):
        async def revert_run_file_change(self, session_id, run_id, change_id, *, dry_run=True):
            calls.append((session_id, run_id, change_id, dry_run))
            if change_id == 404:
                return {"status": "not_found", "ok": False, "reason": "missing"}
            return {"status": "applied", "ok": True, "applied": not dry_run, "change_id": change_id}

    queue = MessageQueue(RevertAgent())
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/api/runs/run-1/file-changes/7/revert",
                params={"session_id": "web:browser-1"},
                json={"dry_run": False},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            async with session.post(
                f"http://127.0.0.1:{port}/api/runs/run-1/file-changes/404/revert",
                params={"session_id": "web:browser-1"},
                json={"dry_run": False},
            ) as resp:
                assert resp.status == 404

        assert payload == {"ok": True, "revert": {"status": "applied", "ok": True, "applied": True, "change_id": 7}}
        assert calls == [
            ("web:browser-1", "run-1", 7, False),
            ("web:browser-1", "run-1", 404, False),
        ]
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_file_change_revert_api():
    asyncio.run(_run_web_file_change_revert_api())


async def _run_web_permissions_api():
    permission = SimpleNamespace(
        request_id="perm-1",
        tool_name="apply_patch",
        params={"path": Path("notes.txt")},
        reason="tool requires approval",
        status="pending",
        session_id="web:browser-1",
        run_id="run-1",
        channel="web",
        external_chat_id="browser-1",
        created_at=100.0,
        expires_at=160.0,
        resolved_at=None,
        resolution_reason="",
        timed_out=False,
    )
    deny_permission = SimpleNamespace(
        request_id="perm-2",
        tool_name="exec",
        params={"command": "git reset --hard HEAD"},
        reason="tool requires approval",
        status="created",
        session_id="web:browser-1",
        run_id="run-1",
        channel="web",
        external_chat_id="browser-1",
        created_at=101.0,
        expires_at=161.0,
        resolved_at=None,
        resolution_reason="",
        timed_out=False,
    )
    missing_calls = []

    class PermissionAgent(EchoAgent):
        def pending_permission_requests(self):
            return [item for item in [permission, deny_permission] if item.status == "pending"]

        async def approve_permission_request(self, request_id):
            if request_id != permission.request_id or permission.status != "pending":
                missing_calls.append(("approve", request_id))
                return None
            permission.status = "approved"
            permission.resolved_at = 120.0
            permission.resolution_reason = "approved once"
            deny_permission.status = "pending"
            return permission

        async def deny_permission_request(self, request_id, reason="user denied approval"):
            if request_id != deny_permission.request_id or deny_permission.status != "pending":
                missing_calls.append(("deny", request_id))
                return None
            deny_permission.status = "denied"
            deny_permission.resolved_at = 121.0
            deny_permission.resolution_reason = reason
            return deny_permission

    queue = MessageQueue(PermissionAgent())
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/permissions") as resp:
                assert resp.status == 200
                payload = await resp.json()

            assert payload == {
                "permissions": [
                    {
                        "request_id": "perm-1",
                        "tool_name": "apply_patch",
                        "params": {"path": "notes.txt"},
                        "reason": "tool requires approval",
                        "status": "pending",
                        "action_type": "edit",
                        "risk_level": "medium",
                        "risk_levels": ["write"],
                        "resource": "notes.txt",
                        "preview": "notes.txt",
                        "recommended_decision": "approve",
                        "session_id": "web:browser-1",
                        "run_id": "run-1",
                        "channel": "web",
                        "external_chat_id": "browser-1",
                        "created_at": 100.0,
                        "expires_at": 160.0,
                        "resolved_at": None,
                        "resolution_reason": "",
                        "timed_out": False,
                    }
                ]
            }

            async with session.post(f"http://127.0.0.1:{port}/api/permissions/perm-1/approve") as resp:
                assert resp.status == 200
                approved_payload = await resp.json()
            assert approved_payload["ok"] is True
            assert approved_payload["permission"]["status"] == "approved"
            assert approved_payload["permission"]["resolution_reason"] == "approved once"

            async with session.post(
                f"http://127.0.0.1:{port}/api/permissions/perm-2/deny",
                json={"reason": "not now"},
            ) as resp:
                assert resp.status == 200
                denied_payload = await resp.json()
            assert denied_payload["ok"] is True
            assert denied_payload["permission"]["status"] == "denied"
            assert denied_payload["permission"]["resolution_reason"] == "not now"
            assert denied_payload["permission"]["action_type"] == "destructive"
            assert denied_payload["permission"]["recommended_decision"] == "deny"
            assert denied_payload["permission"]["destructive_reason"] == "git reset --hard"

            async with session.post(
                f"http://127.0.0.1:{port}/api/permissions/missing/deny",
                json={"reason": "not now"},
            ) as resp:
                assert resp.status == 404
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass

    assert missing_calls == [("deny", "missing")]


def test_web_adapter_exposes_permissions_api():
    asyncio.run(_run_web_permissions_api())


async def _run_web_worktree_cleanup_api(tmp_path: Path):
    marker_dir = tmp_path / "sandbox"
    marker_dir.mkdir()
    (marker_dir / ".opensprite-worktree.json").write_text("{}", encoding="utf-8")
    cleanup_calls = []
    trace_events = []

    class WorktreeAgent(EchoAgent):
        def cleanup_worktree_sandbox(self, sandbox_path):
            cleanup_calls.append(sandbox_path)
            return {"ok": True, "status": "removed", "sandbox_path": sandbox_path}

        async def _emit_run_event(self, session_id, run_id, event_type, payload, **kwargs):
            trace_events.append((session_id, run_id, event_type, payload, kwargs))

    queue = MessageQueue(WorktreeAgent())
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/api/worktrees/cleanup",
                json={"sandbox_path": str(marker_dir), "session_id": "web:browser-1", "run_id": "run-1"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            async with session.post(f"http://127.0.0.1:{port}/api/worktrees/cleanup", json={}) as resp:
                assert resp.status == 400

        assert payload == {
            "ok": True,
            "cleanup": {"ok": True, "status": "removed", "sandbox_path": str(marker_dir)},
        }
        assert cleanup_calls == [str(marker_dir)]
        assert [(event[0], event[1], event[2]) for event in trace_events] == [
            ("web:browser-1", "run-1", WORKTREE_CLEANUP_STARTED_EVENT),
            ("web:browser-1", "run-1", WORKTREE_CLEANUP_COMPLETED_EVENT),
        ]
        assert trace_events[-1][3]["sandbox_path"] == str(marker_dir)
        assert trace_events[-1][3]["ok"] is True
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_worktree_cleanup_api(tmp_path):
    asyncio.run(_run_web_worktree_cleanup_api(tmp_path))


async def _run_web_settings_provider_api(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    applied_log_configs = []
    monkeypatch.setattr("opensprite.channels.web.setup_log", lambda log_config: applied_log_configs.append(log_config))

    class SettingsAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.config_path = config_path
            self.reloads = []
            self.llm_calls = SimpleNamespace(config=None)

        def reload_llm_from_config(self, config):
            active = config.llm.get_active()
            self.reloads.append((config.llm.default, active.model))
            return {"provider_id": config.llm.default, "model": active.model, "configured": config.is_llm_configured}

    agent = SettingsAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/channels") as resp:
                assert resp.status == 200
                channels_payload = await resp.json()

            channel_ids = {channel["id"] for channel in channels_payload["channels"]}
            assert channel_ids == set()
            assert channels_payload["connected"] == []
            assert [channel["id"] for channel in channels_payload["available"]] == ["telegram"]

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/channels/web",
                json={"enabled": False, "settings": {}},
            ) as resp:
                assert resp.status == 400

            telegram = channels_payload["available"][0]
            assert telegram["type"] == "telegram"
            assert telegram["requires_token"] is True

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/channels",
                json={"type": "telegram", "name": "Work Telegram", "token": "telegram-secret"},
            ) as resp:
                assert resp.status == 200
                channel_update_payload = await resp.json()

            assert channel_update_payload["restart_required"] is True
            assert channel_update_payload["channel"]["id"] == "telegram_work_telegram"
            assert channel_update_payload["channel"]["type"] == "telegram"
            assert channel_update_payload["channel"]["name"] == "Work Telegram"
            assert channel_update_payload["channel"]["enabled"] is True
            assert channel_update_payload["channel"]["token_configured"] is True
            assert channel_update_payload["channel"]["settings"] == {}
            channels = json.loads((tmp_path / "channels.json").read_text(encoding="utf-8"))
            telegram_config = channels["instances"]["telegram_work_telegram"]
            assert telegram_config["type"] == "telegram"
            assert telegram_config["enabled"] is True
            assert telegram_config["token"] == "telegram-secret"
            assert telegram_config["drop_pending_updates"] is False
            assert telegram_config["poll_timeout"] == 10

            async with session.get(f"http://127.0.0.1:{port}/api/settings/channels") as resp:
                assert resp.status == 200
                connected_payload = await resp.json()

            assert [channel["id"] for channel in connected_payload["connected"]] == ["telegram_work_telegram"]
            assert [channel["id"] for channel in connected_payload["available"]] == ["telegram"]

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/channels/telegram_work_telegram/disconnect"
            ) as resp:
                assert resp.status == 200
                channel_disconnect_payload = await resp.json()

            assert channel_disconnect_payload == {
                "ok": True,
                "channel_id": "telegram_work_telegram",
                "instance_id": "telegram_work_telegram",
                "restart_required": True,
            }
            channels = json.loads((tmp_path / "channels.json").read_text(encoding="utf-8"))
            assert "telegram_work_telegram" not in channels["instances"]

            async with session.get(f"http://127.0.0.1:{port}/api/settings/schedule") as resp:
                assert resp.status == 200
                schedule_payload = await resp.json()

            assert schedule_payload["default_timezone"] == "UTC"
            assert "Asia/Taipei" in schedule_payload["common_timezones"]

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/schedule",
                json={"default_timezone": "Not/AZone"},
            ) as resp:
                assert resp.status == 400

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/schedule",
                json={"default_timezone": "Asia/Taipei"},
            ) as resp:
                assert resp.status == 200
                schedule_update_payload = await resp.json()

            assert schedule_update_payload["default_timezone"] == "Asia/Taipei"
            assert schedule_update_payload["restart_required"] is False
            assert schedule_update_payload["runtime_reloaded"] is True
            assert schedule_update_payload["runtime"] == {"default_timezone": "Asia/Taipei", "tool_updated": False}
            assert agent.tools_config.cron.default_timezone == "Asia/Taipei"
            main_config = json.loads(config_path.read_text(encoding="utf-8"))
            assert main_config["tools"]["cron"]["default_timezone"] == "Asia/Taipei"

            async with session.get(f"http://127.0.0.1:{port}/api/settings/llm") as resp:
                assert resp.status == 200
                llm_payload = await resp.json()

            assert llm_payload["llm"]["effective_request"]["configured"] is False

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/llm",
                json={},
            ) as resp:
                assert resp.status == 200
                llm_update_payload = await resp.json()

            assert llm_update_payload["llm"]["effective_request"]["configured"] is False

            async with session.get(f"http://127.0.0.1:{port}/api/settings/log") as resp:
                assert resp.status == 200
                log_payload = await resp.json()

            assert log_payload["log"]["level"] == "INFO"
            assert "DEBUG" in log_payload["log"]["levels"]

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/log",
                json={
                    "enabled": True,
                    "level": "DEBUG",
                    "retention_days": 14,
                    "log_system_prompt": True,
                    "log_system_prompt_lines": 0,
                    "log_reasoning_details": False,
                },
            ) as resp:
                assert resp.status == 200
                log_update_payload = await resp.json()

            assert log_update_payload["restart_required"] is False
            assert log_update_payload["runtime_reloaded"] is True
            assert log_update_payload["log"]["level"] == "DEBUG"
            loaded_config = Config.from_json(config_path)
            assert loaded_config.log.level == "DEBUG"
            assert loaded_config.log.retention_days == 14
            assert applied_log_configs[-1].level == "DEBUG"

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/log",
                json={
                    "enabled": "false",
                    "log_system_prompt": "false",
                    "log_reasoning_details": "false",
                },
            ) as resp:
                assert resp.status == 200
                log_disable_payload = await resp.json()

            assert log_disable_payload["log"]["enabled"] is False
            loaded_config = Config.from_json(config_path)
            assert loaded_config.log.enabled is False
            assert loaded_config.log.log_system_prompt is False
            assert loaded_config.log.log_reasoning_details is False

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/log",
                json={"level": "LOUD"},
            ) as resp:
                assert resp.status == 400

            async with session.get(f"http://127.0.0.1:{port}/api/settings/providers") as resp:
                assert resp.status == 200
                providers_payload = await resp.json()

            assert providers_payload["connected"] == []
            assert {provider["id"] for provider in providers_payload["available"]} >= {"openai", "openrouter", "minimax"}

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/providers/ollama/connect",
                json={},
            ) as resp:
                assert resp.status == 200
                ollama_connect_payload = await resp.json()

            assert ollama_connect_payload["provider"]["auth_type"] == "optional_api_key"
            assert ollama_connect_payload["provider"]["requires_api_key"] is False
            assert ollama_connect_payload["provider"]["api_key_optional"] is True

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/models/select",
                json={"provider_id": "ollama", "model": "qwen3:14b"},
            ) as resp:
                assert resp.status == 200
                ollama_select_payload = await resp.json()

            assert ollama_select_payload["restart_required"] is False
            assert ollama_select_payload["runtime_reloaded"] is True
            assert ollama_select_payload["runtime"] == {
                "provider_id": "ollama",
                "model": "qwen3:14b",
                "configured": True,
            }
            assert agent.reloads[-1] == ("ollama", "qwen3:14b")

            async with session.get(f"http://127.0.0.1:{port}/api/settings/auth/openai-codex") as resp:
                assert resp.status == 200
                codex_status = await resp.json()

            assert codex_status["provider"] == "openai-codex"
            assert codex_status["configured"] is False
            assert codex_status["path"] == str(tmp_path / "auth" / "openai-codex.json")

            save_codex_token(CodexToken(access_token="codex-token", account_id="acct-1"), tmp_path)

            async with session.get(f"http://127.0.0.1:{port}/api/settings/auth/openai-codex") as resp:
                assert resp.status == 200
                codex_configured_status = await resp.json()

            assert codex_configured_status["configured"] is True
            assert codex_configured_status["account_id"] == "acct-1"

            monkeypatch.setattr(
                codex_module,
                "codex_start_device_auth",
                lambda: SimpleNamespace(
                    verification_uri="https://auth.openai.com/codex/device",
                    user_code="ABCD",
                    device_auth_id="device-1",
                    poll_interval=3,
                    expires_in=600,
                ),
            )

            async with session.post(f"http://127.0.0.1:{port}/api/settings/auth/openai-codex/login") as resp:
                assert resp.status == 200
                codex_login_payload = await resp.json()

            assert codex_login_payload["mode"] == "web_device_code"
            assert codex_login_payload["verification_uri"] == "https://auth.openai.com/codex/device"
            assert codex_login_payload["user_code"] == "ABCD"
            assert codex_login_payload["device_auth_id"] == "device-1"

            delete_codex_token(tmp_path)

            def fake_poll_device_auth(device_auth_id, user_code, *, app_home=None, timeout_seconds=15.0):
                assert device_auth_id == "device-1"
                assert user_code == "ABCD"
                save_codex_token(CodexToken(access_token="codex-token-2", account_id="acct-2"), app_home)
                return SimpleNamespace(status="authorized", token=CodexToken(access_token="codex-token-2"))

            monkeypatch.setattr(codex_module, "codex_poll_device_auth", fake_poll_device_auth)

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/auth/openai-codex/poll",
                json={"device_auth_id": "device-1", "user_code": "ABCD"},
            ) as resp:
                assert resp.status == 200
                codex_poll_payload = await resp.json()

            assert codex_poll_payload["status"] == "authorized"
            assert codex_poll_payload["auth"]["configured"] is True
            assert codex_poll_payload["auth"]["account_id"] == "acct-2"

            async with session.post(f"http://127.0.0.1:{port}/api/settings/auth/openai-codex/logout") as resp:
                assert resp.status == 200
                codex_logout_payload = await resp.json()

            assert codex_logout_payload["removed"] is True
            assert not (tmp_path / "auth" / "openai-codex.json").exists()

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/providers/openai/connect",
                json={"api_key": "secret-key"},
            ) as resp:
                assert resp.status == 200
                connect_payload = await resp.json()

            assert connect_payload["provider"]["api_key_configured"] is True
            assert connect_payload["provider"]["credential_id"].startswith("cred_")
            assert connect_payload["provider"]["credential_preview"] == "secr...-key"
            assert "api_key" not in connect_payload["provider"]

            async with session.get(f"http://127.0.0.1:{port}/api/settings/credentials") as resp:
                assert resp.status == 200
                credentials_payload = await resp.json()

            openai_credentials = credentials_payload["credentials"]["openai"]
            assert openai_credentials[0]["secret_preview"] == "secr...-key"
            assert "secret" not in openai_credentials[0]

            async with session.get(f"http://127.0.0.1:{port}/api/settings/models") as resp:
                assert resp.status == 200
                models_payload = await resp.json()

            assert models_payload["providers"][0]["id"] == "openai"
            selected_openai_model = models_payload["providers"][0]["models"][0]

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/models/select",
                json={"provider_id": "openai", "model": selected_openai_model},
            ) as resp:
                assert resp.status == 200
                select_payload = await resp.json()

            assert select_payload["restart_required"] is False
            assert select_payload["runtime_reloaded"] is True
            assert select_payload["runtime"] == {
                "provider_id": "openai",
                "model": selected_openai_model,
                "configured": True,
            }
            assert agent.reloads[-1] == ("openai", selected_openai_model)
            providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
            assert providers["openai"]["api_key"] == ""
            assert providers["openai"]["credential_id"] == connect_payload["provider"]["credential_id"]
            assert providers["openai"]["enabled"] is True
            assert providers["openai"]["model"] == selected_openai_model

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/credentials/default",
                json={"provider": "openai", "credential_id": connect_payload["provider"]["credential_id"]},
            ) as resp:
                assert resp.status == 200

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/providers/openai/credential",
                json={"credential_id": connect_payload["provider"]["credential_id"]},
            ) as resp:
                assert resp.status == 200

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/providers/openrouter/connect",
                json={"api_key": "router-key"},
            ) as resp:
                assert resp.status == 200

            async with session.post(f"http://127.0.0.1:{port}/api/settings/providers/openrouter/disconnect") as resp:
                assert resp.status == 200
                inactive_disconnect_payload = await resp.json()

            assert inactive_disconnect_payload == {
                "ok": True,
                "provider_id": "openrouter",
                "restart_required": False,
                "runtime_reloaded": True,
                "runtime": {"provider_id": "openai", "model": selected_openai_model, "configured": True},
            }
            assert agent.reloads[-1] == ("openai", selected_openai_model)
            providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
            assert set(providers) == {"openai", "ollama"}

            async with session.post(f"http://127.0.0.1:{port}/api/settings/providers/ollama/disconnect") as resp:
                assert resp.status == 200
                inactive_ollama_disconnect_payload = await resp.json()

            assert inactive_ollama_disconnect_payload == {
                "ok": True,
                "provider_id": "ollama",
                "restart_required": False,
                "runtime_reloaded": True,
                "runtime": {"provider_id": "openai", "model": selected_openai_model, "configured": True},
            }
            assert agent.reloads[-1] == ("openai", selected_openai_model)

            async with session.post(f"http://127.0.0.1:{port}/api/settings/providers/openai/disconnect") as resp:
                assert resp.status == 200
                disconnect_payload = await resp.json()

            assert disconnect_payload == {
                "ok": True,
                "provider_id": "openai",
                "restart_required": False,
                "runtime_reloaded": True,
                "runtime": {"provider_id": None, "model": "", "configured": False},
            }
            assert agent.reloads[-1] == (None, "")
            main_config = json.loads(config_path.read_text(encoding="utf-8"))
            providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
            assert main_config["llm"]["default"] is None
            assert providers == {}
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_settings_provider_api(tmp_path, monkeypatch):
    asyncio.run(_run_web_settings_provider_api(tmp_path, monkeypatch))


async def _run_web_mcp_settings_api(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    class SettingsAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.config_path = config_path
            self.reloads = 0

        async def reload_mcp_from_config(self):
            self.reloads += 1
            return f"reload-{self.reloads}"

    agent = SettingsAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/mcp") as resp:
                assert resp.status == 200
                payload = await resp.json()

            assert payload["servers"] == []
            assert payload["mcp_servers_file"] == str(tmp_path / "mcp_servers.json")

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/mcp",
                json={
                    "server_id": "filesystem",
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                    "env": {"TOKEN": "secret-token"},
                    "tool_timeout": 12,
                    "enabled_tools": ["*"],
                },
            ) as resp:
                assert resp.status == 200
                response_text = await resp.text()
                created = json.loads(response_text)

            assert "secret-token" not in response_text
            assert created["restart_required"] is False
            assert created["runtime_reloaded"] is True
            assert created["reload_message"] == "reload-1"
            assert created["server"]["id"] == "filesystem"
            assert created["server"]["env_configured"] is True
            assert created["server"]["env_keys"] == ["TOKEN"]
            mcp_file = json.loads((tmp_path / "mcp_servers.json").read_text(encoding="utf-8"))
            assert mcp_file["filesystem"]["env"] == {"TOKEN": "secret-token"}

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/mcp/filesystem",
                json={
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "demo-mcp"],
                    "enabled_tools": ["read_file"],
                },
            ) as resp:
                assert resp.status == 200
                updated = await resp.json()

            assert updated["reload_message"] == "reload-2"
            assert updated["server"]["enabled_tools"] == ["read_file"]
            mcp_file = json.loads((tmp_path / "mcp_servers.json").read_text(encoding="utf-8"))
            assert mcp_file["filesystem"]["env"] == {"TOKEN": "secret-token"}

            async with session.post(f"http://127.0.0.1:{port}/api/settings/mcp/reload") as resp:
                assert resp.status == 200
                reloaded = await resp.json()

            assert reloaded["reload_message"] == "reload-3"

            async with session.delete(f"http://127.0.0.1:{port}/api/settings/mcp/filesystem") as resp:
                assert resp.status == 200
                removed = await resp.json()

            assert removed["reload_message"] == "reload-4"
            assert removed["servers"] == []
            assert json.loads((tmp_path / "mcp_servers.json").read_text(encoding="utf-8")) == {}
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_exposes_mcp_settings_api(tmp_path):
    asyncio.run(_run_web_mcp_settings_api(tmp_path))


async def _run_web_schedule_settings_creates_default_config(tmp_path: Path):
    agent = EchoAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/settings/schedule") as resp:
                assert resp.status == 200
                payload = await resp.json()

        assert payload["default_timezone"] == "UTC"
        assert (tmp_path / ".opensprite" / "opensprite.json").is_file()
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_adapter_schedule_settings_create_default_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    asyncio.run(_run_web_schedule_settings_creates_default_config(tmp_path))


async def _run_web_cron_jobs_api(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)
    workspace_root = tmp_path / "workspace"
    seeded_session_id = "telegram:user-1"
    seeded_service = CronService(
        get_session_workspace(seeded_session_id, workspace_root=workspace_root) / "cron" / "jobs.json",
        session_id=seeded_session_id,
    )
    seeded_job = seeded_service.add_job(
        name="seeded telegram job",
        schedule=CronSchedule(kind="every", every_ms=120_000),
        message="seeded status",
        deliver=True,
        channel="telegram",
        external_chat_id="user-1",
    )
    triggered: list[tuple[str, str]] = []

    async def on_job(session_id, job):
        triggered.append((session_id, job.payload.message))
        return None

    class CronAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.config_path = config_path
            self.cron_manager = CronManager(workspace_root=workspace_root, on_job=on_job)

    agent = CronAgent()
    queue = MessageQueue(agent)
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None
        session_id = "web:browser-1"

        async with ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/api/cron/jobs",
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            assert payload["session_id"] is None
            assert [(job["session_id"], job["id"]) for job in payload["jobs"]] == [(seeded_session_id, seeded_job.id)]

            async with session.post(
                f"http://127.0.0.1:{port}/api/cron/jobs",
                json={
                    "session_id": session_id,
                    "kind": "every",
                    "every_seconds": 60,
                    "message": "check status",
                    "deliver": True,
                },
            ) as resp:
                assert resp.status == 200
                created = await resp.json()

            job_id = created["job"]["id"]
            assert created["job"]["schedule"]["kind"] == "every"
            assert created["job"]["session_id"] == session_id
            assert created["job"]["payload"]["channel"] == "web"
            assert created["job"]["payload"]["external_chat_id"] == "browser-1"

            async with session.get(f"http://127.0.0.1:{port}/api/cron/jobs") as resp:
                assert resp.status == 200
                all_jobs = await resp.json()

            assert {job["session_id"] for job in all_jobs["jobs"]} == {session_id, seeded_session_id}

            async with session.put(
                f"http://127.0.0.1:{port}/api/cron/jobs/{job_id}",
                json={
                    "session_id": session_id,
                    "kind": "cron",
                    "cron_expr": "0 9 * * *",
                    "tz": "Asia/Taipei",
                    "message": "daily check",
                    "deliver": False,
                },
            ) as resp:
                assert resp.status == 200
                updated = await resp.json()

            assert updated["job"]["schedule"]["kind"] == "cron"
            assert updated["job"]["schedule"]["tz"] == "Asia/Taipei"
            assert updated["job"]["payload"]["deliver"] is False

            async with session.post(
                f"http://127.0.0.1:{port}/api/cron/jobs/{job_id}/pause",
                json={"session_id": session_id},
            ) as resp:
                assert resp.status == 200
                paused = await resp.json()

            assert paused["job"]["enabled"] is False

            async with session.post(
                f"http://127.0.0.1:{port}/api/cron/jobs/{job_id}/enable",
                json={"session_id": session_id},
            ) as resp:
                assert resp.status == 200
                enabled = await resp.json()

            assert enabled["job"]["enabled"] is True

            async with session.post(
                f"http://127.0.0.1:{port}/api/cron/jobs/{job_id}/run",
                json={"session_id": session_id},
            ) as resp:
                assert resp.status == 200

            assert triggered == [(session_id, "daily check")]

            async with session.delete(
                f"http://127.0.0.1:{port}/api/cron/jobs/{job_id}",
                params={"session_id": session_id},
            ) as resp:
                assert resp.status == 200

            async with session.get(
                f"http://127.0.0.1:{port}/api/cron/jobs",
            ) as resp:
                assert resp.status == 200
                final_payload = await resp.json()

            assert [(job["session_id"], job["id"]) for job in final_payload["jobs"]] == [(seeded_session_id, seeded_job.id)]
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass
        await agent.cron_manager.stop()


def test_web_adapter_cron_jobs_api(tmp_path):
    asyncio.run(_run_web_cron_jobs_api(tmp_path))


async def _run_web_channel_settings_hot_reload(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    class SettingsAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.config_path = config_path

    class FakeChannelManager:
        def __init__(self):
            self.calls = []

        async def apply(self, channels_config, *, include_fixed=False):
            self.calls.append((channels_config, include_fixed))
            return {
                "ok": True,
                "started": ["telegram_work_telegram"],
                "stopped": [],
                "restarted": [],
                "unchanged": [],
                "failed": [],
                "running": ["telegram_work_telegram"],
            }

    agent = SettingsAgent()
    queue = MessageQueue(agent)
    channel_manager = FakeChannelManager()
    queue.channel_manager = channel_manager
    adapter = WebAdapter(
        mq=queue,
        config={
            "host": "127.0.0.1",
            "port": 0,
            "path": "/ws",
            "health_path": "/healthz",
            "frontend_auto_build": False,
        },
    )
    adapter_task = asyncio.create_task(adapter.run())

    try:
        await adapter.wait_until_started()
        port = adapter.bound_port
        assert port is not None

        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/channels",
                json={"type": "telegram", "name": "Work Telegram", "token": "telegram-secret"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

        assert payload["restart_required"] is False
        assert payload["runtime_reloaded"] is True
        assert payload["runtime"]["started"] == ["telegram_work_telegram"]
        assert payload["channel"]["id"] == "telegram_work_telegram"
        assert len(channel_manager.calls) == 1
        assert channel_manager.calls[0][1] is False
    finally:
        adapter_task.cancel()
        try:
            await adapter_task
        except asyncio.CancelledError:
            pass


def test_web_channel_settings_hot_reload(tmp_path):
    asyncio.run(_run_web_channel_settings_hot_reload(tmp_path))
