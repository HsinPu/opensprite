import asyncio
from collections import defaultdict
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import AgentConfig, Config, LLMsConfig, LogConfig, MemoryConfig, ProviderConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.context.paths import get_session_workspace
from opensprite.llms.base import LLMResponse, ToolCall
from opensprite.runs.schema import serialize_run_artifacts
from opensprite.storage import MemoryStorage
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"

    def build_system_prompt(self, session_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, session_id=None):
        return [{"role": "user", "content": current_message}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return messages

    def add_assistant_message(self, messages, content, tool_calls=None):
        return messages


class FakeStorage:
    def __init__(self):
        self.saved = []
        self.messages = defaultdict(list)

    async def get_messages(self, session_id, limit=None):
        messages = self.messages.get(session_id, [])
        if limit:
            return messages[-limit:]
        return list(messages)

    async def add_message(self, session_id, message):
        self.messages[session_id].append(message)
        self.saved.append((session_id, message.role, message.content, message.tool_name, message.metadata))

    async def clear_messages(self, session_id):
        return None

    async def get_consolidated_index(self, session_id):
        return 0

    async def set_consolidated_index(self, session_id, index):
        return None

    async def get_all_sessions(self):
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
    registry.register(DummyTool("delegate_many"))
    registry.register(DummyTool("run_workflow"))
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
    agent._current_session_id.set("telegram:user-a")
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
    assert "delegate_many" not in tool_names
    assert "run_workflow" not in tool_names
    assert "cron" not in tool_names
    assert "configure_mcp" not in tool_names
    assert "configure_skill" not in tool_names
    assert "configure_subagent" not in tool_names
    assert "task_update" not in tool_names
    child_session_id = next(session_id for session_id in storage.messages if ":subagent:task_" in session_id)
    assert child_session_id.startswith("telegram:user-a:subagent:task_")
    assert storage.saved[0][0:4] == (child_session_id, "user", "do the task", None)
    assert storage.saved[1][0:4] == (child_session_id, "tool", "tool:abc", "read_file")
    assert storage.saved[2][0:4] == (child_session_id, "assistant", "done", None)
    assert storage.saved[0][4]["kind"] == "subagent_task"
    assert storage.saved[0][4]["parent_session_id"] == "telegram:user-a"


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
    agent._current_session_id.set("telegram:user-a")
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
    session_workspace = get_session_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = session_workspace / "subagent_prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "custom-implementer.md").write_text(
        "---\n"
        "name: custom-implementer\n"
        "description: Custom implementation helper for this session workspace.\n"
        "tool_profile: implementation\n"
        "---\n"
        "Implement the requested change.\n",
        encoding="utf-8",
    )
    provider = ResumeProvider()
    storage = FakeStorage()
    registry = ToolRegistry()
    for name in ["read_file", "apply_patch", "exec", "process", "delegate", "delegate_many", "run_workflow", "web_search"]:
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
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("do the task", prompt_type="custom-implementer"))

    assert "Subagent: custom-implementer" in result
    tool_names = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
    assert "read_file" in tool_names
    assert "apply_patch" in tool_names
    assert "exec" in tool_names
    assert "process" in tool_names
    assert "delegate" not in tool_names
    assert "delegate_many" not in tool_names
    assert "run_workflow" not in tool_names
    assert "web_search" not in tool_names


