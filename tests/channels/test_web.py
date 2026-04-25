import asyncio
from pathlib import Path

from aiohttp import ClientSession

from opensprite.bus.dispatcher import MessageQueue
from opensprite.bus.events import RunEvent
from opensprite.bus.message import AssistantMessage
from opensprite.channels.web import WebAdapter


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
