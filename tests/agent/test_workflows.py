import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import Config, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.llms.base import LLMResponse
from opensprite.storage import MemoryStorage
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


class WorkflowProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        task_text = next(
            str(message.content)
            for message in reversed(messages)
            if getattr(message, "role", None) == "user"
        )
        if "Review the current workspace changes" in task_text or "Resume the code review step" in task_text:
            return LLMResponse(content="Review Findings\n- No major findings.", model="fake-model")
        if "Create a clear outline" in task_text or "Resume the outline step" in task_text:
            return LLMResponse(content="建議標題：Workflow Outline\n\n## 大綱\n1. Step one", model="fake-model")
        if "Add the minimal effective tests" in task_text or "Resume the tests step" in task_text:
            return LLMResponse(content="Added focused tests.", model="fake-model")
        return LLMResponse(content="Implemented the requested change.", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


class FailingWorkflowProvider(WorkflowProvider):
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        task_text = next(
            str(message.content)
            for message in reversed(messages)
            if getattr(message, "role", None) == "user"
        )
        if "Review the current workspace changes" in task_text:
            raise RuntimeError("review step failed")
        return await super().chat(messages, tools=tools, model=model, temperature=temperature, max_tokens=max_tokens, **kwargs)


def _make_agent(tmp_path: Path, provider) -> AgentLoop:
    return AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
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


def test_run_workflow_runs_implement_then_review_and_emits_trace(tmp_path):
    async def scenario():
        provider = WorkflowProvider()
        agent = _make_agent(tmp_path, provider)
        storage = agent.storage
        agent._current_session_id.set("telegram:user-a")
        agent._current_run_id.set("run_parent")
        agent._current_channel.set("telegram")
        agent._current_external_chat_id.set("user-a")
        agent.app_home = tmp_path / "opensprite-home"
        await storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "workflow"})

        result = await agent.run_workflow("implement_then_review", "Implement a safe change.")
        trace = await storage.get_run_trace("telegram:user-a", "run_parent")
        child_sessions = [session_id for session_id in await storage.get_all_sessions() if ":subagent:" in session_id]
        return result, trace, child_sessions

    result, trace, child_sessions = asyncio.run(scenario())

    assert "Workflow: implement_then_review" in result
    assert "[1] implementer | completed" in result
    assert "[2] code-reviewer | completed" in result
    assert len(child_sessions) == 2
    assert trace is not None
    event_types = [event.event_type for event in trace.events]
    assert event_types.count("workflow.started") == 1
    assert event_types.count("workflow.step.started") == 2
    assert event_types.count("workflow.step.completed") == 2
    assert event_types.count("workflow.completed") == 1


def test_run_workflow_returns_error_for_unknown_workflow(tmp_path):
    agent = _make_agent(tmp_path, WorkflowProvider())
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_workflow("unknown_flow", "Do work"))

    assert "unknown workflow 'unknown_flow'" in result


def test_run_workflow_can_resume_from_specific_step(tmp_path):
    async def scenario():
        provider = WorkflowProvider()
        agent = _make_agent(tmp_path, provider)
        storage = agent.storage
        agent._current_session_id.set("telegram:user-a")
        agent._current_run_id.set("run_parent")
        agent._current_channel.set("telegram")
        agent._current_external_chat_id.set("user-a")
        agent.app_home = tmp_path / "opensprite-home"
        await storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "workflow resume"})

        result = await agent.run_workflow("implement_then_review", "Implement a safe change.", start_step="review")
        trace = await storage.get_run_trace("telegram:user-a", "run_parent")
        return result, trace, provider.calls

    result, trace, calls = asyncio.run(scenario())

    assert "Workflow: implement_then_review" in result
    assert "Resumed from step: 2" in result
    assert "[2] code-reviewer | completed" in result
    assert trace is not None
    started_event = next(event for event in trace.events if event.event_type == "workflow.started")
    assert started_event.payload["resumed"] is True
    assert started_event.payload["start_step_id"] == "review"
    assert started_event.payload["start_step_label"] == "Code review"
    assert any("Resume the code review step" in str(message.content) for call in calls for message in call["messages"])


def test_run_workflow_returns_error_for_unknown_start_step(tmp_path):
    agent = _make_agent(tmp_path, WorkflowProvider())
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_workflow("implement_then_review", "Do work", start_step="nope"))

    assert "unknown start_step 'nope'" in result


def test_run_workflow_emits_failed_trace_when_step_errors(tmp_path):
    async def scenario():
        provider = FailingWorkflowProvider()
        agent = _make_agent(tmp_path, provider)
        storage = agent.storage
        agent._current_session_id.set("telegram:user-a")
        agent._current_run_id.set("run_parent")
        agent._current_channel.set("telegram")
        agent._current_external_chat_id.set("user-a")
        agent.app_home = tmp_path / "opensprite-home"
        await storage.create_run("telegram:user-a", "run_parent", metadata={"objective": "workflow fail"})

        result = await agent.run_workflow("implement_then_review", "Implement a safe change.")
        trace = await storage.get_run_trace("telegram:user-a", "run_parent")
        return result, trace

    result, trace = asyncio.run(scenario())

    assert "workflow step 'review' failed" in result
    assert trace is not None
    event_types = [event.event_type for event in trace.events]
    assert "workflow.step.failed" in event_types
    assert "workflow.failed" in event_types
    failed_event = next(event for event in trace.events if event.event_type == "workflow.failed")
    assert failed_event.payload["next_step_id"] == "review"
    assert failed_event.payload["next_step_label"] == "Code review"
