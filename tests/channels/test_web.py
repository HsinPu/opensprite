import asyncio
import json
from pathlib import Path

from aiohttp import ClientSession

from opensprite.bus.dispatcher import MessageQueue
from opensprite.bus.events import RunEvent
from opensprite.bus.message import AssistantMessage
from opensprite.channels.web import WebAdapter
from opensprite.config import Config
from opensprite.storage import MemoryStorage


class EchoAgent:
    def __init__(self):
        self.seen_messages = []

    async def process(self, user_message):
        self.seen_messages.append(user_message)
        return AssistantMessage(
            text=f"echo:{user_message.text}",
            channel="web",
            chat_id=user_message.chat_id,
            session_chat_id=user_message.session_chat_id,
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
                assert payload == {"ok": True, "channel": "web"}

            async with session.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                session_frame = await ws.receive_json(timeout=2)
                assert session_frame["type"] == "session"
                assert session_frame["channel"] == "web"
                assert session_frame["session_chat_id"].startswith("web:")

                await queue.bus.publish_run_event(
                    RunEvent(
                        channel="web",
                        chat_id=session_frame["chat_id"],
                        session_chat_id=session_frame["session_chat_id"],
                        run_id="run-test",
                        event_type="run_started",
                        payload={"status": "running"},
                        created_at=123.0,
                    )
                )
                run_frame = await ws.receive_json(timeout=2)
                assert run_frame == {
                    "type": "run_event",
                    "channel": "web",
                    "chat_id": session_frame["chat_id"],
                    "session_chat_id": session_frame["session_chat_id"],
                    "run_id": "run-test",
                    "event_type": "run_started",
                    "payload": {"status": "running"},
                    "created_at": 123.0,
                }

                await ws.send_str("hello from browser")
                reply = await ws.receive_json(timeout=2)
                assert reply == {
                    "type": "message",
                    "channel": "web",
                    "chat_id": session_frame["chat_id"],
                    "session_chat_id": session_frame["session_chat_id"],
                    "text": "echo:hello from browser",
                    "metadata": {"source": "test"},
                }

                await ws.send_json({"chat_id": "browser-2", "text": "second round"})
                second_reply = await ws.receive_json(timeout=2)
                assert second_reply["chat_id"] == "browser-2"
                assert second_reply["session_chat_id"] == "web:browser-2"
                assert second_reply["text"] == "echo:second round"

        seen_sessions = [message.session_chat_id for message in agent.seen_messages]
        assert seen_sessions == [session_frame["session_chat_id"], "web:browser-2"]
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


async def _run_web_run_events_api():
    storage = MemoryStorage()
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
    await storage.add_run_part(
        "web:browser-1",
        "run-1",
        "tool_call",
        content="apply patch",
        tool_name="apply_patch",
        metadata={"path": Path("notes.txt")},
        created_at=103.0,
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
        diff="--- a/notes.txt\n+++ b/notes.txt\n",
        metadata={"verified": True},
        created_at=104.0,
    )
    await storage.create_run("web:browser-1", "run-2", created_at=200.0)

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
                params={"chat_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            assert payload["run_id"] == "run-1"
            assert payload["chat_id"] == "web:browser-1"
            assert [event["event_type"] for event in payload["events"]] == [
                "task_intent.detected",
                "completion_gate.evaluated",
            ]
            assert payload["events"][0]["event_id"] == 1
            assert payload["events"][0]["payload"] == {
                "objective": "inspect run timeline",
                "path": "notes.txt",
            }
            assert payload["events"][1]["created_at"] == 102.0

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs",
                params={"chat_id": "web:browser-1", "limit": "1"},
            ) as resp:
                assert resp.status == 200
                runs_payload = await resp.json()

            assert runs_payload["chat_id"] == "web:browser-1"
            assert [run["run_id"] for run in runs_payload["runs"]] == ["run-2"]

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/run-1",
                params={"chat_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 200
                trace_payload = await resp.json()

            assert trace_payload["run"]["run_id"] == "run-1"
            assert trace_payload["run"]["metadata"] == {"objective": "notes.txt"}
            assert [event["event_type"] for event in trace_payload["events"]] == [
                "task_intent.detected",
                "completion_gate.evaluated",
            ]
            assert trace_payload["parts"] == [
                {
                    "part_id": 1,
                    "run_id": "run-1",
                    "chat_id": "web:browser-1",
                    "part_type": "tool_call",
                    "content": "apply patch",
                    "tool_name": "apply_patch",
                    "metadata": {"path": "notes.txt"},
                    "created_at": 103.0,
                }
            ]
            assert trace_payload["file_changes"][0]["change_id"] == 1
            assert trace_payload["file_changes"][0]["path"] == "notes.txt"
            assert trace_payload["file_changes"][0]["before_content"] == "old\n"
            assert trace_payload["file_changes"][0]["after_content"] == "new\n"

            async with session.get(f"http://127.0.0.1:{port}/api/runs/run-1/events") as resp:
                assert resp.status == 400

            async with session.get(f"http://127.0.0.1:{port}/api/runs") as resp:
                assert resp.status == 400

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs",
                params={"chat_id": "web:browser-1", "limit": "not-a-number"},
            ) as resp:
                assert resp.status == 400

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/missing-run/events",
                params={"chat_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 404

            async with session.get(
                f"http://127.0.0.1:{port}/api/runs/missing-run",
                params={"chat_id": "web:browser-1"},
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


async def _run_web_run_cancel_api():
    storage = MemoryStorage()
    await storage.create_run("web:browser-1", "run-1", status="running", created_at=100.0)
    await storage.create_run("web:browser-1", "run-2", status="completed", created_at=101.0)
    cancel_calls = []

    class CancelAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.storage = storage

        async def request_run_cancel(self, chat_id, run_id, *, channel=None, transport_chat_id=None):
            cancel_calls.append((chat_id, run_id, channel, transport_chat_id))
            return run_id == "run-1"

    agent = CancelAgent()
    queue = MessageQueue(agent)
    cancelled_sessions = []

    async def fake_cancel_chat(chat_id, channel=None):
        cancelled_sessions.append((chat_id, channel))
        return 1

    queue.cancel_chat = fake_cancel_chat
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
                f"http://127.0.0.1:{port}/api/runs/run-1/cancel",
                params={"chat_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()

            assert payload == {
                "ok": True,
                "chat_id": "web:browser-1",
                "run_id": "run-1",
                "status": "cancelling",
            }
            assert cancel_calls[0] == ("web:browser-1", "run-1", "web", "browser-1")
            assert cancelled_sessions == [("web:browser-1", None)]

            async with session.post(
                f"http://127.0.0.1:{port}/api/runs/run-2/cancel",
                params={"chat_id": "web:browser-1"},
            ) as resp:
                assert resp.status == 409

            async with session.post(
                f"http://127.0.0.1:{port}/api/runs/missing-run/cancel",
                params={"chat_id": "web:browser-1"},
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


async def _run_web_settings_provider_api(tmp_path: Path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    class SettingsAgent(EchoAgent):
        def __init__(self):
            super().__init__()
            self.config_path = config_path
            self.reloads = []

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
            async with session.get(f"http://127.0.0.1:{port}/api/settings/providers") as resp:
                assert resp.status == 200
                providers_payload = await resp.json()

            assert providers_payload["connected"] == []
            assert {provider["id"] for provider in providers_payload["available"]} >= {"openai", "openrouter", "minimax"}

            async with session.put(
                f"http://127.0.0.1:{port}/api/settings/providers/openai/connect",
                json={"api_key": "secret-key"},
            ) as resp:
                assert resp.status == 200
                connect_payload = await resp.json()

            assert connect_payload["provider"]["api_key_configured"] is True
            assert "api_key" not in connect_payload["provider"]

            async with session.get(f"http://127.0.0.1:{port}/api/settings/models") as resp:
                assert resp.status == 200
                models_payload = await resp.json()

            assert models_payload["providers"][0]["id"] == "openai"
            assert "gpt-4.1-mini" in models_payload["providers"][0]["models"]

            async with session.post(
                f"http://127.0.0.1:{port}/api/settings/models/select",
                json={"provider_id": "openai", "model": "gpt-4.1-mini"},
            ) as resp:
                assert resp.status == 200
                select_payload = await resp.json()

            assert select_payload["restart_required"] is False
            assert select_payload["runtime_reloaded"] is True
            assert select_payload["runtime"] == {
                "provider_id": "openai",
                "model": "gpt-4.1-mini",
                "configured": True,
            }
            assert agent.reloads[-1] == ("openai", "gpt-4.1-mini")
            providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
            assert providers["openai"]["api_key"] == "secret-key"
            assert providers["openai"]["enabled"] is True
            assert providers["openai"]["model"] == "gpt-4.1-mini"

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
                "runtime": {"provider_id": "openai", "model": "gpt-4.1-mini", "configured": True},
            }
            assert agent.reloads[-1] == ("openai", "gpt-4.1-mini")
            providers = json.loads((tmp_path / "llm.providers.json").read_text(encoding="utf-8"))
            assert set(providers) == {"openai"}

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


def test_web_adapter_exposes_settings_provider_api(tmp_path):
    asyncio.run(_run_web_settings_provider_api(tmp_path))
