import asyncio
import base64
import hashlib
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.agent.execution import ContextCompactionEvent, ExecutionResult
from opensprite.bus import MessageBus
from opensprite.bus.events import OutboundMessage
from opensprite.config.schema import AgentConfig, Config, LogConfig, MemoryConfig, MessagesConfig, RecentSummaryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.bus.message import UserMessage
from opensprite.documents.active_task import create_active_task_store
from opensprite.storage import MemoryStorage
from opensprite.storage.base import StoredMessage
from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.process_runtime import BackgroundSession
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.shell_runtime import CapturedOutputChunk


class FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.last_history = None

    def build_system_prompt(self, chat_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, chat_id=None):
        self.last_history = list(history)
        return [{"role": "user", "content": current_message}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return messages

    def add_assistant_message(self, messages, content, tool_calls=None):
        return messages


class FakeProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        raise AssertionError("provider.chat should not be called in this test")

    def get_default_model(self) -> str:
        return "fake-model"


class FakeStorage:
    def __init__(self):
        self.saved = []

    async def get_messages(self, chat_id, limit=None):
        return []

    async def add_message(self, chat_id, message: StoredMessage):
        self.saved.append((chat_id, message.role, message.content, dict(message.metadata)))

    async def clear_messages(self, chat_id):
        return None

    async def get_consolidated_index(self, chat_id):
        return 0

    async def set_consolidated_index(self, chat_id, index):
        return None

    async def get_all_chats(self):
        return []


class HistoryStorage(FakeStorage):
    def __init__(self, messages):
        super().__init__()
        self.messages = list(messages)

    async def get_messages(self, chat_id, limit=None):
        if limit is None:
            return list(self.messages)
        return list(self.messages[-limit:])


class FakeBus:
    def __init__(self):
        self.outbound: list[OutboundMessage] = []

    async def publish_outbound(self, message: OutboundMessage) -> None:
        self.outbound.append(message)


class DummyTool(Tool):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "dummy"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs):
        return "ok"


class LargeSchemaTool(Tool):
    @property
    def name(self) -> str:
        return "large"

    @property
    def description(self) -> str:
        return "large schema tool"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "string",
                    "description": "x" * 2000,
                }
            },
        }

    async def _execute(self, **kwargs):
        return "ok"


