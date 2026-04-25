from pathlib import Path

import opensprite.agent.agent as agent_module
from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, Config, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"

    def build_system_prompt(self, chat_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, chat_id=None):
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


def test_system_prompt_logging_writes_one_file_per_prompt(tmp_path):
    registry = ToolRegistry()
    registry.register(DummyTool())
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent.app_home = tmp_path / "home"

    agent._write_full_system_prompt_log("telegram:user-a", "first prompt")
    agent._write_full_system_prompt_log("telegram:user-a", "second prompt")

    dated_dirs = list((agent.app_home / "logs" / "system-prompts").iterdir())
    assert len(dated_dirs) == 1

    log_files = sorted(dated_dirs[0].glob("*.md"))
    assert len(log_files) == 2
    assert all("telegram-user-a" in file.name for file in log_files)
    assert log_files[0].read_text(encoding="utf-8") != log_files[1].read_text(encoding="utf-8")


def test_subagent_system_prompt_logging_uses_separate_directory(tmp_path):
    registry = ToolRegistry()
    registry.register(DummyTool())
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent.app_home = tmp_path / "home"

    agent._write_full_system_prompt_log("telegram:user-a:subagent:implementer", "subagent prompt")

    subagent_root = agent.app_home / "logs" / "system-prompts" / "subagents"
    dated_dirs = list(subagent_root.iterdir())
    assert len(dated_dirs) == 1
    log_files = list(dated_dirs[0].glob("*.md"))
    assert len(log_files) == 1
    assert "subagent" in log_files[0].name


def test_main_prompt_logging_includes_available_subagent_summary(tmp_path, monkeypatch):
    registry = ToolRegistry()
    registry.register(DummyTool())
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent.app_home = tmp_path / "home"
    info_messages: list[str] = []

    monkeypatch.setattr(agent_module.logger, "info", lambda message: info_messages.append(message))

    agent._log_prepared_messages(
        "telegram:user-a",
        [
            {
                "role": "system",
                "content": "# Available Subagents\n\n- `writer`: drafting helper\n- `researcher`: research helper\n",
            }
        ],
    )

    assert any(
        "[telegram:user-a] prompt.subagents | count=2 names=writer, researcher" in message
        for message in info_messages
    )
