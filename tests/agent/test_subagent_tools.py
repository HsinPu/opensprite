import asyncio
from collections import defaultdict
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, Config, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.context.paths import get_chat_workspace
from opensprite.llms.base import LLMResponse, ToolCall
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


class FakeStorage:
    def __init__(self):
        self.saved = []
        self.messages = defaultdict(list)

    async def get_messages(self, chat_id, limit=None):
        messages = self.messages.get(chat_id, [])
        if limit:
            return messages[-limit:]
        return list(messages)

    async def add_message(self, chat_id, message):
        self.messages[chat_id].append(message)
        self.saved.append((chat_id, message.role, message.content, message.tool_name, message.metadata))

    async def clear_messages(self, chat_id):
        return None

    async def get_consolidated_index(self, chat_id):
        return 0

    async def set_consolidated_index(self, chat_id, index):
        return None

    async def get_all_chats(self):
        return []


class DummyTool(Tool):
    def __init__(self, name: str = "demo_tool"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    async def _execute(self, value: str = "", **kwargs):
        return f"tool:{value}"


class FakeProvider:
    def __init__(self, tool_name: str = "read_file", tool_arguments: dict | None = None):
        self.calls = []
        self.tool_name = tool_name
        self.tool_arguments = tool_arguments or {"value": "abc"}

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        if len(self.calls) == 1:
            return LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name=self.tool_name, arguments=self.tool_arguments)],
            )
        return LLMResponse(content="done", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


def test_implementer_subagent_can_use_profile_tools_but_not_delegate(tmp_path):
    provider = FakeProvider()
    storage = FakeStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("read_file"))
    registry.register(DummyTool("apply_patch"))
    registry.register(DummyTool("exec"))
    registry.register(DummyTool("process"))
    registry.register(DummyTool("delegate"))
    registry.register(DummyTool("cron"))
    registry.register(DummyTool("configure_mcp"))
    registry.register(DummyTool("configure_skill"))
    registry.register(DummyTool("configure_subagent"))
    registry.register(DummyTool("task_update"))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("do the task", prompt_type="implementer"))

    assert "Task ID: task_" in result
    assert "Subagent: implementer" in result
    assert "Result:\ndone" in result
    assert provider.calls[0]["tools"] is not None
    tool_names = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
    assert "read_file" in tool_names
    assert "apply_patch" in tool_names
    assert "exec" in tool_names
    assert "process" in tool_names
    assert "delegate" not in tool_names
    assert "cron" not in tool_names
    assert "configure_mcp" not in tool_names
    assert "configure_skill" not in tool_names
    assert "configure_subagent" not in tool_names
    assert "task_update" not in tool_names
    child_chat_id = next(chat_id for chat_id in storage.messages if ":subagent:task_" in chat_id)
    assert child_chat_id.startswith("telegram:user-a:subagent:task_")
    assert storage.saved[0][0:4] == (child_chat_id, "user", "do the task", None)
    assert storage.saved[1][0:4] == (child_chat_id, "tool", "tool:abc", "read_file")
    assert storage.saved[2][0:4] == (child_chat_id, "assistant", "done", None)
    assert storage.saved[0][4]["kind"] == "subagent_task"
    assert storage.saved[0][4]["parent_chat_id"] == "telegram:user-a"


def test_code_reviewer_subagent_is_read_only(tmp_path):
    provider = ResumeProvider()
    storage = FakeStorage()
    registry = ToolRegistry()
    for name in ["read_file", "grep_files", "batch", "apply_patch", "write_file", "exec", "web_search"]:
        registry.register(DummyTool(name))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("review the task", prompt_type="code-reviewer"))

    assert "Subagent: code-reviewer" in result
    tool_names = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
    assert "read_file" in tool_names
    assert "grep_files" in tool_names
    assert "batch" in tool_names
    assert "apply_patch" not in tool_names
    assert "write_file" not in tool_names
    assert "exec" not in tool_names
    assert "web_search" not in tool_names


def test_custom_subagent_tool_profile_controls_runtime_tools(tmp_path):
    workspace = tmp_path / "workspace"
    chat_workspace = get_chat_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = chat_workspace / "subagent_prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "custom-implementer.md").write_text(
        "---\n"
        "name: custom-implementer\n"
        "description: Custom implementation helper for this chat workspace.\n"
        "tool_profile: implementation\n"
        "---\n"
        "Implement the requested change.\n",
        encoding="utf-8",
    )
    provider = ResumeProvider()
    storage = FakeStorage()
    registry = ToolRegistry()
    for name in ["read_file", "apply_patch", "exec", "process", "delegate", "web_search"]:
        registry.register(DummyTool(name))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(workspace),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("do the task", prompt_type="custom-implementer"))

    assert "Subagent: custom-implementer" in result
    tool_names = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
    assert "read_file" in tool_names
    assert "apply_patch" in tool_names
    assert "exec" in tool_names
    assert "process" in tool_names
    assert "delegate" not in tool_names
    assert "web_search" not in tool_names