def _image_data_url(payload: bytes, mime_type: str = "image/png") -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _media_data_url(payload: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_agent_process_persists_user_then_assistant_then_runs_maintenance(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(DummyTool())
        storage = FakeStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=FakeContextBuilder(tmp_path),
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        call_order = []
        release_maintenance = asyncio.Event()

        async def fake_call_llm(chat_id, current_message, channel=None, user_images=None, allow_tools=True, **kwargs):
            call_order.append(("call_llm", chat_id, current_message, channel, list(user_images or [])))
            assert storage.saved[0][1] == "user"
            return ExecutionResult(content="assistant reply", executed_tool_calls=0, used_configure_skill=False)

        async def fake_consolidate(chat_id):
            await release_maintenance.wait()
            call_order.append(("memory", chat_id))

        async def fake_update_profile(chat_id):
            await release_maintenance.wait()
            call_order.append(("profile", chat_id))

        async def fake_update_active_task(chat_id):
            await release_maintenance.wait()
            call_order.append(("active-task", chat_id))

        async def fake_update_recent_summary(chat_id):
            await release_maintenance.wait()
            call_order.append(("recent-summary", chat_id))

        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_consolidate
        agent._maybe_update_recent_summary = fake_update_recent_summary
        agent._maybe_update_user_profile = fake_update_profile
        agent._maybe_update_active_task = fake_update_active_task

        response = await agent.process(
            UserMessage(
                text="hello",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
                sender_id="user-1",
                sender_name="alice",
                images=["img1"],
                metadata={"source": "test"},
            )
        )

        assert call_order == [
            ("call_llm", "telegram:room-1", "hello", "telegram", ["img1"]),
        ]

        release_maintenance.set()
        await agent.wait_for_background_maintenance()

        return response, storage, call_order

    response, storage, call_order = asyncio.run(scenario())

    assert [entry[1] for entry in storage.saved] == ["user", "assistant"]
    assert storage.saved[0][3]["sender_name"] == "alice"
    assert storage.saved[0][3]["images_count"] == 1
    assert storage.saved[1][3] == {"channel": "telegram", "transport_chat_id": "room-1"}
    assert call_order[0] == ("call_llm", "telegram:room-1", "hello", "telegram", ["img1"])
    assert set(call_order[1:]) == {
        ("memory", "telegram:room-1"),
        ("recent-summary", "telegram:room-1"),
        ("profile", "telegram:room-1"),
        ("active-task", "telegram:room-1"),
    }
    assert response.text == "assistant reply"
    assert response.channel == "telegram"
    assert response.session_chat_id == "telegram:room-1"


def test_agent_process_emits_run_lifecycle_events(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=FakeContextBuilder(tmp_path / "workspace"),
            tools=ToolRegistry(),
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        async def fake_call_llm(*args, **kwargs):
            return ExecutionResult(
                content="assistant reply",
                executed_tool_calls=0,
                context_compactions=1,
                context_compaction_events=[
                    ContextCompactionEvent(
                        trigger="proactive",
                        strategy="deterministic",
                        outcome="compacted",
                        iteration=1,
                        messages_before=8,
                        messages_after=3,
                    )
                ],
            )

        async def fake_transition(*args, **kwargs):
            return None

        agent.call_llm = fake_call_llm
        agent._maybe_apply_immediate_task_transition = fake_transition
        agent._schedule_post_response_maintenance = lambda chat_id: None
        agent._maybe_schedule_skill_review = lambda chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="hello",
                channel="web",
                chat_id="browser-1",
                session_chat_id="web:browser-1",
                sender_id="user-1",
            )
        )

        run = next(iter(storage._runs.values()))
        events = next(iter(storage._run_events.values()))
        parts = await storage.get_run_parts("web:browser-1", run.run_id)
        return response, run, events, parts

    response, run, events, parts = asyncio.run(scenario())

    assert response.text == "assistant reply"
    assert run.status == "completed"
    assert run.chat_id == "web:browser-1"
    assert [event.event_type for event in events] == ["run_started", "llm_status", "run_finished"]
    assert events[0].payload["status"] == "running"
    assert events[-1].payload["status"] == "completed"
    assert [part.part_type for part in parts] == ["context_compaction", "assistant_message"]
    assert parts[0].content == "proactive:deterministic:compacted"
    assert parts[0].metadata["messages_before"] == 8
    assert parts[1].content == "assistant reply"
    assert parts[1].metadata["executed_tool_calls"] == 0
    assert parts[1].metadata["context_compactions"] == 1


def test_agent_verify_hooks_emit_verification_events(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=FakeContextBuilder(tmp_path / "workspace"),
            tools=ToolRegistry(),
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )
        bus = MessageBus()
        agent._message_bus = bus
        await storage.create_run("web:browser-1", "run-1")

        before = agent._make_tool_progress_hook(
            channel="web",
            transport_chat_id="browser-1",
            session_chat_id="web:browser-1",
            run_id="run-1",
            enabled=True,
        )
        after = agent._make_tool_result_hook(
            channel="web",
            transport_chat_id="browser-1",
            session_chat_id="web:browser-1",
            run_id="run-1",
            enabled=True,
        )

        await before("verify", {"action": "python_compile", "path": "src"})
        await after("verify", {"action": "python_compile", "path": "src"}, "Verification passed: python_compile")

        stored_events = await storage.get_run_events("web:browser-1", "run-1")
        stored_parts = await storage.get_run_parts("web:browser-1", "run-1")
        bus_events = []
        while bus.run_events_size:
            bus_events.append(await bus.consume_run_event())
        return stored_events, stored_parts, bus_events

    stored_events, stored_parts, bus_events = asyncio.run(scenario())

    assert [event.event_type for event in stored_events] == [
        "tool_started",
        "verification_started",
        "tool_result",
        "verification_result",
    ]
    assert [event.event_type for event in bus_events] == [event.event_type for event in stored_events]
    assert stored_events[1].payload == {"action": "python_compile", "path": "src"}
    assert stored_events[-1].payload["ok"] is True
    assert [part.part_type for part in stored_parts] == ["tool_call", "tool_result"]
    assert [part.tool_name for part in stored_parts] == ["verify", "verify"]
    assert stored_parts[0].metadata["args"] == {"action": "python_compile", "path": "src"}
    assert stored_parts[1].metadata["ok"] is True
    assert stored_parts[1].content == "Verification passed: python_compile"


def test_agent_default_filesystem_tools_record_run_file_changes(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=FakeContextBuilder(tmp_path / "workspace"),
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )
        await storage.create_run("web:browser-1", "run-1")

        chat_token = agent._current_chat_id.set("web:browser-1")
        channel_token = agent._current_channel.set("web")
        transport_token = agent._current_transport_chat_id.set("browser-1")
        run_token = agent._current_run_id.set("run-1")
        try:
            result = await agent.tools.execute(
                "write_file",
                {"path": "notes.txt", "content": "hello\n"},
            )
        finally:
            agent._current_run_id.reset(run_token)
            agent._current_transport_chat_id.reset(transport_token)
            agent._current_channel.reset(channel_token)
            agent._current_chat_id.reset(chat_token)

        changes = await storage.get_run_file_changes("web:browser-1", "run-1")
        events = await storage.get_run_events("web:browser-1", "run-1")
        return result, changes, events

    result, changes, events = asyncio.run(scenario())

    assert "Successfully wrote to notes.txt" in result
    assert len(changes) == 1
    assert changes[0].tool_name == "write_file"
    assert changes[0].path == "notes.txt"
    assert changes[0].action == "add"
    assert changes[0].before_sha256 is None
    assert changes[0].after_sha256 == _sha256("hello\n")
    assert changes[0].before_content is None
    assert changes[0].after_content == "hello\n"
    assert "+++ b/notes.txt" in changes[0].diff
    assert changes[0].metadata["diff_len"] == len(changes[0].diff)
    assert changes[0].metadata["after_content_available"] is True
    assert [event.event_type for event in events] == ["file_changed"]
    assert events[0].payload["path"] == "notes.txt"


def test_agent_tool_permission_requests_emit_run_events(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        registry = ToolRegistry(
            permission_policy=ToolPermissionPolicy(approval_mode="ask", approval_required_tools=["dummy"])
        )
        registry.register(DummyTool())
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=FakeContextBuilder(tmp_path / "workspace"),
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(**{"permissions": {"approval_timeout_seconds": 1}}),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )
        bus = MessageBus()
        agent._message_bus = bus
        await storage.create_run("web:browser-1", "run-1")

        chat_token = agent._current_chat_id.set("web:browser-1")
        channel_token = agent._current_channel.set("web")
        transport_token = agent._current_transport_chat_id.set("browser-1")
        run_token = agent._current_run_id.set("run-1")
        try:
            task = asyncio.create_task(agent.tools.execute("dummy", {}))
            for _ in range(100):
                pending = agent.pending_permission_requests()
                if pending:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("permission request was not created")

            request = pending[0]
            assert request.tool_name == "dummy"
            assert not task.done()

            await agent.approve_permission_request(request.request_id)
            result = await task
        finally:
            agent._current_run_id.reset(run_token)
            agent._current_transport_chat_id.reset(transport_token)
            agent._current_channel.reset(channel_token)
            agent._current_chat_id.reset(chat_token)

        stored_events = await storage.get_run_events("web:browser-1", "run-1")
        bus_events = []
        while bus.run_events_size:
            bus_events.append(await bus.consume_run_event())
        return result, stored_events, bus_events

    result, stored_events, bus_events = asyncio.run(scenario())

    assert result == "ok"
    assert [event.event_type for event in stored_events] == [
        "permission_requested",
        "permission_granted",
    ]
    assert [event.event_type for event in bus_events] == [event.event_type for event in stored_events]
    assert stored_events[0].payload["tool_name"] == "dummy"
    assert stored_events[0].payload["status"] == "pending"
    assert stored_events[1].payload["status"] == "approved"
    assert stored_events[1].payload["resolution_reason"] == "approved once"


def test_agent_process_persists_media_only_message_without_llm(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(DummyTool())
        storage = FakeStorage()
        context_builder = FakeContextBuilder(tmp_path / "workspace")
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=context_builder,
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        async def fail_call_llm(*args, **kwargs):
            raise AssertionError("media-only messages should not call the LLM")

        async def fake_maintenance(chat_id):
            return None

        agent.call_llm = fail_call_llm
        agent._maybe_consolidate_memory = fake_maintenance
        agent._maybe_update_recent_summary = fake_maintenance
        agent._maybe_update_user_profile = fake_maintenance
        agent._maybe_update_active_task = fake_maintenance

        response = await agent.process(
            UserMessage(
                text="",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
                images=[_image_data_url(b"image-bytes")],
                audios=[_media_data_url(b"audio-bytes", "audio/ogg")],
                videos=[_media_data_url(b"video-bytes", "video/mp4")],
            )
        )
        await agent.wait_for_background_maintenance()
        return response, storage, context_builder.workspace

    response, storage, workspace_root = asyncio.run(scenario())

    user_metadata = storage.saved[0][3]
    image_files = user_metadata["image_files"]
    audio_files = user_metadata["audio_files"]
    video_files = user_metadata["video_files"]
    saved_image = workspace_root / "chats" / "telegram" / "room-1" / image_files[0]
    saved_audio = workspace_root / "chats" / "telegram" / "room-1" / audio_files[0]
    saved_video = workspace_root / "chats" / "telegram" / "room-1" / video_files[0]

    assert response.text == "已收到並保存媒體檔案。需要我分析內容時，請直接告訴我要看哪一個檔案。"
    assert user_metadata["images_dir"] == "images"
    assert user_metadata["audios_dir"] == "audios"
    assert user_metadata["videos_dir"] == "videos"
    assert image_files[0].startswith("images/inbound-")
    assert image_files[0].endswith(".png")
    assert audio_files[0].startswith("audios/inbound-")
    assert audio_files[0].endswith(".ogg")
    assert video_files[0].startswith("videos/inbound-")
    assert video_files[0].endswith(".mp4")
    assert saved_image.read_bytes() == b"image-bytes"
    assert saved_audio.read_bytes() == b"audio-bytes"
    assert saved_video.read_bytes() == b"video-bytes"
    assert [entry[1] for entry in storage.saved] == ["user", "assistant"]
    assert storage.saved[0][2].startswith("[Media-only message saved to workspace]")
    assert f"Images: {image_files[0]}" in storage.saved[0][2]
    assert f"Audios: {audio_files[0]}" in storage.saved[0][2]
    assert f"Videos: {video_files[0]}" in storage.saved[0][2]


def test_agent_process_passes_saved_media_paths_when_text_requests_analysis(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(DummyTool())
        storage = FakeStorage()
        context_builder = FakeContextBuilder(tmp_path / "workspace")
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=context_builder,
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        captured = {}

        async def fake_call_llm(
            chat_id,
            current_message,
            channel=None,
            user_images=None,
            user_image_files=None,
            user_audio_files=None,
            user_video_files=None,
            allow_tools=True,
            **kwargs,
        ):
            captured["current_message"] = current_message
            captured["user_image_files"] = list(user_image_files or [])
            captured["user_audio_files"] = list(user_audio_files or [])
            captured["user_video_files"] = list(user_video_files or [])
            return ExecutionResult(content="analysis reply", executed_tool_calls=0, used_configure_skill=False)

        async def fake_maintenance(chat_id):
            return None

        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_maintenance
        agent._maybe_update_recent_summary = fake_maintenance
        agent._maybe_update_user_profile = fake_maintenance
        agent._maybe_update_active_task = fake_maintenance

        response = await agent.process(
            UserMessage(
                text="請幫我分析這些檔案",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
                images=[_image_data_url(b"image-bytes")],
                audios=[_media_data_url(b"audio-bytes", "audio/ogg")],
                videos=[_media_data_url(b"video-bytes", "video/mp4")],
            )
        )
        await agent.wait_for_background_maintenance()
        return response, captured

    response, captured = asyncio.run(scenario())

    assert response.text == "analysis reply"
    assert captured["current_message"] == "請幫我分析這些檔案"
    assert captured["user_image_files"][0].startswith("images/inbound-")
    assert captured["user_audio_files"][0].startswith("audios/inbound-")
    assert captured["user_video_files"][0].startswith("videos/inbound-")


def test_agent_process_returns_queued_outbound_media(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(DummyTool())
        storage = FakeStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=FakeContextBuilder(tmp_path),
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        async def fake_call_llm(chat_id, current_message, channel=None, user_images=None, allow_tools=True, **kwargs):
            assert agent._queue_outbound_media("image", "img-out") is None
            assert agent._queue_outbound_media("voice", "voice-out") is None
            assert agent._queue_outbound_media("audio", "audio-out") is None
            assert agent._queue_outbound_media("video", "video-out") is None
            return ExecutionResult(content="sending media", executed_tool_calls=1, used_configure_skill=False)

        async def fake_maintenance(chat_id):
            return None

        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_maintenance
        agent._maybe_update_recent_summary = fake_maintenance
        agent._maybe_update_user_profile = fake_maintenance
        agent._maybe_update_active_task = fake_maintenance

        response = await agent.process(
            UserMessage(
                text="send it",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
            )
        )
        await agent.wait_for_background_maintenance()
        return response, storage

    response, storage = asyncio.run(scenario())

    assert response.text == "sending media"
    assert response.images == ["img-out"]
    assert response.voices == ["voice-out"]
    assert response.audios == ["audio-out"]
    assert response.videos == ["video-out"]
    assert [entry[1] for entry in storage.saved] == ["user", "assistant"]


def test_mark_active_task_status_updates_processed_index_for_terminal_states(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(DummyTool())
        storage = HistoryStorage(
            [
                StoredMessage(role="user", content="first", timestamp=1.0),
                StoredMessage(role="assistant", content="second", timestamp=2.0),
                StoredMessage(role="user", content="third", timestamp=3.0),
            ]
        )
        context_builder = FakeContextBuilder(tmp_path)
        context_builder.app_home = tmp_path / "home"
        context_builder.tool_workspace = tmp_path / "workspace"

        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=context_builder,
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        store = create_active_task_store(agent.app_home, "telegram:room-1", workspace_root=agent.tool_workspace)
        store.write_managed_block(
            "- Status: active\n"
            "- Goal: Keep going\n"
            "- Deliverable: output\n"
            "- Definition of done:\n"
            "  - done\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: 1. inspect\n"
            "- Next step: 2. verify\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        )

        rendered = await agent.mark_active_task_status("telegram:room-1", "done")
        return rendered, store.get_processed_index("telegram:room-1")

    rendered, processed_index = asyncio.run(scenario())

    assert rendered is not None
    assert "- Status: done" in rendered
    assert processed_index == 3


def test_process_moves_active_task_to_waiting_user_when_reply_requests_missing_info(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(DummyTool())
        storage = FakeStorage()
        context_builder = FakeContextBuilder(tmp_path)
        context_builder.app_home = tmp_path / "home"
        context_builder.tool_workspace = tmp_path / "workspace"

        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=context_builder,
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        store = create_active_task_store(agent.app_home, "telegram:room-1", workspace_root=agent.tool_workspace)
        store.write_managed_block(
            "- Status: active\n"
            "- Goal: Finish the refactor\n"
            "- Deliverable: merged refactor\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: 2. apply the fix\n"
            "- Next step: 3. verify\n"
            "- Completed steps:\n"
            "  - inspect\n"
            "- Open questions:\n"
            "  - none"
        )

        async def fake_call_llm(*args, **kwargs):
            return ExecutionResult(content="請問你要用哪個 target branch？", executed_tool_calls=0)

        agent.call_llm = fake_call_llm
        await agent.process(
            UserMessage(
                text="繼續做",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
            )
        )
        return store.read_managed_block()

    task_block = asyncio.run(scenario())

    assert "- Status: waiting_user" in task_block
    assert "請問你要用哪個 target branch？" in task_block


def test_process_moves_active_task_to_blocked_when_reply_reports_blocking_error(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(DummyTool())
        storage = FakeStorage()
        context_builder = FakeContextBuilder(tmp_path)
        context_builder.app_home = tmp_path / "home"
        context_builder.tool_workspace = tmp_path / "workspace"

        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=FakeProvider(),
            storage=storage,
            context_builder=context_builder,
            tools=registry,
            memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        store = create_active_task_store(agent.app_home, "telegram:room-1", workspace_root=agent.tool_workspace)
        store.write_managed_block(
            "- Status: active\n"
            "- Goal: Finish the refactor\n"
            "- Deliverable: merged refactor\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: 3. verify\n"
            "- Next step: not set\n"
            "- Completed steps:\n"
            "  - inspect\n"
            "  - apply fix\n"
            "- Open questions:\n"
            "  - none"
        )

        async def fake_call_llm(*args, **kwargs):
            return ExecutionResult(content="目前無法繼續，測試環境失敗。", executed_tool_calls=1, had_tool_error=True)

        agent.call_llm = fake_call_llm
        await agent.process(
            UserMessage(
                text="繼續驗證",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
            )
        )
        return store.read_managed_block()

    task_block = asyncio.run(scenario())

    assert "- Status: blocked" in task_block
    assert "目前無法繼續，測試環境失敗。" in task_block


def test_background_session_exit_notifier_publishes_outbound_and_persists_message(tmp_path):
    registry = ToolRegistry()
    registry.register(DummyTool())
    storage = FakeStorage()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=FakeProvider(),
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    fake_bus = FakeBus()
    agent._message_bus = fake_bus

    class _FakeProcess:
        pid = 4321

    chat_token = agent._current_chat_id.set("telegram:room-1")
    channel_token = agent._current_channel.set("telegram")
    transport_token = agent._current_transport_chat_id.set("room-1")
    try:
        notifier = agent._make_background_session_exit_notifier()
        assert notifier is not None

        session = BackgroundSession(
            session_id="bg123",
            command="python job.py",
            cwd=str(tmp_path),
            process=_FakeProcess(),
            read_tasks=[],
            output_chunks=[CapturedOutputChunk("stdout", b"job done\n")],
            timeout_seconds=5,
            drain_timeout=5,
            state="exited",
            termination_reason="exit",
            exit_code=0,
            started_at=10.0,
            started_at_wall=100.0,
            finished_at=12.5,
            finished_at_wall=102.5,
        )

        asyncio.run(notifier(session))
    finally:
        agent._current_transport_chat_id.reset(transport_token)
        agent._current_channel.reset(channel_token)
        agent._current_chat_id.reset(chat_token)

    assert len(fake_bus.outbound) == 1
    outbound = fake_bus.outbound[0]
    assert outbound.channel == "telegram"
    assert outbound.chat_id == "room-1"
    assert outbound.session_chat_id == "telegram:room-1"
    assert "Background session finished." in outbound.content
    assert "Session ID: bg123" in outbound.content
    assert "job done" in outbound.content
    assert storage.saved[-1][1] == "assistant"
    assert storage.saved[-1][2] == outbound.content
    assert storage.saved[-1][3]["kind"] == "background_session_exit"


def test_call_llm_trims_old_history_to_token_budget(tmp_path):
    context_builder = FakeContextBuilder(tmp_path)
    storage = HistoryStorage(
        [
            StoredMessage(role="user", content="old message " * 40, timestamp=1.0),
            StoredMessage(role="assistant", content="recent message", timestamp=2.0),
        ]
    )
    agent = AgentLoop(
        config=Config.load_agent_template_config(history_token_budget=120),
        provider=FakeProvider(),
        storage=storage,
        context_builder=context_builder,
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    captured = {}

    async def fake_execute_messages(
        log_id,
        chat_messages,
        *,
        allow_tools,
        tool_result_chat_id=None,
        tool_registry=None,
        on_tool_before_execute=None,
        on_tool_after_execute=None,
        on_llm_status=None,
        refresh_system_prompt=None,
        max_tool_iterations=None,
    ):
        captured["messages"] = list(chat_messages)
        return ExecutionResult(content="ok", executed_tool_calls=0, used_configure_skill=False)

    agent._execute_messages = fake_execute_messages

    result = asyncio.run(agent.call_llm("telegram:room-1", "current input", channel="telegram", allow_tools=False))

    assert result.content == "ok"
    assert context_builder.last_history == [{"role": "assistant", "content": "recent message"}]
    assert [message.role for message in captured["messages"]] == ["user"]


def test_load_history_uses_agent_max_history(tmp_path):
    storage = HistoryStorage(
        [
            StoredMessage(role="user", content="first", timestamp=1.0),
            StoredMessage(role="assistant", content="second", timestamp=2.0),
            StoredMessage(role="user", content="third", timestamp=3.0),
        ]
    )
    agent = AgentLoop(
        config=Config.load_agent_template_config(max_history=2),
        provider=FakeProvider(),
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    history = asyncio.run(agent._load_history("telegram:room-1"))

    assert [message.content for message in history] == ["second", "third"]


def test_trim_history_reports_base_tokens_without_history(tmp_path):
    agent = AgentLoop(
        config=Config.load_agent_template_config(history_token_budget=500),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    history, base_tokens, history_tokens, final_tokens = agent._trim_history_to_token_budget(
        history=[],
        current_message="hello",
        channel="telegram",
        chat_id="telegram:room-1",
    )

    assert history == []
    assert base_tokens > 0
    assert history_tokens == 0
    assert final_tokens == base_tokens


def test_effective_context_budget_uses_model_window_and_manual_cap(tmp_path):
    chat_kwargs = Config.packaged_agent_llm_chat_kwargs()
    chat_kwargs["llm_chat_max_tokens"] = 200
    agent = AgentLoop(
        config=Config.load_agent_template_config(history_token_budget=1000),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
        llm_context_window_tokens=500,
        **chat_kwargs,
    )

    assert agent._effective_context_token_budget() == 300
    assert agent.execution_engine.context_compaction_token_budget == 300

    agent.config.history_token_budget = 150
    assert agent._effective_context_token_budget() == 150


def test_tool_schema_tokens_reduce_history_budget(tmp_path):
    storage = HistoryStorage([StoredMessage(role="assistant", content="recent message", timestamp=1.0)])
    registry = ToolRegistry()
    registry.register(LargeSchemaTool())
    agent = AgentLoop(
        config=Config.load_agent_template_config(history_token_budget=150),
        provider=FakeProvider(),
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    tool_tokens = agent._estimate_tool_schema_tokens(allow_tools=True)
    assert tool_tokens > 0

    kept_without_tools, _, _, _ = agent._trim_history_to_token_budget(
        history=[{"role": "assistant", "content": "recent message"}],
        current_message="hello",
        channel="telegram",
        chat_id="telegram:room-1",
        tool_schema_tokens=0,
    )
    kept_with_tools, _, _, _ = agent._trim_history_to_token_budget(
        history=[{"role": "assistant", "content": "recent message"}],
        current_message="hello",
        channel="telegram",
        chat_id="telegram:room-1",
        tool_schema_tokens=tool_tokens,
    )

    assert kept_without_tools == [{"role": "assistant", "content": "recent message"}]
    assert kept_with_tools == []


def test_agent_process_returns_setup_hint_when_llm_not_configured(tmp_path):
    storage = FakeStorage()
    messages = MessagesConfig(**{"agent": {"llm_not_configured": "請先設定 LLM"}})
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=FakeProvider(),
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
        llm_configured=False,
        messages_config=messages,
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    async def fail_call_llm(*args, **kwargs):
        raise AssertionError("call_llm should not run when llm is not configured")

    agent.call_llm = fail_call_llm

    response = asyncio.run(
        agent.process(
            UserMessage(
                text="hello",
                channel="telegram",
                chat_id="room-1",
                session_chat_id="telegram:room-1",
                sender_id="user-1",
                sender_name="alice",
            )
        )
    )

    assert response.text == "請先設定 LLM"
    assert [entry[1] for entry in storage.saved] == ["user", "assistant"]
    assert storage.saved[1][2] == "請先設定 LLM"