def test_custom_subagent_without_tool_profile_defaults_read_only(tmp_path):
    workspace = tmp_path / "workspace"
    session_workspace = get_session_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = session_workspace / "subagent_prompts"
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
    agent._current_session_id.set("telegram:user-a")
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
    session_workspace = get_session_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = session_workspace / "subagent_prompts"
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
    agent._current_session_id.set("telegram:user-a")
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
    agent._current_session_id.set("telegram:user-a")
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
    agent._current_session_id.set("telegram:user-a")
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
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("add tests", prompt_type="test-writer"))

    assert "Result:\ndone" in result
    tool_results = [saved for saved in storage.saved if saved[1] == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0][0:4] == (
        next(session_id for session_id in storage.messages if ":subagent:task_" in session_id),
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
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("add tests", prompt_type="test-writer"))

    assert "Result:\ndone" in result
    tool_results = [saved for saved in storage.saved if saved[1] == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0][0:4] == (
        next(session_id for session_id in storage.messages if ":subagent:task_" in session_id),
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


class ParallelOutcomeProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        task_text = next(
            str(message.content)
            for message in reversed(messages)
            if getattr(message, "role", None) == "user"
        )
        if "slow" in task_text:
            await asyncio.sleep(0.05)
        return LLMResponse(content=f"reply:{task_text}", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


class SlowParallelProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        await asyncio.sleep(0.2)
        return LLMResponse(content="slow-done", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


class PartiallyFailingParallelProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        task_text = next(
            str(message.content)
            for message in reversed(messages)
            if getattr(message, "role", None) == "user"
        )
        if "broken" in task_text:
            raise RuntimeError("child task failed")
        return LLMResponse(content=f"ok:{task_text}", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


class StructuredReviewProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        return LLMResponse(
            content=(
                "Review Findings\n"
                "1. high src/foo.py: Null handling bug\n"
                "   Why: Empty input can raise an exception.\n"
                "   Fix: Guard the null path before dereference.\n\n"
                "```json\n"
                "{\n"
                '  "schema_version": 1,\n'
                '  "contract": "readonly_subagent_result",\n'
                '  "prompt_type": "code-reviewer",\n'
                '  "status": "ok",\n'
                '  "summary": "One high-risk bug found.",\n'
                '  "sections": [\n'
                '    {\n'
                '      "key": "findings",\n'
                '      "title": "Review Findings",\n'
                '      "type": "finding_list",\n'
                '      "items": [\n'
                '        {\n'
                '          "title": "Null handling bug",\n'
                '          "severity": "high",\n'
                '          "path": "src/foo.py",\n'
                '          "start_line": 10,\n'
                '          "end_line": 14,\n'
                '          "why": "Empty input can raise an exception.",\n'
                '          "fix": "Guard the null path before dereference."\n'
                '        }\n'
                '      ]\n'
                '    }\n'
                '  ],\n'
                '  "questions": [],\n'
                '  "residual_risks": ["Did not run integration tests."],\n'
                '  "sources": [{"kind": "file", "path": "src/foo.py", "start_line": 10, "end_line": 14}]\n'
                "}\n"
                "```"
            ),
            model="fake-model",
        )

    def get_default_model(self) -> str:
        return "fake-model"


class ModelRoutingProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model, "temperature": temperature, "max_tokens": max_tokens})
        return LLMResponse(content="routed result", model=model or "fake-model")

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
    agent._current_session_id.set("telegram:user-a")
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
    child_session_id = f"telegram:user-a:subagent:{task_id}"
    stored_roles = [message.role for message in storage.messages[child_session_id]]
    assert stored_roles == ["user", "assistant", "user", "assistant"]


def test_subagent_run_persists_child_run_lineage_and_parent_events(tmp_path):
    provider = FakeProvider()
    storage = MemoryStorage()
    registry = ToolRegistry()
    registry.register(DummyTool("read_file"))
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
    agent._current_session_id.set("telegram:user-a")
    agent._current_run_id.set("run_parent")
    agent._current_channel.set("telegram")
    agent._current_external_chat_id.set("user-a")
    agent.app_home = tmp_path / "opensprite-home"

    asyncio.run(storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "delegate work"}))

    result = asyncio.run(agent.run_subagent("do the task", prompt_type="implementer"))

    task_id = next(line.split(": ", 1)[1] for line in result.splitlines() if line.startswith("Task ID:"))
    child_session_id = f"telegram:user-a:subagent:{task_id}"
    child_runs = asyncio.run(storage.get_runs(child_session_id))
    assert len(child_runs) == 1
    child_run = child_runs[0]
    assert child_run.metadata["kind"] == "subagent"
    assert child_run.metadata["objective"] == "do the task"
    assert child_run.metadata["task_id"] == task_id
    assert child_run.metadata["prompt_type"] == "implementer"
    assert child_run.metadata["parent_session_id"] == "telegram:user-a"
    assert child_run.metadata["parent_run_id"] == "run_parent"
    assert child_run.metadata["resume"] is False
    assert child_run.metadata["child_session_id"] == child_session_id
    assert child_run.metadata["child_run_id"] == child_run.run_id
    assert child_run.metadata["summary"] == "done"

    child_trace = asyncio.run(storage.get_run_trace(child_session_id, child_run.run_id))
    assert child_trace is not None
    child_event_types = [event.event_type for event in child_trace.events]
    assert child_event_types[:3] == ["run_started", "tool_started", "tool_result"]
    assert "run_part_delta" in child_event_types
    assert child_event_types[-1] == "run_finished"
    assert child_trace.events[0].payload["parent_run_id"] == "run_parent"
    assert child_trace.events[-1].payload["summary"] == "done"
    assert [part.part_type for part in child_trace.parts] == ["tool_call", "tool_result", "llm_step", "llm_step", "assistant_message"]
    assert child_trace.parts[-1].metadata["task_id"] == task_id

    parent_trace = asyncio.run(storage.get_run_trace("telegram:user-a", "run_parent"))
    assert parent_trace is not None
    assert [event.event_type for event in parent_trace.events] == ["subagent.started", "subagent.completed"]
    artifacts = serialize_run_artifacts(parent_trace)
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_id"] == f"subagent:{task_id}"
    assert artifacts[0]["artifact_type"] == "subagent_task"
    assert artifacts[0]["kind"] == "work"
    assert artifacts[0]["status"] == "completed"
    assert artifacts[0]["title"] == "Subagent: implementer"
    assert artifacts[0]["detail"] == "done"
    assert artifacts[0]["metadata"] == {
        "status": "completed",
        "task_id": task_id,
        "prompt_type": "implementer",
        "child_session_id": child_session_id,
        "child_run_id": child_run.run_id,
        "parent_session_id": "telegram:user-a",
        "parent_run_id": "run_parent",
        "resume": False,
        "summary": "done",
        "executed_tool_calls": 1,
        "had_tool_error": False,
        "verification_attempted": False,
        "verification_passed": False,
        "delegation_mode": "serial",
    }
    assert artifacts[0]["source"] == "event"
    assert artifacts[0]["source_id"] == 2
    assert artifacts[0]["sources"] == ["event"]


