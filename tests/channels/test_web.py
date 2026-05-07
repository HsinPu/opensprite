import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from aiohttp import ClientSession

from opensprite.bus.dispatcher import MessageQueue
from opensprite.bus.events import RunEvent, SessionStatusEvent
from opensprite.bus.message import AssistantMessage
from opensprite.channels.web import WebAdapter
from opensprite.config import Config
from opensprite.auth.codex import CodexToken, delete_codex_token, save_codex_token
import opensprite.auth.codex as codex_module
from opensprite.context.paths import get_session_workspace
from opensprite.cron import CronManager, CronSchedule, CronService
from opensprite.storage import MemoryStorage, StoredDelegatedTask, StoredMessage, StoredWorkState
from opensprite.storage.base import StoredBackgroundProcess


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
                        event_type="run_started",
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
                    "event_type": "run_started",
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
                        event_type="run_started",
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
        "task_intent.detected",
        payload={"objective": "inspect run timeline", "path": Path("notes.txt")},
        created_at=101.0,
    )
    await storage.add_run_event(
        "web:browser-1",
        "run-1",
        "completion_gate.evaluated",
        payload={"status": "complete"},
        created_at=102.0,
    )
    await storage.add_run_event(
        "web:browser-1",
        "run-1",
        "tool_started",
        payload={"tool_name": "apply_patch", "tool_call_id": "call-1", "args_preview": "apply patch"},
        created_at=102.5,
    )
    await storage.add_run_event(
        "web:browser-1",
        "run-1",
        "tool_result",
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
            started_at=140.0,
            updated_at=160.0,
            finished_at=160.0,
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
                "task_intent.detected",
                "completion_gate.evaluated",
                "tool_started",
                "tool_result",
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
            assert processes_payload["processes"][1]["command"] == "npm run dev"
            assert processes_payload["processes"][1]["metadata"] == {"source": "test"}

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
                "task_intent.detected",
                "completion_gate.evaluated",
                "tool_started",
                "tool_result",
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
        params={"command": "pytest"},
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

    class WorktreeAgent(EchoAgent):
        def cleanup_worktree_sandbox(self, sandbox_path):
            cleanup_calls.append(sandbox_path)
            return {"ok": True, "status": "removed", "sandbox_path": sandbox_path}

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
                json={"sandbox_path": str(marker_dir)},
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

    class SettingsAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.config_path = config_path
            self.reloads = []

        def reload_llm_from_config(self, config):
            active = config.llm.get_active()
            self.reloads.append((config.llm.default, active.model, config.llm.pass_decoding_params))
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

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/channels/console",
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

            assert llm_payload["llm"]["pass_decoding_params"] is True

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/llm",
                json={"pass_decoding_params": False},
            ) as resp:
                assert resp.status == 200
                llm_update_payload = await resp.json()

            assert llm_update_payload["llm"]["pass_decoding_params"] is False
            assert llm_update_payload["restart_required"] is False
            assert llm_update_payload["runtime_reloaded"] is True
            assert agent.reloads[-1][2] is False
            loaded_config = Config.from_json(config_path)
            assert loaded_config.llm.pass_decoding_params is False

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
            assert agent.reloads[-1] == ("ollama", "qwen3:14b", False)

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
            assert agent.reloads[-1] == ("openai", selected_openai_model, False)
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
            assert agent.reloads[-1] == ("openai", selected_openai_model, False)
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
            assert agent.reloads[-1] == ("openai", selected_openai_model, False)

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
            assert agent.reloads[-1] == (None, "", False)
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
