from opensprite.agent.skill_review import build_skill_review_user_content, format_stored_messages_for_transcript
import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.skill_review import build_skill_review_user_content, format_stored_messages_for_transcript
from opensprite.config.schema import AgentConfig, Config, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.storage.base import StoredMessage
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class _FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"

    def build_system_prompt(self, chat_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message}]


class _FakeProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        raise AssertionError("provider.chat should not be called in this test")

    def get_default_model(self) -> str:
        return "fake-model"


class _FakeStorage:
    async def get_messages(self, chat_id, limit=None):
        return []

    async def add_message(self, chat_id, message):
        return None

    async def clear_messages(self, chat_id):
        return None

    async def get_consolidated_index(self, chat_id):
        return 0

    async def set_consolidated_index(self, chat_id, index):
        return None

    async def get_all_chats(self):
        return []


class _DummyTool(Tool):
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


def _make_agent(tmp_path: Path) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(_DummyTool())
    return AgentLoop(
        config=AgentConfig(),
        provider=_FakeProvider(),
        storage=_FakeStorage(),
        context_builder=_FakeContextBuilder(tmp_path),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )


def test_format_stored_messages_for_transcript_includes_tool_name():
    rows = [
        StoredMessage(role="user", content="hi", timestamp=1.0),
        StoredMessage(role="assistant", content="hello", timestamp=2.0),
        StoredMessage(role="tool", content="output", timestamp=3.0, tool_name="read_file"),
    ]
    text = format_stored_messages_for_transcript(rows)
    assert "USER" in text
    assert "ASSISTANT" in text
    assert "[tool:read_file]" in text
    assert "output" in text


def test_build_skill_review_user_content_wraps_transcript():
    body = build_skill_review_user_content("LINE1")
    assert "--- TRANSCRIPT ---" in body
    assert "LINE1" in body
    assert "Nothing to save" in body


def test_skill_review_scheduler_coalesces_same_chat_into_rerun(tmp_path):
    async def scenario():
        agent = _make_agent(tmp_path)
        agent._skill_review_tool_registry = lambda: object()

        release = asyncio.Event()
        started = asyncio.Event()
        calls: list[str] = []

        async def fake_run(chat_id: str) -> None:
            calls.append(chat_id)
            started.set()
            if len(calls) == 1:
                await release.wait()

        agent._run_skill_review = fake_run
        result = ExecutionResult(content="done", executed_tool_calls=agent.config.skill_review_min_tool_calls)

        agent._maybe_schedule_skill_review("chat-a", result)
        await started.wait()
        agent._maybe_schedule_skill_review("chat-a", result)

        release.set()
        await agent.wait_for_background_skill_reviews()
        return calls

    calls = asyncio.run(scenario())

    assert calls == ["chat-a", "chat-a"]


def test_skill_review_scheduler_keeps_different_chats_separate(tmp_path):
    async def scenario():
        agent = _make_agent(tmp_path)
        agent._skill_review_tool_registry = lambda: object()

        release = asyncio.Event()
        started = set()
        calls: list[str] = []

        async def fake_run(chat_id: str) -> None:
            calls.append(chat_id)
            started.add(chat_id)
            if len(started) < 2:
                await asyncio.sleep(0)
            await release.wait()

        agent._run_skill_review = fake_run
        result = ExecutionResult(content="done", executed_tool_calls=agent.config.skill_review_min_tool_calls)

        agent._maybe_schedule_skill_review("chat-a", result)
        agent._maybe_schedule_skill_review("chat-b", result)
        await asyncio.sleep(0)
        assert sorted(agent._skill_review_tasks) == ["chat-a", "chat-b"]

        release.set()
        await agent.wait_for_background_skill_reviews()
        return calls

    calls = asyncio.run(scenario())

    assert sorted(calls) == ["chat-a", "chat-b"]
