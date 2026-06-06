import asyncio
import json
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.agent.execution import SubagentTaskOutcome, WorkflowStepSpec
from opensprite.agent.execution import (
    SubagentWorkflowService,
    WORKFLOW_COMPLETED_STATUS,
    WORKFLOW_ERROR_FIELD,
    WORKFLOW_NEXT_STEP_ID_FIELD,
    WORKFLOW_NEXT_STEP_LABEL_FIELD,
    WORKFLOW_REVIEW_ATTEMPTED_FIELD,
    WORKFLOW_REVIEW_FINDING_COUNT_FIELD,
    WORKFLOW_REVIEW_PASSED_FIELD,
    WORKFLOW_STATUS_FIELD,
    WORKFLOW_SUMMARY_FIELD,
)
from opensprite.config.schema import Config, LogConfig, MemoryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.llms.base import LLMResponse
from opensprite.runs.events import (
    WORKFLOW_COMPLETED_EVENT,
    WORKFLOW_FAILED_EVENT,
    WORKFLOW_STARTED_EVENT,
    WORKFLOW_STEP_COMPLETED_EVENT,
    WORKFLOW_STEP_FAILED_EVENT,
    WORKFLOW_STEP_STARTED_EVENT,
)
from opensprite.storage import MemoryStorage
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.result_status import classify_tool_result_status


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


def _clean_review_response() -> str:
    payload = {
        "schema_version": 1,
        "contract": "readonly_subagent_result",
        "prompt_type": "code-reviewer",
        "status": "ok",
        "summary": "No major findings.",
        "sections": [],
        "questions": [],
        "residual_risks": [],
        "sources": [],
    }
    return "Review Findings\n- No major findings.\n\n```json\n" + json.dumps(payload) + "\n```"


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
            return LLMResponse(content=_clean_review_response(), model="fake-model")
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


def test_workflow_review_outcome_requires_structured_clean_review():
    review = SubagentTaskOutcome(
        task_id="task_review",
        child_session_id="session_review",
        child_run_id="run_review",
        prompt_type="code-reviewer",
        status="completed",
        content="Review Findings\n- No major findings.",
        summary="No major findings.",
    )

    result = SubagentWorkflowService._review_outcome([review])

    assert result[WORKFLOW_REVIEW_ATTEMPTED_FIELD] is True
    assert result[WORKFLOW_REVIEW_PASSED_FIELD] is False
    assert result[WORKFLOW_REVIEW_FINDING_COUNT_FIELD] == 0


def test_workflow_payloads_share_outcome_fields():
    step = WorkflowStepSpec(
        step_id="implement",
        label="Implement",
        prompt_type="implementer",
        task_builder=lambda task, outcomes: task,
    )
    outcome = SubagentTaskOutcome(
        task_id="task_implement",
        prompt_type="implementer",
        child_session_id="session_implement",
        child_run_id="run_implement",
        status=WORKFLOW_COMPLETED_STATUS,
        summary="Implemented safely.",
    )

    step_payload = SubagentWorkflowService._step_payload(
        workflow_run_id="workflow_123",
        workflow_id="implement_then_review",
        spec=step,
        step_index=1,
        total_steps=1,
        outcome=outcome,
    )
    workflow_payload = SubagentWorkflowService._workflow_payload(
        workflow_run_id="workflow_123",
        workflow_id="implement_then_review",
        task_preview="Implement safely",
        steps=(step,),
        outcomes=[outcome],
        status=WORKFLOW_COMPLETED_STATUS,
    )

    expected = {
        WORKFLOW_STATUS_FIELD: WORKFLOW_COMPLETED_STATUS,
        "task_id": "task_implement",
        "child_session_id": "session_implement",
        "child_run_id": "run_implement",
        WORKFLOW_SUMMARY_FIELD: "Implemented safely.",
        WORKFLOW_ERROR_FIELD: "",
    }
    for key, value in expected.items():
        assert step_payload[key] == value
        assert workflow_payload["steps"][0][key] == value


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
    assert event_types.count(WORKFLOW_STARTED_EVENT) == 1
    assert event_types.count(WORKFLOW_STEP_STARTED_EVENT) == 2
    assert event_types.count(WORKFLOW_STEP_COMPLETED_EVENT) == 2
    assert event_types.count(WORKFLOW_COMPLETED_EVENT) == 1


def test_run_workflow_returns_error_for_unknown_workflow(tmp_path):
    agent = _make_agent(tmp_path, WorkflowProvider())
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_workflow("unknown_flow", "Do work"))

    status = classify_tool_result_status(result)
    assert status.error_type == "RunWorkflowToolError"
    assert status.category == "unknown_workflow"
    assert "unknown workflow 'unknown_flow'" in status.error


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
    started_event = next(event for event in trace.events if event.event_type == WORKFLOW_STARTED_EVENT)
    assert started_event.payload["resumed"] is True
    assert started_event.payload["start_step_id"] == "review"
    assert started_event.payload["start_step_label"] == "Code review"
    assert any("Resume the code review step" in str(message.content) for call in calls for message in call["messages"])


def test_run_workflow_returns_error_for_unknown_start_step(tmp_path):
    agent = _make_agent(tmp_path, WorkflowProvider())
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_workflow("implement_then_review", "Do work", start_step="nope"))

    status = classify_tool_result_status(result)
    assert status.error_type == "ToolValidationError"
    assert status.category == "unknown_start_step"
    assert status.invalid_arguments is True
    assert "unknown start_step 'nope'" in status.error


def test_run_workflow_returns_validation_error_for_empty_task(tmp_path):
    agent = _make_agent(tmp_path, WorkflowProvider())
    agent._current_session_id.set("telegram:user-a")
    agent.app_home = tmp_path / "opensprite-home"

    result = asyncio.run(agent.run_workflow("implement_then_review", " "))
    status = classify_tool_result_status(result)

    assert status.error_type == "ToolValidationError"
    assert status.category == "invalid_arguments"
    assert status.invalid_arguments is True
    assert "workflow task must be a non-empty string" in status.error
    assert json.loads(result)["metadata"] == {"tool_name": "run_workflow"}


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

    status = classify_tool_result_status(result)
    assert status.error_type == "WorkflowExecutionError"
    assert status.category == "workflow_step_failed"
    assert "workflow step 'review' failed" in status.error
    assert trace is not None
    event_types = [event.event_type for event in trace.events]
    assert WORKFLOW_STEP_FAILED_EVENT in event_types
    assert WORKFLOW_FAILED_EVENT in event_types
    failed_event = next(event for event in trace.events if event.event_type == WORKFLOW_FAILED_EVENT)
    assert failed_event.payload[WORKFLOW_NEXT_STEP_ID_FIELD] == "review"
    assert failed_event.payload[WORKFLOW_NEXT_STEP_LABEL_FIELD] == "Code review"