def test_custom_subagent_without_tool_profile_defaults_read_only(tmp_path):
    workspace = tmp_path / "workspace"
    chat_workspace = get_chat_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = chat_workspace / "subagent_prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "custom-agent.md").write_text(
        "---\n"
        "name: custom-agent\n"
        "description: Custom helper without an explicit tool profile.\n"
        "---\n"
        "Inspect the requested context.\n",
        encoding="utf-8",
    )
    provider = ResumeProvider()
    storage = FakeStorage()
    registry = ToolRegistry()
    for name in ["read_file", "apply_patch", "exec", "web_search"]:
        registry.register(DummyTool(name))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(workspace),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("inspect", prompt_type="custom-agent"))

    assert "Subagent: custom-agent" in result
    tool_names = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
    assert "read_file" in tool_names
    assert "apply_patch" not in tool_names
    assert "exec" not in tool_names
    assert "web_search" not in tool_names


def test_invalid_subagent_tool_profile_blocks_delegation(tmp_path):
    workspace = tmp_path / "workspace"
    chat_workspace = get_chat_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = chat_workspace / "subagent_prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "bad-agent.md").write_text(
        "---\n"
        "name: bad-agent\n"
        "description: Invalid runtime profile example.\n"
        "tool_profile: root\n"
        "---\n"
        "Do unsafe things.\n",
        encoding="utf-8",
    )
    provider = ResumeProvider()
    storage = FakeStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("read_file"))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(workspace),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("do it", prompt_type="bad-agent"))

    assert "invalid tool_profile 'root'" in result
    assert provider.calls == []
    assert storage.saved == []


def test_code_reviewer_forbidden_write_call_is_not_executed(tmp_path):
    provider = FakeProvider(
        tool_name="apply_patch",
        tool_arguments={"changes": [{"action": "add", "path": "notes.txt", "content": "x"}]},
    )
    storage = FakeStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("read_file"))
    registry.register(DummyTool("apply_patch"))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("review the task", prompt_type="code-reviewer"))

    assert "Result:\ndone" in result
    tool_results = [saved for saved in storage.saved if saved[1] == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0][3] == "apply_patch"
    assert "Tool 'apply_patch' not found" in tool_results[0][2]


def test_test_writer_write_tools_are_limited_to_test_paths(tmp_path):
    provider = FakeProvider(
        tool_name="apply_patch",
        tool_arguments={
            "changes": [
                {"action": "add", "path": "src/app.py", "content": "x"},
            ]
        },
    )
    storage = FakeStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("read_file"))
    registry.register(DummyTool("apply_patch"))
    registry.register(DummyTool("exec"))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("add tests", prompt_type="test-writer"))

    assert "Result:\ndone" in result
    tool_results = [saved for saved in storage.saved if saved[1] == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0][3] == "apply_patch"
    assert "blocked by permission policy" in tool_results[0][2]
    assert "outside allowed subagent write paths" in tool_results[0][2]


def test_test_writer_can_use_write_tools_for_test_paths(tmp_path):
    provider = FakeProvider(
        tool_name="apply_patch",
        tool_arguments={
            "changes": [
                {"action": "add", "path": "tests/test_app.py", "content": "x"},
            ]
        },
    )
    storage = FakeStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("read_file"))
    registry.register(DummyTool("apply_patch"))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("add tests", prompt_type="test-writer"))

    assert "Result:\ndone" in result
    tool_results = [saved for saved in storage.saved if saved[1] == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0][0:4] == (
        next(chat_id for chat_id in storage.messages if ":subagent:task_" in chat_id),
        "tool",
        "tool:",
        "apply_patch",
    )


def test_test_writer_allows_common_js_tests_directory(tmp_path):
    provider = FakeProvider(
        tool_name="apply_patch",
        tool_arguments={
            "changes": [
                {"action": "add", "path": "src/__tests__/app.ts", "content": "x"},
            ]
        },
    )
    storage = FakeStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("read_file"))
    registry.register(DummyTool("apply_patch"))
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("add tests", prompt_type="test-writer"))

    assert "Result:\ndone" in result
    tool_results = [saved for saved in storage.saved if saved[1] == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0][0:4] == (
        next(chat_id for chat_id in storage.messages if ":subagent:task_" in chat_id),
        "tool",
        "tool:",
        "apply_patch",
    )


class ResumeProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        return LLMResponse(content=f"reply-{len(self.calls)}", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


def test_subagent_resume_uses_child_session_history(tmp_path):
    provider = ResumeProvider()
    storage = FakeStorage()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    first = asyncio.run(agent.run_subagent("initial task", prompt_type="implementer"))
    task_id = next(line.split(": ", 1)[1] for line in first.splitlines() if line.startswith("Task ID:"))
    second = asyncio.run(agent.run_subagent("continue task", task_id=task_id))

    assert f"Task ID: {task_id}" in second
    second_messages = provider.calls[1]["messages"]
    assert [message.role for message in second_messages] == ["system", "user", "assistant", "user"]
    assert second_messages[1].content == "initial task"
    assert second_messages[2].content == "reply-1"
    assert second_messages[3].content == "continue task"
    child_chat_id = f"telegram:user-a:subagent:{task_id}"
    stored_roles = [message.role for message in storage.messages[child_chat_id]]
    assert stored_roles == ["user", "assistant", "user", "assistant"]


def test_subagent_resume_rejects_prompt_type_switch(tmp_path):
    provider = ResumeProvider()
    storage = FakeStorage()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=storage,
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_chat_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    first = asyncio.run(agent.run_subagent("initial task", prompt_type="implementer"))
    task_id = next(line.split(": ", 1)[1] for line in first.splitlines() if line.startswith("Task ID:"))
    result = asyncio.run(agent.run_subagent("continue task", prompt_type="writer", task_id=task_id))

    assert f"was created with prompt_type 'implementer'" in result