def test_run_subagents_many_returns_ordered_results_and_parent_trace(tmp_path):
    async def scenario():
        provider = ParallelOutcomeProvider()
        storage = MemoryStorage()
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
        agent._current_session_id.set("telegram:user-a")
        agent._current_run_id.set("run_parent")
        agent._current_channel.set("telegram")
        agent._current_external_chat_id.set("user-a")
        agent.app_home = tmp_path / "opensprite-home"
        await storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "parallel delegation"})

        result = await agent.run_subagents_many(
            [
                {"task": "slow task", "prompt_type": "researcher"},
                {"task": "fast task", "prompt_type": "code-reviewer"},
            ],
            max_parallel=2,
        )
        parent_trace = await storage.get_run_trace("telegram:user-a", "run_parent")
        child_sessions = [session_id for session_id in await storage.get_all_sessions() if ":subagent:" in session_id]
        child_statuses = [
            (await storage.get_runs(session_id, limit=1))[0].status
            for session_id in child_sessions
        ]
        return result, parent_trace, child_sessions, child_statuses

    result, parent_trace, child_sessions, child_statuses = asyncio.run(scenario())

    assert "Parallel delegation completed: 2 task(s), 0 failed." in result
    assert result.index("[1] researcher") < result.index("[2] code-reviewer")
    assert "reply:slow task" in result
    assert "reply:fast task" in result
    assert len(child_sessions) == 2
    assert child_statuses == ["completed", "completed"]
    assert parent_trace is not None
    assert [event.event_type for event in parent_trace.events].count("subagent.group.started") == 1
    assert [event.event_type for event in parent_trace.events].count("subagent.group.completed") == 1
    assert [event.event_type for event in parent_trace.events].count("subagent.started") == 2
    assert [event.event_type for event in parent_trace.events].count("subagent.completed") == 2


def test_run_subagents_many_rejects_write_capable_profiles(tmp_path):
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=ResumeProvider(),
        storage=MemoryStorage(),
        context_builder=FakeContextBuilder(tmp_path / "workspace"),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(
        agent.run_subagents_many(
            [{"task": "do the task", "prompt_type": "implementer"}],
            max_parallel=2,
        )
    )

    assert "parallel delegation only supports read-only or research subagents" in result


