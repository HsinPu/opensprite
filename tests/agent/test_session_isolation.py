import asyncio

from opensprite.agent.agent import AgentLoop
from opensprite.bus.message import UserMessage
from opensprite.config.schema import AgentConfig, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.context.file_builder import FileContextBuilder
from opensprite.context.paths import get_chat_workspace
from opensprite.llms.base import LLMResponse
from opensprite.storage.sqlite import SQLiteStorage
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


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


class RecordingProvider:
    def __init__(self):
        self.system_prompts = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.system_prompts.append(str(messages[0].content))
        return LLMResponse(content=f"reply-{len(self.system_prompts)}", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


def test_agent_process_keeps_workspace_and_sqlite_history_isolated_per_session(tmp_path):
    async def scenario():
        workspace_root = tmp_path / "workspace"
        provider = RecordingProvider()
        storage = SQLiteStorage(db_path=tmp_path / "sessions.db")
        builder = FileContextBuilder(
            app_home=tmp_path / "home",
            bootstrap_dir=tmp_path / "bootstrap",
            memory_dir=tmp_path / "memory",
            tool_workspace=workspace_root,
            default_skills_dir=tmp_path / "skills",
        )
        registry = ToolRegistry()
        registry.register(DummyTool())
        agent = AgentLoop(
            config=AgentConfig(),
            provider=provider,
            storage=storage,
            context_builder=builder,
            tools=registry,
            memory_config=MemoryConfig(),
            tools_config=ToolsConfig(),
            log_config=LogConfig(),
            search_config=SearchConfig(),
            user_profile_config=UserProfileConfig(enabled=False),
        )

        response_a = await agent.process(
            UserMessage(
                text="hello from A",
                channel="telegram",
                chat_id="user-a",
                session_chat_id="telegram:user-a",
            )
        )
        response_b = await agent.process(
            UserMessage(
                text="hello from B",
                channel="telegram",
                chat_id="user-b",
                session_chat_id="telegram:user-b",
            )
        )

        messages_a = await storage.get_messages("telegram:user-a")
        messages_b = await storage.get_messages("telegram:user-b")
        all_chats = sorted(await storage.get_all_chats())

        return {
            "prompts": list(provider.system_prompts),
            "messages_a": messages_a,
            "messages_b": messages_b,
            "all_chats": all_chats,
            "workspace_a": get_chat_workspace("telegram:user-a", workspace_root=workspace_root),
            "workspace_b": get_chat_workspace("telegram:user-b", workspace_root=workspace_root),
            "response_a": response_a,
            "response_b": response_b,
        }

    result = asyncio.run(scenario())

    assert str(result["workspace_a"].resolve()) in result["prompts"][0]
    assert str(result["workspace_b"].resolve()) in result["prompts"][1]
    assert result["workspace_a"] != result["workspace_b"]
    assert [message.content for message in result["messages_a"]] == ["hello from A", "reply-1"]
    assert [message.content for message in result["messages_b"]] == ["hello from B", "reply-2"]
    assert result["all_chats"] == ["telegram:user-a", "telegram:user-b"]
    assert result["response_a"].session_chat_id == "telegram:user-a"
    assert result["response_b"].session_chat_id == "telegram:user-b"