def test_run_subagents_many_cancels_children_with_parent_cancel_request(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=SlowParallelProvider(),
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
        agent._current_session_id.set("telegram:user-a")
        agent._current_run_id.set("run_parent")
        agent._current_channel.set("telegram")
        agent._current_external_chat_id.set("user-a")
        agent.app_home = tmp_path / "opensprite-home"
        await storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "parallel cancellation"})
        agent.run_state.start("telegram:user-a", "run_parent")
        task = asyncio.create_task(
            agent.run_subagents_many(
                [{"task": "slow task", "prompt_type": "researcher"}],
                max_parallel=1,
            )
        )
        try:
            for _ in range(20):
                if any(":subagent:" in session_id for session_id in await storage.get_all_sessions()):
                    break
                await asyncio.sleep(0.01)
            agent.run_state.request_cancel("telegram:user-a", "run_parent")
            try:
                await task
            except asyncio.CancelledError:
                cancelled = True
            else:
                cancelled = False
        finally:
            agent.run_state.finish("telegram:user-a", "run_parent")
        child_sessions = [session_id for session_id in await storage.get_all_sessions() if ":subagent:" in session_id]
        child_statuses = [
            (await storage.get_runs(session_id, limit=1))[0].status
            for session_id in child_sessions
        ]
        parent_trace = await storage.get_run_trace("telegram:user-a", "run_parent")
        return cancelled, child_statuses, parent_trace

    cancelled, child_statuses, parent_trace = asyncio.run(scenario())

    assert cancelled is True
    assert child_statuses == ["cancelled"]
    assert parent_trace is not None
    assert "subagent.group.cancelled" in [event.event_type for event in parent_trace.events]
    assert "subagent.cancelled" in [event.event_type for event in parent_trace.events]


def test_run_subagents_many_emits_group_failed_when_one_child_fails(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=PartiallyFailingParallelProvider(),
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
        agent._current_session_id.set("telegram:user-a")
        agent._current_run_id.set("run_parent")
        agent._current_channel.set("telegram")
        agent._current_external_chat_id.set("user-a")
        agent.app_home = tmp_path / "opensprite-home"
        await storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "parallel failure"})

        result = await agent.run_subagents_many(
            [
                {"task": "healthy task", "prompt_type": "researcher"},
                {"task": "broken task", "prompt_type": "code-reviewer"},
            ],
            max_parallel=2,
        )
        parent_trace = await storage.get_run_trace("telegram:user-a", "run_parent")
        child_sessions = [session_id for session_id in await storage.get_all_sessions() if ":subagent:" in session_id]
        child_statuses = sorted([
            (await storage.get_runs(session_id, limit=1))[0].status
            for session_id in child_sessions
        ])
        return result, parent_trace, child_statuses

    result, parent_trace, child_statuses = asyncio.run(scenario())

    assert "Parallel delegation completed: 2 task(s), 1 failed." in result
    assert "Error:" in result
    assert child_statuses == ["completed", "failed"]
    assert parent_trace is not None
    assert "subagent.group.failed" in [event.event_type for event in parent_trace.events]


def test_run_subagent_uses_prompt_model_override_when_present(tmp_path):
    workspace = tmp_path / "workspace"
    session_workspace = get_session_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = session_workspace / "subagent_prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "custom-reviewer.md").write_text(
        "---\n"
        "name: custom-reviewer\n"
        "description: Custom reviewer with a routed model.\n"
        "tool_profile: read-only\n"
        "llm_model: review-model\n"
        "---\n"
        "Review the requested task.\n",
        encoding="utf-8",
    )
    provider = ModelRoutingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=MemoryStorage(),
        context_builder=FakeContextBuilder(workspace),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_subagent("review this task", prompt_type="custom-reviewer"))

    assert "Subagent: custom-reviewer" in result
    assert provider.calls[0]["model"] == "review-model"


def test_run_subagent_uses_prompt_decoding_overrides_when_present(tmp_path):
    workspace = tmp_path / "workspace"
    session_workspace = get_session_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = session_workspace / "subagent_prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "custom-reviewer.md").write_text(
        "---\n"
        "name: custom-reviewer\n"
        "description: Custom reviewer with routed decoding params.\n"
        "tool_profile: read-only\n"
        "llm_model: review-model\n"
        "llm_temperature: 0.1\n"
        "llm_max_tokens: 123\n"
        "---\n"
        "Review the requested task.\n",
        encoding="utf-8",
    )
    provider = ModelRoutingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=MemoryStorage(),
        context_builder=FakeContextBuilder(workspace),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    asyncio.run(agent.run_subagent("review this task", prompt_type="custom-reviewer"))

    assert provider.calls[0]["model"] == "review-model"
    assert provider.calls[0]["temperature"] == 0.1
    assert provider.calls[0]["max_tokens"] == 123


def test_run_subagent_uses_prompt_provider_override_when_present(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    session_workspace = get_session_workspace("telegram:user-a", workspace_root=workspace)
    prompt_dir = session_workspace / "subagent_prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "custom-reviewer.md").write_text(
        "---\n"
        "name: custom-reviewer\n"
        "description: Custom reviewer with provider override.\n"
        "tool_profile: read-only\n"
        "llm_provider: review\n"
        "llm_model: review-model\n"
        "---\n"
        "Review the requested task.\n",
        encoding="utf-8",
    )
    base_provider = ModelRoutingProvider()
    routed_provider = ModelRoutingProvider()

    def fake_create_llm(api_key: str, model: str, base_url: str = "", provider_name: str = "", enabled: bool = True):
        assert provider_name == "review"
        assert model == "provider-review-model"
        return routed_provider

    monkeypatch.setattr("opensprite.agent.subagents.create_llm", fake_create_llm)

    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=base_provider,
        storage=MemoryStorage(),
        context_builder=FakeContextBuilder(workspace),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(max_tool_iterations=3),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        llm_config=LLMsConfig(
            temperature=0.7,
            max_tokens=2048,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            pass_decoding_params=True,
            providers={
                "review": ProviderConfig(
                    api_key="review-key",
                    model="provider-review-model",
                    base_url="https://review.example/v1",
                    enabled=True,
                )
            },
            default="review",
        ),
        **Config.packaged_agent_llm_chat_kwargs(),
    )
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    asyncio.run(agent.run_subagent("review this task", prompt_type="custom-reviewer"))

    assert base_provider.calls == []
    assert routed_provider.calls[0]["model"] == "review-model"


def test_run_subagent_strips_trailing_json_and_persists_structured_output(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=StructuredReviewProvider(),
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
        agent._current_session_id.set("telegram:user-a")
        agent._current_run_id.set("run_parent")
        agent._current_channel.set("telegram")
        agent._current_external_chat_id.set("user-a")
        agent.app_home = tmp_path / "opensprite-home"
        await storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "structured review"})

        result = await agent.run_subagent("review this", prompt_type="code-reviewer")
        child_session_id = next(session_id for session_id in await storage.get_all_sessions() if ":subagent:" in session_id)
        child_messages = await storage.get_messages(child_session_id)
        parent_trace = await storage.get_run_trace("telegram:user-a", "run_parent")
        child_trace = await storage.get_run_trace(child_session_id, (await storage.get_runs(child_session_id, limit=1))[0].run_id)
        return result, child_messages, parent_trace, child_trace

    result, child_messages, parent_trace, child_trace = asyncio.run(scenario())

    assert "```json" not in result
    assert "One high-risk bug found." not in result
    assert child_messages[-1].role == "assistant"
    assert "```json" not in child_messages[-1].content
    assert child_messages[-1].metadata["structured_output"]["prompt_type"] == "code-reviewer"
    assert child_messages[-1].metadata["structured_output"]["finding_count"] == 1
    assert parent_trace is not None
    completed_payload = next(event.payload for event in parent_trace.events if event.event_type == "subagent.completed")
    assert completed_payload["structured_output"]["summary"] == "One high-risk bug found."
    assert completed_payload["structured_output"]["finding_count"] == 1
    assert child_trace is not None
    assistant_part = next(part for part in child_trace.parts if part.part_type == "assistant_message")
    assert "```json" not in assistant_part.content
    assert assistant_part.metadata["structured_output"]["residual_risk_count"] == 1


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
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    first = asyncio.run(agent.run_subagent("initial task", prompt_type="implementer"))
    task_id = next(line.split(": ", 1)[1] for line in first.splitlines() if line.startswith("Task ID:"))
    result = asyncio.run(agent.run_subagent("continue task", prompt_type="writer", task_id=task_id))

    assert f"was created with prompt_type 'implementer'" in result
