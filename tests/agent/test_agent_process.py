import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from opensprite.agent.agent import AgentLoop, _verification_result_is_tool_error
from opensprite.agent.completion_gate import (
    NO_PROGRESS_DURING_CONTINUATION_REASON,
    REVIEW_EVIDENCE_STILL_MISSING_REASON,
)
from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.execution import ContextCompactionEvent, ExecutionResult
from opensprite.runs.trace import RunBusyError
from opensprite.agent.execution import TaskArtifact
from opensprite.agent.task_contract import (
    EvidenceRequirement,
    LLM_PLANNER_CONTRACT_SOURCES,
    PLANNER_INVALID_STATUS,
    PLANNER_METADATA_REASON_FIELD,
    PLANNER_METADATA_STATUS_FIELD,
    PLANNER_VALIDATED_STATUS,
    TaskContract,
)
from opensprite.tools.evidence import VERIFICATION_NAME_METADATA_FIELD, VERIFICATION_STATUS_METADATA_FIELD
from opensprite.agent.turn_runner import AgentTurnRunner
from opensprite.bus import MessageBus
from opensprite.bus.events import InboundMessage, OutboundMessage
from opensprite.config.schema import AgentConfig, Config, LogConfig, MemoryConfig, MessagesConfig, RecentSummaryConfig, SearchConfig, ToolsConfig, UserProfileConfig
from opensprite.context.paths import get_session_skills_dir
from opensprite.bus.message import UserMessage
from opensprite.documents.active_task import create_active_task_store
from opensprite.llms.base import LLMResponse, ToolCall
from opensprite.runs.events import (
    ACTIVE_TASK_COMMAND_APPLIED_EVENT,
    AUTO_CONTINUE_COMPLETED_EVENT,
    AUTO_CONTINUE_SCHEDULED_EVENT,
    AUTO_CONTINUE_SKIPPED_EVENT,
    COMPLETION_GATE_EVALUATED_EVENT,
    CURATOR_STARTED_EVENT,
    FILE_CHANGED_EVENT,
    HARNESS_CHECKPOINT_RECORDED_EVENT,
    HARNESS_SCORECARD_RECORDED_EVENT,
    LLM_STATUS_EVENT,
    PERMISSION_GRANTED_EVENT,
    PERMISSION_REQUESTED_EVENT,
    TASK_CONTEXT_RESOLVED_EVENT,
    TASK_CHECKLIST_UPDATED_EVENT,
    TASK_INTENT_DETECTED_EVENT,
    TASK_OBJECTIVE_RESOLVED_EVENT,
    TOOL_APPROVAL_APPROVED_EVENT,
    TOOL_APPROVAL_REQUESTED_EVENT,
    TOOL_PERMISSION_ALLOWED_EVENT,
    TOOL_PERMISSION_APPROVAL_REQUIRED_EVENT,
    TOOL_PERMISSION_CHECKED_EVENT,
    TOOL_RESULT_EVENT,
    TOOL_STARTED_EVENT,
    VERIFICATION_RESULT_EVENT,
    VERIFICATION_STARTED_EVENT,
    WORK_PLAN_CREATED_EVENT,
    WORK_PROGRESS_UPDATED_EVENT,
)
from opensprite.runs.lifecycle import RUN_FINISHED_EVENT, RUN_STARTED_EVENT
from opensprite.media.router import MediaRouter
from opensprite.storage import MemoryStorage, StoredDelegatedTask
from opensprite.storage.base import StoredMessage, StoredWorkState
from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.process_runtime import BackgroundSession
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.result_status import tool_error_result
from opensprite.tools.shell_runtime import CapturedOutputChunk


class FakeContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.last_history = None

    def build_system_prompt(self, session_id: str = "default") -> str:
        return "system"

    def build_messages(self, history, current_message, current_images=None, channel=None, session_id=None):
        self.last_history = list(history)
        return [{"role": "user", "content": current_message}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return messages

    def add_assistant_message(self, messages, content, tool_calls=None):
        return messages


def _python_shell_command(code: str) -> str:
    argv = [sys.executable, "-u", "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _extract_session_id(result: str) -> str:
    for line in result.splitlines():
        if line.startswith("Session ID: "):
            return line.removeprefix("Session ID: ").strip()
    raise AssertionError(f"Session ID missing from result: {result}")


def _is_planner_call(messages, tools=None) -> bool:
    if tools:
        return False
    first = str(getattr(messages[0], "content", "") or "") if messages else ""
    return "task planner" in first


def _is_completion_judge_call(messages, tools=None) -> bool:
    if tools:
        return False
    first = str(getattr(messages[0], "content", "") or "") if messages else ""
    return "completion judge" in first


def _completion_judge_facts(messages) -> dict:
    latest_user_text = next(
        (str(getattr(message, "content", "") or "") for message in reversed(messages) if getattr(message, "role", None) == "user"),
        "",
    )
    marker = "Facts:\n"
    raw = latest_user_text.split(marker, 1)[1] if marker in latest_user_text else "{}"
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _delegated_task_structured_output(item: dict) -> dict:
    structured = item.get("structured_output")
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if not isinstance(structured, dict):
        structured = metadata.get("structured_output")
    return structured if isinstance(structured, dict) else {}


def _is_clean_code_review_task(item: dict) -> bool:
    if item.get("prompt_type") != "code-reviewer":
        return False
    structured = _delegated_task_structured_output(item)
    return str(structured.get("status") or "") == "ok" and int(structured.get("finding_count") or 0) == 0


def _fake_clean_review_response() -> str:
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
    return "Review completed without findings.\n\n```json\n" + json.dumps(payload) + "\n```"


def _completion_judge_response(messages) -> LLMResponse:
    facts = _completion_judge_facts(messages)
    response = str(facts.get("assistant_response", {}).get("text") or "")
    execution = facts.get("execution", {}) if isinstance(facts.get("execution"), dict) else {}
    contract = facts.get("task_contract", {}) if isinstance(facts.get("task_contract"), dict) else {}
    intent = facts.get("task_intent", {}) if isinstance(facts.get("task_intent"), dict) else {}
    objective = str(contract.get("objective") or intent.get("objective") or "")
    requirements = contract.get("requirements") if isinstance(contract.get("requirements"), list) else []
    requirement_kinds = {str(item.get("kind") or "") for item in requirements if isinstance(item, dict)}
    delegated_tasks = facts.get("delegated_tasks") if isinstance(facts.get("delegated_tasks"), list) else []
    review_attempted = any(
        isinstance(item, dict) and item.get("prompt_type") in {"code-reviewer", "security-reviewer", "async-concurrency-reviewer"}
        for item in delegated_tasks
    )
    clean_review = any(
        isinstance(item, dict) and _is_clean_code_review_task(item) for item in delegated_tasks
    )
    file_changes = int(execution.get("file_change_count") or 0)
    verification_passed = bool(execution.get("verification_passed"))
    verification_attempted = bool(execution.get("verification_attempted"))
    workflow_outcomes = facts.get("workflow_outcomes") if isinstance(facts.get("workflow_outcomes"), list) else []
    completed_workflow = next(
        (item for item in reversed(workflow_outcomes) if isinstance(item, dict) and item.get("status") == "completed"),
        None,
    )
    cancelled_workflow = next(
        (item for item in reversed(workflow_outcomes) if isinstance(item, dict) and item.get("status") == "cancelled"),
        None,
    )
    if "?" in response or "？" in response:
        payload = {"status": "waiting_user", "reason": "assistant requested missing information", "active_task_status": "waiting_user", "active_task_detail": response}
    elif verification_attempted and not verification_passed:
        payload = {
            "status": "needs_verification",
            "reason": "verification did not pass",
            "active_task_status": "in_progress",
            "verification_required": True,
            "verification_attempted": True,
            "verification_passed": False,
            "verification_action": "pytest",
            "verification_path": ".",
        }
    elif (
        completed_workflow
        and completed_workflow.get("review_passed") is True
        and ("tests" in objective.lower() or "verify" in objective.lower())
        and not verification_passed
    ):
        payload = {
            "status": "needs_verification",
            "reason": "required verification was not recorded",
            "active_task_status": "in_progress",
            "verification_required": True,
            "verification_attempted": False,
            "verification_passed": False,
            "verification_action": "pytest",
            "verification_path": ".",
        }
    elif completed_workflow and completed_workflow.get("review_passed") is True:
        workflow_name = str(completed_workflow.get("workflow") or "workflow")
        payload = {"status": "complete", "reason": f"workflow {workflow_name} completed with clean review evidence", "active_task_status": "done", "review_attempted": True, "review_passed": True}
    elif completed_workflow and completed_workflow.get("review_passed") is False:
        payload = {
            "status": "needs_review",
            "reason": "review findings require follow-up",
            "active_task_status": "in_progress",
            "review_required": True,
            "review_attempted": True,
            "review_passed": False,
            "review_summary": str(completed_workflow.get("review_summary") or ""),
            "review_finding_count": int(completed_workflow.get("review_finding_count") or 0),
            "follow_up_workflow": completed_workflow.get("workflow"),
            "follow_up_step_id": "implement",
            "follow_up_step_label": "Address review findings",
            "follow_up_prompt_type": "implementer",
        }
    elif execution.get("had_tool_error"):
        payload = {"status": "blocked", "reason": response or "tool error", "active_task_status": "blocked", "active_task_detail": response}
    elif "reviewed outcome" in response:
        payload = {"status": "complete", "reason": "judge accepted reviewed workflow outcome", "active_task_status": "done", "review_attempted": True, "review_passed": True}
    elif cancelled_workflow:
        payload = {
            "status": "needs_review",
            "reason": "workflow follow-up is required",
            "active_task_status": "in_progress",
            "follow_up_workflow": cancelled_workflow.get("workflow"),
            "follow_up_step_id": cancelled_workflow.get("next_step_id"),
            "follow_up_step_label": cancelled_workflow.get("next_step_label"),
            "follow_up_prompt_type": cancelled_workflow.get("next_step_prompt_type"),
        }
    elif (("file_change" in requirement_kinds) or "refactor" in objective.lower() or "implement" in objective.lower()) and file_changes <= 0:
        payload = {"status": "incomplete", "reason": "expected code changes were not recorded", "active_task_status": "in_progress"}
    elif (("verification" in requirement_kinds) or "tests" in objective.lower() or "verify" in objective.lower()) and not verification_passed:
        payload = {
            "status": "needs_verification",
            "reason": "required verification was not recorded",
            "active_task_status": "in_progress",
            "verification_required": True,
            "verification_attempted": verification_attempted,
            "verification_passed": False,
            "verification_action": "pytest",
            "verification_path": ".",
        }
    elif file_changes > 0 and not review_attempted:
        payload = {"status": "needs_review", "reason": "delegated review was not recorded for code changes", "active_task_status": "in_progress", "review_required": True}
    elif delegated_tasks and any(isinstance(item, dict) and item.get("selected") for item in delegated_tasks):
        payload = {"status": "incomplete", "reason": "delegated task is active", "active_task_status": "in_progress"}
    elif review_attempted and not clean_review:
        payload = {"status": "needs_review", "reason": "review findings require follow-up", "active_task_status": "in_progress", "review_required": True, "review_attempted": True}
    else:
        payload = {
            "status": "complete",
            "reason": "judge accepted test response",
            "active_task_status": "done",
            "verification_required": "verification" in requirement_kinds,
            "verification_attempted": verification_attempted,
            "verification_passed": verification_passed,
            "review_attempted": review_attempted,
            "review_passed": clean_review or not review_attempted,
        }
    return LLMResponse(content=json.dumps(payload), model="fake-model")


def _planner_response(task_type: str = "code_change") -> LLMResponse:
    payload = {
        "task_type": task_type,
        "required_tool_groups": ["workspace_read", "workspace_write"] if task_type == "code_change" else [],
        "allow_no_tool_final": task_type == "pure_answer",
        "final_answer_required": True,
        "reason": "test planner contract",
    }
    return LLMResponse(content=json.dumps(payload), model="fake-model")


class FakeProvider:
    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        if _is_planner_call(messages, tools):
            return _planner_response()
        if _is_completion_judge_call(messages, tools):
            return _completion_judge_response(messages)
        raise AssertionError("provider.chat should only be called by the planner in this test")

    def get_default_model(self) -> str:
        return "fake-model"


class FakeSpeechProvider:
    async def transcribe(self, audio_data_url, *, model=None, language=None):
        return "請幫我整理這段語音重點"


def test_aggregate_execution_results_keeps_only_latest_stop_reason():
    aggregate = AgentTurnRunner._aggregate_execution_results(
        [
            ExecutionResult(
                content="stopped",
                executed_tool_calls=1,
                stop_reason="max_tool_iterations",
                stop_metadata={"iteration_limit": 1},
            ),
            ExecutionResult(content="done", executed_tool_calls=0),
        ],
        content="done",
    )

    assert aggregate.content == "done"
    assert aggregate.executed_tool_calls == 1
    assert aggregate.stop_reason is None
    assert aggregate.stop_metadata == {}


def test_aggregate_execution_results_keeps_valid_contract_over_retry_planning_error():
    valid_contract = TaskContract(
        objective="Find sources",
        task_type="web_research",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS},
    )
    planning_error = TaskContract(
        objective="Find sources",
        task_type="planning_error",
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={
            PLANNER_METADATA_STATUS_FIELD: PLANNER_INVALID_STATUS,
            PLANNER_METADATA_REASON_FIELD: "invalid JSON",
        },
    )

    aggregate = AgentTurnRunner._aggregate_execution_results(
        [
            ExecutionResult(content="first pass", executed_tool_calls=1, task_contract=valid_contract),
            ExecutionResult(content="retry answer", executed_tool_calls=0, task_contract=planning_error),
        ],
        content="retry answer",
    )

    assert aggregate.content == "retry answer"
    assert aggregate.executed_tool_calls == 1
    assert aggregate.task_contract is valid_contract


def test_aggregate_execution_results_keeps_valid_contract_over_retry_planning_error_contract():
    valid_contract = TaskContract(
        objective="Find sources",
        task_type="web_research",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS},
    )
    planning_error_contract = TaskContract(
        objective="Find sources",
        task_type="planning_error",
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={
            PLANNER_METADATA_STATUS_FIELD: PLANNER_INVALID_STATUS,
            PLANNER_METADATA_REASON_FIELD: "invalid JSON",
        },
    )

    aggregate = AgentTurnRunner._aggregate_execution_results(
        [
            ExecutionResult(content="first pass", executed_tool_calls=1, task_contract=valid_contract),
            ExecutionResult(content="retry answer", executed_tool_calls=0, task_contract=planning_error_contract),
        ],
        content="retry answer",
    )

    assert aggregate.content == "retry answer"
    assert aggregate.executed_tool_calls == 1
    assert aggregate.task_contract is valid_contract


def test_aggregate_execution_results_keeps_tool_contract_over_auto_continue_pure_answer():
    web_contract = TaskContract(
        objective="Find sources",
        task_type="web_research",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS},
        harness_profile={"name": "research", "task_type": "web_research"},
    )
    final_answer_contract = TaskContract(
        objective="Continue the current task",
        task_type="pure_answer",
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS},
        harness_profile={"name": "chat", "task_type": "pure_answer"},
    )

    aggregate = AgentTurnRunner._aggregate_execution_results(
        [
            ExecutionResult(content="", executed_tool_calls=1, task_contract=web_contract),
            ExecutionResult(content="final answer", executed_tool_calls=0, task_contract=final_answer_contract),
        ],
        content="final answer",
    )

    assert aggregate.content == "final answer"
    assert aggregate.executed_tool_calls == 1
    assert aggregate.task_contract is web_contract


def test_aggregate_execution_results_does_not_mark_visible_final_as_internal_only():
    aggregate = AgentTurnRunner._aggregate_execution_results(
        [
            ExecutionResult(content="first pass", assistant_internal_only_response=False),
            ExecutionResult(content="", assistant_internal_only_response=True),
        ],
        content="first pass",
    )

    assert aggregate.content == "first pass"
    assert aggregate.assistant_internal_only_response is False


def test_aggregate_execution_results_keeps_planning_error_when_no_valid_contract_exists():
    planning_error = TaskContract(
        objective="Find sources",
        task_type="planning_error",
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={
            PLANNER_METADATA_STATUS_FIELD: PLANNER_INVALID_STATUS,
            PLANNER_METADATA_REASON_FIELD: "invalid JSON",
        },
    )

    aggregate = AgentTurnRunner._aggregate_execution_results(
        [ExecutionResult(content="blocked", executed_tool_calls=0, task_contract=planning_error)],
        content="blocked",
    )

    assert aggregate.task_contract is planning_error


class WorkflowAuthorityProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        if _is_planner_call(messages, tools):
            return _planner_response()
        if _is_completion_judge_call(messages, tools):
            return _completion_judge_response(messages)
        latest_user_text = next(
            (str(getattr(message, "content", "") or "") for message in reversed(messages) if getattr(message, "role", None) == "user"),
            "",
        )
        tool_names = [tool.get("function", {}).get("name") for tool in (tools or []) if isinstance(tool, dict)]
        if "run_workflow" in tool_names and not any(getattr(message, "role", None) == "tool" for message in messages):
            return LLMResponse(
                content="run workflow",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="run_workflow",
                        arguments={"workflow": "implement_then_review", "task": latest_user_text},
                    )
                ],
            )
        if "run_workflow" in tool_names:
            return LLMResponse(content="Here is the reviewed outcome.", model="fake-model")
        if "Review the current workspace changes" in latest_user_text:
            return LLMResponse(content=_fake_clean_review_response(), model="fake-model")
        return LLMResponse(content="Implemented the requested change.", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


class FakeStorage:
    def __init__(self):
        self.saved = []

    async def get_messages(self, session_id, limit=None):
        return []

    async def add_message(self, session_id, message: StoredMessage):
        self.saved.append((session_id, message.role, message.content, dict(message.metadata)))

    async def clear_messages(self, session_id):
        return None

    async def get_consolidated_index(self, session_id):
        return 0

    async def set_consolidated_index(self, session_id, index):
        return None

    async def get_all_sessions(self):
        return []


class HistoryStorage(FakeStorage):
    def __init__(self, messages):
        super().__init__()
        self.messages = list(messages)

    async def get_messages(self, session_id, limit=None):
        if limit is None:
            return list(self.messages)
        return list(self.messages[-limit:])


class FakeBus:
    def __init__(self):
        self.inbound: list[InboundMessage] = []
        self.outbound: list[OutboundMessage] = []

    async def publish_inbound(self, message: InboundMessage) -> None:
        self.inbound.append(message)

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


def test_verification_tool_error_uses_structured_status():
    assert _verification_result_is_tool_error({"status": "failed"}) is True
    assert _verification_result_is_tool_error({"status": "timed_out"}) is True
    assert _verification_result_is_tool_error({"status": "error"}) is True
    assert _verification_result_is_tool_error({"status": "skipped"}) is False
    assert _verification_result_is_tool_error({"status": "unknown"}) is False
    assert _verification_result_is_tool_error({"status": "passed"}) is False


def _image_data_url(payload: bytes, mime_type: str = "image/png") -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _media_data_url(payload: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_curator_skill_snapshot_is_session_scoped(tmp_path):
    workspace = tmp_path / "workspace"
    session_a_skills = get_session_skills_dir("web:browser-a", workspace_root=workspace)
    session_b_skills = get_session_skills_dir("web:browser-b", workspace_root=workspace)
    skill_dir = session_a_skills / "session-a-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("session a only", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(DummyTool())

    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(workspace),
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    assert agent._read_skill_snapshot("web:browser-a")
    assert agent._read_skill_snapshot("web:browser-b") == ""


def test_agent_loop_uses_configured_continuation_budgets(tmp_path):
    config = Config.load_agent_template_config(
        auto_continue_default_budget=2,
        auto_continue_long_running_budget=6,
        auto_continue_deterministic_action_budget=7,
    )

    agent = AgentLoop(
        config=config,
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    assert agent.auto_continue.max_auto_continues == 2
    assert agent.auto_continue.max_deterministic_actions == 7
    assert agent.work_progress.default_continuation_budget == 2
    assert agent.work_progress.long_running_continuation_budget == 6


def test_agent_goal_command_persists_resumable_work_state(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        context_builder = FakeContextBuilder(tmp_path / "workspace")
        context_builder.app_home = tmp_path / "app_home"
        context_builder.tool_workspace = tmp_path / "workspace"
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
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

        await storage.create_run("web:browser-1", "run-1")
        session_token = agent._current_session_id.set("web:browser-1")
        channel_token = agent._current_channel.set("web")
        transport_token = agent._current_external_chat_id.set("browser-1")
        run_token = agent._current_run_id.set("run-1")
        try:
            rendered = await agent.set_goal_from_text("web:browser-1", "Finish phase two and run tests.")
        finally:
            agent._current_run_id.reset(run_token)
            agent._current_external_chat_id.reset(transport_token)
            agent._current_channel.reset(channel_token)
            agent._current_session_id.reset(session_token)
        return (
            rendered,
            await storage.get_work_state("web:browser-1"),
            await storage.get_run_events("web:browser-1", "run-1"),
        )

    rendered, work_state, events = asyncio.run(scenario())

    assert rendered is not None
    assert "- Goal: Finish phase two and run tests." in rendered
    assert work_state is not None
    assert work_state.objective == "Finish phase two and run tests."
    assert work_state.status == "active"
    assert work_state.long_running is True
    assert work_state.metadata["source"] == "goal_command"
    assert work_state.resume_hint.startswith("Resume at current step:")
    assert [event.event_type for event in events] == [ACTIVE_TASK_COMMAND_APPLIED_EVENT]
    assert events[0].payload["command"] == "set_goal"
    assert events[0].payload["work_state_created"] is True


def test_agent_goal_intent_uses_explicit_task_kind(tmp_path):
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=FakeProvider(),
        storage=FakeStorage(),
        context_builder=FakeContextBuilder(tmp_path),
        tools=ToolRegistry(),
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    intent = agent._task_intent_for_explicit_goal("Please refactor the agent and run tests.")

    assert intent.kind == "task"
    assert intent.long_running is True
    assert intent.objective == "Please refactor the agent and run tests."


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

        async def fake_call_llm(session_id, current_message, channel=None, user_images=None, allow_tools=True, **kwargs):
            call_order.append(("call_llm", session_id, current_message, channel, list(user_images or [])))
            assert storage.saved[0][1] == "user"
            return ExecutionResult(content="assistant reply", executed_tool_calls=0, used_configure_skill=False)

        async def fake_consolidate(session_id):
            await release_maintenance.wait()
            call_order.append(("memory", session_id))

        async def fake_update_profile(session_id):
            await release_maintenance.wait()
            call_order.append(("profile", session_id))

        async def fake_update_active_task(session_id):
            await release_maintenance.wait()
            call_order.append(("active-task", session_id))

        async def fake_update_recent_summary(session_id):
            await release_maintenance.wait()
            call_order.append(("recent-summary", session_id))

        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_consolidate
        agent._maybe_update_recent_summary = fake_update_recent_summary
        agent._maybe_update_user_profile = fake_update_profile
        agent._maybe_update_active_task = fake_update_active_task

        response = await agent.process(
            UserMessage(
                text="hello",
                channel="telegram",
                external_chat_id="room-1",
                session_id="telegram:room-1",
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
    assert storage.saved[1][3] == {"channel": "telegram", "external_chat_id": "room-1"}
    assert call_order[0] == ("call_llm", "telegram:room-1", "hello", "telegram", ["img1"])
    assert set(call_order[1:]) == {
        ("memory", "telegram:room-1"),
        ("recent-summary", "telegram:room-1"),
        ("profile", "telegram:room-1"),
        ("active-task", "telegram:room-1"),
    }
    assert response.text == "assistant reply"
    assert response.channel == "telegram"
    assert response.session_id == "telegram:room-1"


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
                task_contract=TaskContract(
                    objective="hello",
                    task_type="pure_answer",
                    contract_sources=("test",),
                    harness_profile={"name": "chat", "task_type": "pure_answer"},
                ),
                harness_policy={"name": "chat_guidance_policy"},
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
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="hello",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
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
    assert run.session_id == "web:browser-1"
    assert [event.event_type for event in events] == [
        RUN_STARTED_EVENT,
        TASK_INTENT_DETECTED_EVENT,
        LLM_STATUS_EVENT,
        COMPLETION_GATE_EVALUATED_EVENT,
        WORK_PROGRESS_UPDATED_EVENT,
        HARNESS_CHECKPOINT_RECORDED_EVENT,
        HARNESS_SCORECARD_RECORDED_EVENT,
        RUN_FINISHED_EVENT,
    ]
    assert events[0].payload["status"] == "running"
    assert events[1].payload["kind"] == "task"
    assert events[1].payload["objective"] == "hello"
    assert events[3].payload["status"] == "complete"
    assert events[4].payload["next_action"] == "finalize"
    assert events[5].payload["next_action"] == "finalize"
    assert events[6].payload["profile"]["name"] == "chat"
    assert events[6].payload["completion"]["status"] == "complete"
    assert events[-1].payload["status"] == "completed"
    assert [part.part_type for part in parts] == ["context_compaction", "harness_checkpoint", "harness_scorecard", "assistant_message"]
    assert parts[0].content == "proactive:deterministic:compacted"
    assert parts[0].metadata["messages_before"] == 8
    assert parts[1].metadata["harness_profile"]["name"] == "chat"
    assert parts[1].metadata["next_action"] == "finalize"
    assert parts[2].metadata["profile"]["name"] == "chat"
    assert parts[2].metadata["completion"]["status"] == "complete"
    assert parts[3].content == "assistant reply"
    assert parts[3].metadata["executed_tool_calls"] == 0
    assert parts[3].metadata["context_compactions"] == 1


def test_agent_process_schedules_curator_after_run_finished(tmp_path):
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
            return ExecutionResult(content="assistant reply", executed_tool_calls=0)

        async def fake_maintenance(session_id):
            return None

        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_maintenance
        agent._maybe_update_recent_summary = fake_maintenance
        agent._maybe_update_user_profile = fake_maintenance
        agent._maybe_update_active_task = fake_maintenance

        await agent.process(
            UserMessage(
                text="hello",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                sender_id="user-1",
            )
        )
        await agent.wait_for_background_maintenance()

        run = next(iter(storage._runs.values()))
        return await storage.get_run_events("web:browser-1", run.run_id)

    events = asyncio.run(scenario())
    event_types = [event.event_type for event in events]

    assert RUN_FINISHED_EVENT in event_types
    assert CURATOR_STARTED_EVENT in event_types
    assert event_types.index(RUN_FINISHED_EVENT) < event_types.index(CURATOR_STARTED_EVENT)


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
            external_chat_id="browser-1",
            session_id="web:browser-1",
            run_id="run-1",
            enabled=True,
        )
        after = agent._make_tool_result_hook(
            channel="web",
            external_chat_id="browser-1",
            session_id="web:browser-1",
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
        TOOL_STARTED_EVENT,
        VERIFICATION_STARTED_EVENT,
        TOOL_RESULT_EVENT,
        VERIFICATION_RESULT_EVENT,
    ]
    assert [event.event_type for event in bus_events] == [event.event_type for event in stored_events]
    assert stored_events[1].payload == {"action": "python_compile", "path": "src"}
    assert stored_events[-1].payload["ok"] is True
    assert stored_events[-1].payload[VERIFICATION_STATUS_METADATA_FIELD] == "passed"
    assert stored_events[-1].payload[VERIFICATION_NAME_METADATA_FIELD] == "python_compile"
    assert stored_events[0].payload["state"] == "running"
    assert stored_events[0].payload["started_at"] > 0
    assert stored_events[2].payload["state"] == "completed"
    assert stored_events[2].payload["started_at"] == stored_events[0].payload["started_at"]
    assert stored_events[2].payload["finished_at"] >= stored_events[2].payload["started_at"]
    assert stored_events[2].payload["duration_ms"] >= 0
    assert [part.part_type for part in stored_parts] == ["tool_call", "tool_result"]
    assert [part.tool_name for part in stored_parts] == ["verify", "verify"]
    assert stored_parts[0].metadata["args"] == {"action": "python_compile", "path": "src"}
    assert stored_parts[0].metadata["state"] == "running"
    assert stored_parts[0].metadata["started_at"] == stored_events[0].payload["started_at"]
    assert stored_parts[1].metadata["ok"] is True
    assert stored_parts[1].metadata["state"] == "completed"
    assert stored_parts[1].metadata["duration_ms"] >= 0
    assert stored_parts[1].content == "Verification passed: python_compile"


def test_agent_tool_result_hook_marks_error_executing_results_failed(tmp_path):
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
        await storage.create_run("web:browser-1", "run-1")
        after = agent._make_tool_result_hook(
            channel="web",
            external_chat_id="browser-1",
            session_id="web:browser-1",
            run_id="run-1",
            enabled=True,
        )

        await after(
            "web_fetch",
            {"url": "https://example.test/missing"},
            tool_error_result(
                "HTTP Error: 404 Not Found",
                error_type="ToolExecutionError",
                metadata={"tool_name": "web_fetch"},
            ),
        )

        return await storage.get_run_events("web:browser-1", "run-1"), await storage.get_run_parts("web:browser-1", "run-1")

    stored_events, stored_parts = asyncio.run(scenario())

    assert stored_events[-1].event_type == TOOL_RESULT_EVENT
    assert stored_events[-1].payload["ok"] is False
    assert stored_events[-1].payload["state"] == "error"
    assert stored_events[-1].payload["error"] == "HTTP Error: 404 Not Found"
    assert stored_events[-1].payload["error_type"] == "ToolExecutionError"
    assert stored_events[-1].payload["status_code"] == 404
    assert stored_parts[-1].metadata["ok"] is False
    assert stored_parts[-1].metadata["state"] == "error"
    assert stored_parts[-1].metadata["error"] == "HTTP Error: 404 Not Found"
    assert stored_parts[-1].metadata["error_type"] == "ToolExecutionError"
    assert stored_parts[-1].metadata["status_code"] == 404


def test_agent_tool_result_hook_marks_structured_json_error_failed(tmp_path):
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
        await storage.create_run("web:browser-1", "run-1")
        after = agent._make_tool_result_hook(
            channel="web",
            external_chat_id="browser-1",
            session_id="web:browser-1",
            run_id="run-1",
            enabled=True,
        )
        result = json.dumps({"type": "web_search", "ok": False, "error": "Search failed"})

        await after("web_search", {"query": "Qwen"}, result)

        return await storage.get_run_events("web:browser-1", "run-1"), await storage.get_run_parts("web:browser-1", "run-1")

    stored_events, stored_parts = asyncio.run(scenario())

    assert stored_events[-1].payload["ok"] is False
    assert stored_events[-1].payload["state"] == "error"
    assert stored_events[-1].payload["error"] == "Search failed"
    assert stored_events[-1].payload["error_type"] == "ToolError"
    assert stored_parts[-1].metadata["ok"] is False
    assert stored_parts[-1].metadata["state"] == "error"
    assert stored_parts[-1].metadata["error"] == "Search failed"


def test_agent_tool_result_hook_records_search_trace_metadata(tmp_path):
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
        await storage.create_run("web:browser-1", "run-1")
        after = agent._make_tool_result_hook(
            channel="web",
            external_chat_id="browser-1",
            session_id="web:browser-1",
            run_id="run-1",
            enabled=True,
        )
        result = json.dumps(
            {
                "type": "web_search",
                "ok": False,
                "query": "Qwen latest model 2026",
                "provider": "searxng",
                "backend": "searxng",
                "error": "Client error '403 Forbidden'",
            }
        )

        await after("web_search", {"query": "Qwen latest model 2026"}, result)

        return await storage.get_run_events("web:browser-1", "run-1"), await storage.get_run_parts("web:browser-1", "run-1")

    stored_events, stored_parts = asyncio.run(scenario())

    payload = stored_events[-1].payload
    metadata = stored_parts[-1].metadata
    assert payload["provider"] == "searxng"
    assert payload["backend"] == "searxng"
    assert payload["query"] == "Qwen latest model 2026"
    assert payload["error"] == "Client error '403 Forbidden'"
    assert metadata["provider"] == "searxng"
    assert metadata["backend"] == "searxng"
    assert metadata["search_provider"] == "searxng"
    assert metadata["search_backend"] == "searxng"


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

        session_token = agent._current_session_id.set("web:browser-1")
        channel_token = agent._current_channel.set("web")
        transport_token = agent._current_external_chat_id.set("browser-1")
        run_token = agent._current_run_id.set("run-1")
        try:
            result = await agent.tools.execute(
                "write_file",
                {"path": "notes.txt", "content": "hello\n"},
            )
        finally:
            agent._current_run_id.reset(run_token)
            agent._current_external_chat_id.reset(transport_token)
            agent._current_channel.reset(channel_token)
            agent._current_session_id.reset(session_token)

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
    assert [event.event_type for event in events] == [
        TOOL_PERMISSION_CHECKED_EVENT,
        TOOL_PERMISSION_ALLOWED_EVENT,
        FILE_CHANGED_EVENT,
    ]
    assert events[2].payload["path"] == "notes.txt"


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

        session_token = agent._current_session_id.set("web:browser-1")
        channel_token = agent._current_channel.set("web")
        transport_token = agent._current_external_chat_id.set("browser-1")
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
            agent._current_external_chat_id.reset(transport_token)
            agent._current_channel.reset(channel_token)
            agent._current_session_id.reset(session_token)

        stored_events = await storage.get_run_events("web:browser-1", "run-1")
        bus_events = []
        while bus.run_events_size:
            bus_events.append(await bus.consume_run_event())
        return result, stored_events, bus_events

    result, stored_events, bus_events = asyncio.run(scenario())

    assert result == "ok"
    assert [event.event_type for event in stored_events] == [
        TOOL_PERMISSION_CHECKED_EVENT,
        TOOL_PERMISSION_APPROVAL_REQUIRED_EVENT,
        PERMISSION_REQUESTED_EVENT,
        TOOL_APPROVAL_REQUESTED_EVENT,
        PERMISSION_GRANTED_EVENT,
        TOOL_APPROVAL_APPROVED_EVENT,
    ]
    assert [event.event_type for event in bus_events] == [event.event_type for event in stored_events]
    assert stored_events[2].payload["tool_name"] == "dummy"
    assert stored_events[2].payload["status"] == "pending"
    assert stored_events[4].payload["status"] == "approved"
    assert stored_events[4].payload["resolution_reason"] == "approved once"
    assert stored_events[5].payload["resolution_reason"] == "approved once"


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

        async def fake_maintenance(session_id):
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
                external_chat_id="room-1",
                session_id="telegram:room-1",
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
    saved_image = workspace_root / "sessions" / "telegram" / "room-1" / image_files[0]
    saved_audio = workspace_root / "sessions" / "telegram" / "room-1" / audio_files[0]
    saved_video = workspace_root / "sessions" / "telegram" / "room-1" / video_files[0]

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


def test_agent_process_routes_audio_only_message_to_llm(tmp_path):
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
            media_router=MediaRouter(speech_provider=FakeSpeechProvider()),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        captured = {}

        async def fake_call_llm(
            session_id,
            current_message,
            channel=None,
            user_images=None,
            user_image_files=None,
            user_audio_files=None,
            user_video_files=None,
            allow_tools=True,
            **kwargs,
        ):
            captured.setdefault("current_message", current_message)
            captured.setdefault("current_audios", list(agent._get_current_audios() or []))
            captured.setdefault("user_audio_files", list(user_audio_files or []))
            return ExecutionResult(content="transcript reply", executed_tool_calls=1, used_configure_skill=False)

        async def fake_maintenance(session_id):
            return None

        agent.call_llm = fake_call_llm
        agent._maybe_consolidate_memory = fake_maintenance
        agent._maybe_update_recent_summary = fake_maintenance
        agent._maybe_update_user_profile = fake_maintenance
        agent._maybe_update_active_task = fake_maintenance

        response = await agent.process(
            UserMessage(
                text="",
                channel="telegram",
                external_chat_id="room-1",
                session_id="telegram:room-1",
                audios=[_media_data_url(b"audio-bytes", "audio/ogg")],
                metadata={"audio_kinds": ["voice"]},
            )
        )
        await agent.wait_for_background_maintenance()
        return response, captured, storage

    response, captured, storage = asyncio.run(scenario())

    assert response.text == "transcript reply"
    assert captured["current_message"].startswith("請幫我整理這段語音重點")
    assert "[Uploaded file path(s): audios/inbound-" in captured["current_message"]
    assert captured["current_audios"] == []
    assert captured["user_audio_files"] == []
    assert [entry[1] for entry in storage.saved] == ["user", "assistant"]


def test_agent_process_saves_uploaded_audio_without_pretranscribing(tmp_path):
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
            media_router=MediaRouter(speech_provider=FakeSpeechProvider()),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        async def fail_call_llm(*args, **kwargs):
            raise AssertionError("uploaded audio files without text should not call the LLM")

        async def fake_maintenance(session_id):
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
                external_chat_id="room-1",
                session_id="telegram:room-1",
                audios=[_media_data_url(b"audio-bytes", "audio/mpeg")],
                metadata={"audio_kinds": ["audio"]},
            )
        )
        await agent.wait_for_background_maintenance()
        return response, storage

    response, storage = asyncio.run(scenario())

    assert response.text == "已收到並保存媒體檔案。需要我分析內容時，請直接告訴我要看哪一個檔案。"
    assert storage.saved[0][3]["audio_files"][0].startswith("audios/inbound-")
    assert storage.saved[0][3]["audio_kinds"] == ["audio"]


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
            session_id,
            current_message,
            channel=None,
            user_images=None,
            user_image_files=None,
            user_audio_files=None,
            user_video_files=None,
            allow_tools=True,
            **kwargs,
        ):
            captured.setdefault("current_message", current_message)
            captured.setdefault("user_image_files", list(user_image_files or []))
            captured.setdefault("user_audio_files", list(user_audio_files or []))
            captured.setdefault("user_video_files", list(user_video_files or []))
            return ExecutionResult(content="analysis reply", executed_tool_calls=0, used_configure_skill=False)

        async def fake_maintenance(session_id):
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
                external_chat_id="room-1",
                session_id="telegram:room-1",
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


def test_agent_process_does_not_seed_active_task_from_detected_intent_only(tmp_path):
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
            recent_summary_config=RecentSummaryConfig(**{**Config.load_template_data()["recent_summary"], "enabled": False}),
            **Config.packaged_agent_llm_chat_kwargs(),
        )

        async def fake_execute_messages(*args, **kwargs):
            return ExecutionResult(content="seeded", executed_tool_calls=0)

        agent._execute_messages = fake_execute_messages
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests. Keep the public API stable.",
                channel="telegram",
                external_chat_id="room-1",
                session_id="telegram:room-1",
            )
        )
        store = create_active_task_store(agent.app_home, "telegram:room-1", workspace_root=agent.tool_workspace)
        return store.read_managed_block(), store.read_events()

    task_block, events = asyncio.run(scenario())

    assert "- Status: inactive" in task_block
    assert "- Goal: not set" in task_block
    assert not any(event["event_type"] == "seed" for event in events)


def test_agent_process_emits_task_context_resolved_event(tmp_path):
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

        async def fake_execute_messages(*args, **kwargs):
            return ExecutionResult(content="context resolved", executed_tool_calls=0)

        agent._execute_messages = fake_execute_messages
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )

        run = next(iter(storage._runs.values()))
        return await storage.get_run_events("web:browser-1", run.run_id)

    events = asyncio.run(scenario())

    event = next(event for event in events if event.event_type == TASK_CONTEXT_RESOLVED_EVENT)
    assert event.payload["method"] == "deterministic"
    assert event.payload["is_follow_up"] is False
    assert event.payload["continuation_type"] == "none"
    assert event.payload["confidence"] >= 0.0
    objective_event = next(event for event in events if event.event_type == TASK_OBJECTIVE_RESOLVED_EVENT)
    assert objective_event.payload["method"] == "deterministic"
    assert objective_event.payload["should_use_resolved_objective"] is False


def test_agent_process_emits_completion_gate_needs_verification_after_code_changes(tmp_path):
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
                content="Completed the refactor.",
                executed_tool_calls=0,
                file_change_count=1,
                touched_paths=("src/agent.py",),
            )

        agent.call_llm = fake_call_llm
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )

        run = next(iter(storage._runs.values()))
        return await storage.get_run_events("web:browser-1", run.run_id)

    events = asyncio.run(scenario())

    completion_event = next(event for event in events if event.event_type == COMPLETION_GATE_EVALUATED_EVENT)
    assert completion_event.payload["status"] == "needs_verification"
    assert completion_event.payload["reason"] == "required verification was not recorded"


def test_agent_process_emits_completion_gate_needs_review_after_code_changes_without_review_evidence(tmp_path):
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
                content="Implemented the cleanup successfully.",
                executed_tool_calls=0,
                file_change_count=1,
                touched_paths=("src/cleanup.py",),
            )

        agent.call_llm = fake_call_llm
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        await agent.process(
            UserMessage(
                text="Please implement the cleanup.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )

        run = next(iter(storage._runs.values()))
        return await storage.get_run_events("web:browser-1", run.run_id)

    events = asyncio.run(scenario())

    completion_event = next(event for event in events if event.event_type == COMPLETION_GATE_EVALUATED_EVENT)
    work_progress_event = next(event for event in events if event.event_type == WORK_PROGRESS_UPDATED_EVENT)
    assert completion_event.payload["status"] == "needs_review"
    assert completion_event.payload["reason"] == "delegated review was not recorded for code changes"
    assert completion_event.payload["review_required"] is True
    assert work_progress_event.payload["next_action"] == "collect_review_evidence"


def test_agent_process_workflow_completion_authority_marks_complete_with_clean_review(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=WorkflowAuthorityProvider(),
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
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="Implement a safe change.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )

        run = next(iter(storage._runs.values()))
        events = await storage.get_run_events("web:browser-1", run.run_id)
        return response, events

    response, events = asyncio.run(scenario())

    assert response.text == "Here is the reviewed outcome."
    completion_event = next(event for event in events if event.event_type == COMPLETION_GATE_EVALUATED_EVENT)
    run_finished = next(event for event in events if event.event_type == RUN_FINISHED_EVENT)
    assert completion_event.payload["status"] == "complete"
    assert completion_event.payload["reason"] == "workflow implement_then_review completed with clean review evidence"
    assert run_finished.payload["completion_gate"]["status"] == "complete"


def test_agent_process_auto_continues_once_when_code_changes_are_missing(tmp_path):
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
        calls = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            if len(calls) == 1:
                return ExecutionResult(
                    content="Completed the refactor.",
                    executed_tool_calls=0,
                    task_contract=TaskContract(
                        objective="Please refactor the agent and run tests.",
                        task_type="code_change",
                        requirements=(
                            EvidenceRequirement(kind="file_change", min_count=1),
                            EvidenceRequirement(kind="verification", tool_group="verification", min_count=1),
                        ),
                        allow_no_tool_final=False,
                        contract_sources=("test",),
                        harness_profile={"name": "coding", "task_type": "workspace_change"},
                    ),
                    harness_policy={"name": "workspace_change_guidance_policy"},
                )
            return ExecutionResult(
                content="Verification passed and the refactor is complete.",
                executed_tool_calls=1,
                file_change_count=1,
                touched_paths=("src/opensprite/agent.py",),
                verification_attempted=True,
                verification_passed=True,
                task_contract=TaskContract(
                    objective="Please refactor the agent and run tests.",
                    task_type="code_change",
                    requirements=(
                        EvidenceRequirement(kind="file_change", min_count=1),
                        EvidenceRequirement(kind="verification", tool_group="verification", min_count=1),
                    ),
                    allow_no_tool_final=False,
                    contract_sources=("test",),
                    harness_profile={"name": "coding", "task_type": "workspace_change"},
                ),
                harness_policy={"name": "workspace_change_guidance_policy"},
            )

        agent.call_llm = fake_call_llm
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(storage._runs.values()))
        events = await storage.get_run_events("web:browser-1", run.run_id)
        parts = await storage.get_run_parts("web:browser-1", run.run_id)
        return response, calls, events, parts

    response, calls, events, parts = asyncio.run(scenario())

    assert response.text == "Verification passed and the refactor is complete."
    assert len(calls) == 2
    assert "Completion gate reason: expected code changes were not recorded" in calls[1]
    assert [event.event_type for event in events] == [
        RUN_STARTED_EVENT,
        TASK_INTENT_DETECTED_EVENT,
        LLM_STATUS_EVENT,
        WORK_PLAN_CREATED_EVENT,
        COMPLETION_GATE_EVALUATED_EVENT,
        WORK_PROGRESS_UPDATED_EVENT,
        HARNESS_CHECKPOINT_RECORDED_EVENT,
        HARNESS_SCORECARD_RECORDED_EVENT,
        AUTO_CONTINUE_SCHEDULED_EVENT,
        COMPLETION_GATE_EVALUATED_EVENT,
        WORK_PROGRESS_UPDATED_EVENT,
        HARNESS_CHECKPOINT_RECORDED_EVENT,
        HARNESS_SCORECARD_RECORDED_EVENT,
        AUTO_CONTINUE_COMPLETED_EVENT,
        AUTO_CONTINUE_SKIPPED_EVENT,
        TASK_CHECKLIST_UPDATED_EVENT,
        RUN_FINISHED_EVENT,
    ]
    assert events[4].payload["status"] == "incomplete"
    assert events[4].payload["reason"] == "expected code changes were not recorded"
    assert events[5].payload["next_action"] == "continue_work"
    assert events[9].payload["status"] == "needs_review"
    assert events[9].payload["reason"] == "delegated review was not recorded for code changes"
    assert events[10].payload["next_action"] == "collect_review_evidence"
    assert events[11].payload["next_action"] == "collect_review_evidence"
    assert events[12].payload["completion"]["status"] == "needs_review"
    assert events[13].payload["completion_status"] == "needs_review"
    assert events[14].payload["reason"] == REVIEW_EVIDENCE_STILL_MISSING_REASON
    assert events[-1].payload["status"] == "needs_review"
    assert events[-1].payload["completion_gate"]["status"] == "needs_review"
    assert sum(1 for part in parts if part.part_type == "harness_checkpoint") == 2
    assert sum(1 for part in parts if part.part_type == "harness_scorecard") == 2
    assistant_part = next(part for part in parts if part.part_type == "assistant_message")
    assert assistant_part.metadata["auto_continue_attempts"] == 1
    assert assistant_part.metadata["verification_passed"] is True
    assert assistant_part.metadata["work_progress"]["file_change_count"] == 1
    assert any(part.part_type == "worktree_sandbox" for part in parts)
    assert any(part.part_type == "task_checklist" for part in parts)


def test_agent_process_direct_verify_can_finish_without_second_llm_pass(tmp_path):
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
        calls = []
        verified = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            if len(calls) == 1:
                return ExecutionResult(
                    content="Implemented the refactor.",
                    executed_tool_calls=1,
                    file_change_count=1,
                    touched_paths=("src/agent.py",),
                    delegated_tasks=(
                        StoredDelegatedTask(
                            task_id="task_review",
                            prompt_type="code-reviewer",
                            status="completed",
                            summary="No major findings.",
                            metadata={"structured_output": {"status": "ok", "summary": "No major findings.", "finding_count": 0}},
                        ),
                    ),
                )
            raise AssertionError("LLM should not be called after deterministic verification already completed the task")

        async def fake_run_verify(action, path, pytest_args=()):
            verified.append((action, path, tuple(pytest_args)))
            return ExecutionResult(
                content="Verification passed: pytest\nCommand: python -m pytest",
                executed_tool_calls=1,
                verification_attempted=True,
                verification_passed=True,
            )

        agent.call_llm = fake_call_llm
        agent.turn_runner._run_verify = fake_run_verify
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(storage._runs.values()))
        events = await storage.get_run_events("web:browser-1", run.run_id)
        return response, calls, verified, events

    response, calls, verified, events = asyncio.run(scenario())

    assert response.text == "Verification passed: pytest\nCommand: python -m pytest"
    assert len(calls) == 1
    assert verified == [("pytest", ".", ())]
    scheduled = next(event for event in events if event.event_type == AUTO_CONTINUE_SCHEDULED_EVENT)
    assert scheduled.payload["direct_verify_action"] == "pytest"
    assert scheduled.payload["direct_verify_path"] == "."


def test_agent_process_full_deterministic_review_and_verification_matrix(tmp_path):
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
        calls = []
        resumed = []
        verified = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            if len(calls) == 1:
                return ExecutionResult(
                    content="Workflow cancelled.",
                    executed_tool_calls=1,
                    file_change_count=1,
                    touched_paths=("src/agent.py",),
                    workflow_outcomes=(
                        {
                            "workflow_run_id": "workflow_initial",
                            "workflow": "implement_then_review",
                            "status": "cancelled",
                            "summary": "Workflow stopped after 1/2 completed step(s).",
                            "next_step_id": "review",
                            "next_step_label": "Code review",
                            "next_step_prompt_type": "code-reviewer",
                        },
                    ),
                )
            raise AssertionError("LLM should not be called after deterministic review/verify loop covers the task")

        async def fake_run_workflow(workflow, task, start_step=None):
            resumed.append((workflow, task, start_step))
            run_id = agent.turn_context.current_run_id()
            if start_step == "review":
                agent._record_workflow_outcome(
                    run_id,
                    {
                        "workflow_run_id": "workflow_review_resume",
                        "workflow": workflow,
                        "status": "completed",
                        "completed_steps": 2,
                        "failed_steps": 0,
                        "total_steps": 2,
                        "summary": "Completed 2/2 workflow step(s).",
                        "review_attempted": True,
                        "review_passed": False,
                        "review_finding_count": 1,
                        "review_summary": "One high-risk bug found.",
                        "review_first_finding": "src/foo.py: Null handling bug: Guard the null path before dereference.",
                        "verification_attempted": False,
                        "verification_passed": False,
                    },
                )
                return "Workflow: implement_then_review\nStatus: completed\n[2] code-reviewer | completed"
            agent._record_workflow_outcome(
                run_id,
                {
                    "workflow_run_id": "workflow_fix_resume",
                    "workflow": workflow,
                    "status": "completed",
                    "completed_steps": 2,
                    "failed_steps": 0,
                    "total_steps": 2,
                    "summary": "Completed 2/2 workflow step(s).",
                    "review_attempted": True,
                    "review_passed": True,
                    "review_finding_count": 0,
                    "review_summary": "No major findings.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            )
            return "Workflow: implement_then_review\nStatus: completed\n[1] implementer | completed\n[2] code-reviewer | completed"

        async def fake_run_verify(action, path, pytest_args=()):
            verified.append((action, path, tuple(pytest_args)))
            if len(verified) == 1:
                return ExecutionResult(
                    content=tool_error_result(
                        "Verification failed: pytest\n[stderr] failing test",
                        error_type="VerifyToolError",
                        category="verification_failed",
                        metadata={"tool_name": "verify"},
                    ),
                    executed_tool_calls=1,
                    had_tool_error=True,
                    verification_attempted=True,
                    verification_passed=False,
                )
            return ExecutionResult(
                content="Verification passed: pytest\nCommand: python -m pytest",
                executed_tool_calls=1,
                verification_attempted=True,
                verification_passed=True,
            )

        agent.call_llm = fake_call_llm
        agent.turn_runner._run_workflow = fake_run_workflow
        agent.turn_runner._run_verify = fake_run_verify
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(storage._runs.values()))
        events = await storage.get_run_events("web:browser-1", run.run_id)
        return response, calls, resumed, verified, events

    response, calls, resumed, verified, events = asyncio.run(scenario())

    assert response.text == "Verification passed: pytest\nCommand: python -m pytest"
    assert len(calls) == 1
    assert resumed == [
        ("implement_then_review", "Please refactor the agent and run tests.", "review"),
        ("implement_then_review", "Please refactor the agent and run tests.", "implement"),
    ]
    assert verified == [
        ("pytest", ".", ()),
        ("pytest", ".", ()),
    ]
    scheduled = [event for event in events if event.event_type == AUTO_CONTINUE_SCHEDULED_EVENT]
    assert [event.payload.get("direct_start_step") or event.payload.get("direct_verify_action") for event in scheduled] == [
        "review",
        "implement",
        "pytest",
        "pytest",
    ]


def test_agent_process_stops_auto_continue_when_continuation_has_no_progress(tmp_path):
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
        calls = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            return ExecutionResult(
                content="Completed the refactor.",
                executed_tool_calls=0,
                task_contract=TaskContract(objective="Please refactor the agent and run tests.", task_type="code_change"),
            )

        agent.call_llm = fake_call_llm
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(storage._runs.values()))
        return calls, await storage.get_run_events("web:browser-1", run.run_id)

    calls, events = asyncio.run(scenario())

    assert len(calls) == 2
    assert [event.event_type for event in events].count(AUTO_CONTINUE_SCHEDULED_EVENT) == 1
    skipped = next(event for event in events if event.event_type == AUTO_CONTINUE_SKIPPED_EVENT)
    assert skipped.payload["reason"] == NO_PROGRESS_DURING_CONTINUATION_REASON
    assert skipped.payload["completion_status"] == "incomplete"


def test_agent_process_passes_tool_contract_override_to_auto_continue(tmp_path):
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
        web_contract = TaskContract(
            objective="Find the OpenRouter API base URL and cite the source.",
            task_type="web_research",
            requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research", min_count=1),),
            allow_no_tool_final=False,
            contract_sources=("test",),
            planner_metadata={PLANNER_METADATA_STATUS_FIELD: PLANNER_VALIDATED_STATUS},
            harness_profile={"name": "research", "task_type": "web_research"},
        )
        calls = []
        completion_calls = 0

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return ExecutionResult(
                    content="抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。",
                    executed_tool_calls=1,
                    task_contract=web_contract,
                    task_artifacts=(
                        TaskArtifact(
                            kind="web_source",
                            source_tool="web_research",
                            metadata={
                                "sources": [
                                    {
                                        "title": "OpenRouter docs",
                                        "url": "https://openrouter.ai/docs",
                                        "snippet": "API base URL is https://openrouter.ai/api/v1",
                                        "tool_name": "web_fetch",
                                        "content_chars": 1200,
                                        "has_main_content": True,
                                    }
                                ]
                            },
                        ),
                    ),
                )
            return ExecutionResult(
                content="OpenRouter API base URL is https://openrouter.ai/api/v1. Source: https://openrouter.ai/docs",
                executed_tool_calls=0,
                task_contract=web_contract,
            )

        async def fake_evaluate_with_judge(**kwargs):
            nonlocal completion_calls
            completion_calls += 1
            if completion_calls == 1:
                return CompletionGateResult(
                    status="incomplete",
                    reason="research source was gathered but final answer is missing",
                    missing_evidence=("final answer with cited source",),
                    progress_only_response=True,
                )
            return CompletionGateResult(status="complete", reason="final answer cites gathered source")

        agent.call_llm = fake_call_llm
        agent.completion_gate.evaluate_with_judge = fake_evaluate_with_judge
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        await agent.process(
            UserMessage(
                text="Find the OpenRouter API base URL and cite the source.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        return calls

    calls = asyncio.run(scenario())

    assert len(calls) >= 2
    assert calls[0].get("task_contract_override") is None
    assert calls[1].get("task_contract_override") is not None
    assert calls[1]["task_contract_override"].task_type == "web_research"


def test_agent_process_auto_continue_prompt_uses_workflow_follow_up_detail(tmp_path):
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
        calls = []
        resumed = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            if len(calls) == 1:
                return ExecutionResult(
                    content="Workflow cancelled.",
                    executed_tool_calls=1,
                    workflow_outcomes=(
                        {
                            "workflow_run_id": "workflow_abc123",
                            "workflow": "implement_then_review",
                            "status": "cancelled",
                            "summary": "Workflow stopped after 1/2 completed step(s).",
                            "next_step_id": "review",
                            "next_step_label": "Code review",
                            "next_step_prompt_type": "code-reviewer",
                        },
                    ),
                )
            raise AssertionError("LLM should not be called after a direct workflow resume already completed the task")

        async def fake_run_workflow(workflow, task, start_step=None):
            resumed.append((workflow, task, start_step))
            run_id = agent.turn_context.current_run_id()
            agent._record_workflow_outcome(
                run_id,
                {
                    "workflow_run_id": "workflow_resume123",
                    "workflow": workflow,
                    "status": "completed",
                    "completed_steps": 2,
                    "failed_steps": 0,
                    "total_steps": 2,
                    "summary": "Completed 2/2 workflow step(s).",
                    "review_attempted": True,
                    "review_passed": True,
                    "review_finding_count": 0,
                    "review_summary": "No major findings.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            )
            return "Workflow: implement_then_review\nStatus: completed\n[2] code-reviewer | completed"

        agent.call_llm = fake_call_llm
        agent.turn_runner._run_workflow = fake_run_workflow
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="Please implement the cleanup.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(storage._runs.values()))
        events = await storage.get_run_events("web:browser-1", run.run_id)
        return response, calls, resumed, events

    response, calls, resumed, events = asyncio.run(scenario())

    assert response.text == "Workflow: implement_then_review\nStatus: completed\n[2] code-reviewer | completed"
    assert resumed == [("implement_then_review", "Please implement the cleanup.", "review")]
    assert len(calls) == 1
    scheduled = next(event for event in events if event.event_type == AUTO_CONTINUE_SCHEDULED_EVENT)
    assert scheduled.payload["direct_workflow"] == "implement_then_review"
    assert scheduled.payload["direct_start_step"] == "review"


def test_agent_process_can_chain_changed_workflow_follow_up_targets(tmp_path):
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
        calls = []
        resumed = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            if len(calls) == 1:
                return ExecutionResult(
                    content="Workflow cancelled.",
                    executed_tool_calls=1,
                    workflow_outcomes=(
                        {
                            "workflow_run_id": "workflow_initial",
                            "workflow": "implement_then_review",
                            "status": "cancelled",
                            "summary": "Workflow stopped after 1/2 completed step(s).",
                            "next_step_id": "review",
                            "next_step_label": "Code review",
                            "next_step_prompt_type": "code-reviewer",
                        },
                    ),
                )
            raise AssertionError("LLM should not be called after direct workflow resumes provide enough evidence")

        async def fake_run_workflow(workflow, task, start_step=None):
            resumed.append((workflow, task, start_step))
            run_id = agent.turn_context.current_run_id()
            if start_step == "review":
                agent._record_workflow_outcome(
                    run_id,
                    {
                        "workflow_run_id": "workflow_review_resume",
                        "workflow": workflow,
                        "status": "completed",
                        "completed_steps": 2,
                        "failed_steps": 0,
                        "total_steps": 2,
                        "summary": "Completed 2/2 workflow step(s).",
                        "review_attempted": True,
                        "review_passed": False,
                        "review_finding_count": 1,
                        "review_summary": "One high-risk bug found.",
                        "review_first_finding": "src/foo.py: Null handling bug: Guard the null path before dereference.",
                        "verification_attempted": False,
                        "verification_passed": False,
                    },
                )
                return "Workflow: implement_then_review\nStatus: completed\n[2] code-reviewer | completed"
            agent._record_workflow_outcome(
                run_id,
                {
                    "workflow_run_id": "workflow_fix_resume",
                    "workflow": workflow,
                    "status": "completed",
                    "completed_steps": 2,
                    "failed_steps": 0,
                    "total_steps": 2,
                    "summary": "Completed 2/2 workflow step(s).",
                    "review_attempted": True,
                    "review_passed": True,
                    "review_finding_count": 0,
                    "review_summary": "No major findings.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            )
            return "Workflow: implement_then_review\nStatus: completed\n[1] implementer | completed\n[2] code-reviewer | completed"

        agent.call_llm = fake_call_llm
        agent.turn_runner._run_workflow = fake_run_workflow
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="Please implement the cleanup.",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        run = next(iter(storage._runs.values()))
        events = await storage.get_run_events("web:browser-1", run.run_id)
        return response, calls, resumed, events

    response, calls, resumed, events = asyncio.run(scenario())

    assert response.text == "Workflow: implement_then_review\nStatus: completed\n[1] implementer | completed\n[2] code-reviewer | completed"
    assert resumed == [
        ("implement_then_review", "Please implement the cleanup.", "review"),
        ("implement_then_review", "Please implement the cleanup.", "implement"),
    ]
    assert len(calls) == 1
    scheduled = [event for event in events if event.event_type == AUTO_CONTINUE_SCHEDULED_EVENT]
    assert [event.payload.get("direct_start_step") for event in scheduled] == ["review", "implement"]


def test_agent_process_metadata_resume_follow_up_runs_workflow_before_llm(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        await storage.upsert_work_state(
            StoredWorkState(
                session_id="web:browser-1",
                objective="Please implement the cleanup.",
                kind="implementation",
                status="active",
                steps=("1. inspect relevant code", "2. make the smallest correct change", "3. review the result and finalize"),
                constraints=(),
                done_criteria=("requested work is complete",),
                long_running=True,
                coding_task=True,
                expects_code_change=True,
                expects_verification=False,
            )
        )
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
        calls = []
        resumed = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            raise AssertionError("LLM should not be called when quick-action workflow resume already completes")

        async def fake_run_workflow(workflow, task, start_step=None):
            resumed.append((workflow, task, start_step))
            run_id = agent.turn_context.current_run_id()
            agent._record_workflow_outcome(
                run_id,
                {
                    "workflow_run_id": "workflow_resume_ui",
                    "workflow": workflow,
                    "status": "completed",
                    "completed_steps": 2,
                    "failed_steps": 0,
                    "total_steps": 2,
                    "summary": "Completed 2/2 workflow step(s).",
                    "review_attempted": True,
                    "review_passed": True,
                    "review_finding_count": 0,
                    "review_summary": "No major findings.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            )
            return "Workflow: implement_then_review\nStatus: completed\n[2] code-reviewer | completed"

        agent.call_llm = fake_call_llm
        agent.turn_runner._run_workflow = fake_run_workflow
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="continue",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                metadata={
                    "quick_action": " resume_follow_up ",
                    "follow_up_workflow": " implement_then_review ",
                    "follow_up_step_id": " review ",
                    "follow_up_step_label": " Code review ",
                    "follow_up_prompt_type": " code-reviewer ",
                    "active_task_detail": " Resume with the Code review step in implement_then_review. ",
                },
            )
        )

        return response, calls, resumed

    response, calls, resumed = asyncio.run(scenario())

    assert response.text == "Workflow: implement_then_review\nStatus: completed\n[2] code-reviewer | completed"
    assert resumed == [("implement_then_review", "Please implement the cleanup.", "review")]
    assert len(calls) == 0


def test_agent_process_metadata_run_verification_runs_verify_before_llm(tmp_path):
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
        calls = []
        verified = []

        async def fake_call_llm(session_id, current_message, **kwargs):
            calls.append(current_message)
            raise AssertionError("LLM should not be called when quick-action verification already completes")

        async def fake_run_verify(action, path, pytest_args=()):
            verified.append((action, path, tuple(pytest_args)))
            return ExecutionResult(
                content="Verification passed: pytest\nCommand: python -m pytest tests/test_ui.py::test_card",
                executed_tool_calls=1,
                verification_attempted=True,
                verification_passed=True,
            )

        agent.call_llm = fake_call_llm
        agent.turn_runner._run_verify = fake_run_verify
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        response = await agent.process(
            UserMessage(
                text="continue",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
                metadata={
                    "quick_action": " run_verification ",
                    "verification_action": " pytest ",
                    "verification_path": " . ",
                    "verification_pytest_args": [" tests/test_ui.py::test_card "],
                },
            )
        )

        return response, calls, verified

    response, calls, verified = asyncio.run(scenario())

    assert response.text == "Verification passed: pytest\nCommand: python -m pytest tests/test_ui.py::test_card"
    assert len(calls) == 0
    assert verified == [("pytest", ".", ("tests/test_ui.py::test_card",))]


def test_agent_process_marks_active_task_done_when_completion_gate_completes(tmp_path):
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
            "- Goal: Finish cleanup\n"
            "- Deliverable: cleanup\n"
            "- Definition of done:\n"
            "  - done\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. cleanup\n"
            "- Current step: 1. cleanup\n"
            "- Next step: not set\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        )

        async def fake_call_llm(*args, **kwargs):
            return ExecutionResult(
                content="Implemented the final cleanup successfully.",
                executed_tool_calls=0,
                file_change_count=1,
                touched_paths=("src/cleanup.py",),
                delegated_tasks=(
                    StoredDelegatedTask(
                        task_id="task_review",
                        prompt_type="code-reviewer",
                        status="completed",
                        summary="No major findings.",
                        metadata={
                            "structured_output": {
                                "status": "ok",
                                "summary": "No major findings.",
                                "finding_count": 0,
                            }
                        },
                    ),
                ),
            )

        agent.call_llm = fake_call_llm
        await agent.process(
            UserMessage(
                text="Please implement the final cleanup.",
                channel="telegram",
                external_chat_id="room-1",
                session_id="telegram:room-1",
            )
        )
        return store.read_managed_block(), store.read_events()

    task_block, events = asyncio.run(scenario())

    assert "- Status: done" in task_block
    assert any(event["event_type"] == "work_progress" for event in events)


def test_agent_process_updates_active_task_with_verification_step_when_work_remains(tmp_path):
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
            "- Goal: Finish refactor\n"
            "- Deliverable: merged refactor\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "  2. change\n"
            "  3. verify\n"
            "- Current step: 2. change\n"
            "- Next step: 3. verify\n"
            "- Completed steps:\n"
            "  - inspect\n"
            "- Open questions:\n"
            "  - none"
        )

        async def fake_call_llm(*args, **kwargs):
            return ExecutionResult(
                content="Completed the refactor.",
                executed_tool_calls=1,
                file_change_count=1,
                touched_paths=("src/agent.py",),
                task_contract=TaskContract(
                    objective="Please refactor the agent and run tests.",
                    task_type="code_change",
                    requirements=(
                        EvidenceRequirement(kind="file_change", min_count=1),
                        EvidenceRequirement(kind="verification", tool_group="verification", min_count=1),
                    ),
                    allow_no_tool_final=False,
                    contract_sources=("test",),
                    harness_profile={"name": "coding", "task_type": "workspace_change"},
                ),
                harness_policy={"name": "workspace_change_guidance_policy"},
            )

        agent.call_llm = fake_call_llm
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None
        await agent.process(
            UserMessage(
                text="Please refactor the agent and run tests.",
                channel="telegram",
                external_chat_id="room-1",
                session_id="telegram:room-1",
            )
        )
        return store.read_managed_block(), store.read_events()

    task_block, events = asyncio.run(scenario())

    assert "- Status: active" in task_block
    assert "- Current step: 3. run focused verification or state the verification gap" in task_block
    progress_event = next(event for event in reversed(events) if event["event_type"] == "work_progress")
    assert progress_event["details"]["next_action"] == "stop_budget_exhausted"


def test_agent_process_persists_work_state_with_delegate_task(tmp_path):
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
                content="Delegated the implementation task.",
                executed_tool_calls=1,
                delegated_tasks=(
                    StoredDelegatedTask(
                        task_id="task_abc12345",
                        prompt_type="implementer",
                        status="completed",
                        selected=True,
                        summary="Delegated the implementation task.",
                    ),
                ),
            )

        agent.call_llm = fake_call_llm
        agent._schedule_curator = lambda session_id, run_id, channel, external_chat_id, result: None

        await storage.upsert_work_state(
            StoredWorkState(
                session_id="web:browser-1",
                objective="Finish the refactor",
                kind="task",
                status="active",
                steps=("1. inspect", "2. change", "3. verify"),
                constraints=("Keep the public API stable",),
                done_criteria=("tests pass",),
                long_running=True,
                coding_task=True,
                expects_code_change=True,
                expects_verification=True,
            )
        )
        await agent.process(
            UserMessage(
                text="continue",
                channel="web",
                external_chat_id="browser-1",
                session_id="web:browser-1",
            )
        )
        return await storage.get_work_state("web:browser-1")

    work_state = asyncio.run(scenario())

    assert work_state is not None
    assert work_state.objective == "Finish the refactor"
    assert work_state.active_delegate_task_id == "task_abc12345"
    assert work_state.active_delegate_prompt_type == "implementer"
    assert [task.task_id for task in work_state.delegated_tasks] == ["task_abc12345"]
    assert work_state.resume_hint == "Resume at current step: 2. change"


def test_agent_call_llm_uses_read_only_registry_for_planning_contract(tmp_path):
    async def scenario():
        class PlanningProvider(FakeProvider):
            async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
                if _is_planner_call(messages, tools):
                    return _planner_response("planning")
                if _is_completion_judge_call(messages, tools):
                    return _completion_judge_response(messages)
                raise AssertionError("provider.chat should only be called by the planner in this test")

        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=PlanningProvider(),
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
        captured: dict[str, object] = {}

        async def fake_execute_messages(*args, **kwargs):
            registry = kwargs.get("tool_registry")
            captured["tool_names"] = list(registry.tool_names) if registry is not None else None
            return ExecutionResult(content="planning reply", executed_tool_calls=0)

        agent._execute_messages = fake_execute_messages
        message = "先規劃不要動手，幫我整理修復方案"
        await agent.call_llm(
            "web:browser-1",
            message,
            channel="web",
            allow_tools=True,
            task_intent=agent.task_intents.classify(message),
        )
        return captured

    captured = asyncio.run(scenario())

    assert captured["tool_names"] is not None
    tool_names = set(captured["tool_names"])
    assert "read_file" in tool_names
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "apply_patch" not in tool_names
    assert "exec" not in tool_names
    assert "verify" not in tool_names
    assert "delegate" not in tool_names


def test_agent_call_llm_returns_to_normal_registry_after_planning_contract(tmp_path):
    async def scenario():
        class SequencedPlannerProvider(FakeProvider):
            def __init__(self):
                self.task_types = ["planning", "code_change"]

            async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
                if _is_planner_call(messages, tools):
                    task_type = self.task_types.pop(0) if self.task_types else "code_change"
                    return _planner_response(task_type)
                if _is_completion_judge_call(messages, tools):
                    return _completion_judge_response(messages)
                raise AssertionError("provider.chat should only be called by the planner in this test")

        storage = MemoryStorage()
        agent = AgentLoop(
            config=Config.load_agent_template_config(),
            provider=SequencedPlannerProvider(),
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
        captured: list[set[str]] = []

        async def fake_execute_messages(*args, **kwargs):
            registry = kwargs.get("tool_registry")
            if registry is None:
                captured.append(set(agent.tools.tool_names))
            else:
                captured.append(set(registry.tool_names))
            return ExecutionResult(content="reply", executed_tool_calls=0)

        agent._execute_messages = fake_execute_messages
        planning_message = "先規劃不要動手，幫我整理修復方案"
        await agent.call_llm(
            "web:browser-1",
            planning_message,
            channel="web",
            allow_tools=True,
            task_intent=agent.task_intents.classify(planning_message),
        )
        build_message = "好，現在請直接修掉 tests/test_app.py 的問題"
        await agent.call_llm(
            "web:browser-1",
            build_message,
            channel="web",
            allow_tools=True,
            task_intent=agent.task_intents.classify(build_message),
        )
        return captured

    captured = asyncio.run(scenario())

    assert len(captured) == 2
    planning_tools, normal_tools = captured
    assert "write_file" not in planning_tools
    assert "exec" not in planning_tools
    assert "verify" not in planning_tools
    assert "write_file" in normal_tools
    assert "edit_file" in normal_tools
    assert "apply_patch" in normal_tools
    assert "exec" in normal_tools
    assert "verify" in normal_tools


def test_agent_process_rejects_overlapping_runs_for_same_session(tmp_path):
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
        blocker = asyncio.Event()

        async def fake_execute_messages(*args, **kwargs):
            await blocker.wait()
            return ExecutionResult(content="done", executed_tool_calls=0)

        agent._execute_messages = fake_execute_messages
        first = asyncio.create_task(
            agent.process(
                UserMessage(
                    text="Please implement the change.",
                    channel="web",
                    external_chat_id="browser-1",
                    session_id="web:browser-1",
                )
            )
        )
        for _ in range(100):
            if agent.get_active_run("web:browser-1") is not None:
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("active run was not registered")

        try:
            await agent.process(
                UserMessage(
                    text="Please implement another change.",
                    channel="web",
                    external_chat_id="browser-1",
                    session_id="web:browser-1",
                )
            )
        except RunBusyError:
            pass
        else:
            raise AssertionError("RunBusyError was not raised")
        blocker.set()
        await first

    asyncio.run(scenario())


def test_agent_process_cancel_request_marks_run_cancelled_and_clears_active_run(tmp_path):
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

        async def fake_execute_messages(*args, **kwargs):
            should_cancel = kwargs.get("should_cancel")
            for _ in range(200):
                if callable(should_cancel) and should_cancel():
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.001)
            return ExecutionResult(content="done", executed_tool_calls=0)

        agent._execute_messages = fake_execute_messages
        task = asyncio.create_task(
            agent.process(
                UserMessage(
                    text="Please implement the change.",
                    channel="web",
                    external_chat_id="browser-1",
                    session_id="web:browser-1",
                )
            )
        )
        for _ in range(100):
            active = agent.get_active_run("web:browser-1")
            if active is not None:
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("active run was not registered")

        accepted = await agent.request_run_cancel(
            "web:browser-1",
            active.run_id,
            channel="web",
            external_chat_id="browser-1",
        )
        assert accepted is True

        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("process task was not cancelled")

        run = await storage.get_run("web:browser-1", active.run_id)
        events = await storage.get_run_events("web:browser-1", active.run_id)
        return run, events, agent.get_active_run("web:browser-1")

    run, events, active = asyncio.run(scenario())

    assert run is not None
    assert run.status == "cancelled"
    assert [event.event_type for event in events][-2:] == ["run_cancel_requested", "run_cancelled"]
    assert active is None


def test_agent_process_cancel_request_kills_owned_background_sessions(tmp_path):
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
        session_ids: list[str] = []

        async def fake_execute_messages(*args, **kwargs):
            exec_tool = agent.tools.get("exec")
            assert exec_tool is not None
            started = await exec_tool.execute(
                command=_python_shell_command(
                    "import time; print('owned background', flush=True); time.sleep(5)"
                ),
                background=True,
                timeout_seconds=5,
                notify_on_exit=False,
            )
            session_ids.append(_extract_session_id(started))
            should_cancel = kwargs.get("should_cancel")
            for _ in range(200):
                if callable(should_cancel) and should_cancel():
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.01)
            return ExecutionResult(content="done", executed_tool_calls=1)

        agent._execute_messages = fake_execute_messages
        task = asyncio.create_task(
            agent.process(
                UserMessage(
                    text="Please implement the change.",
                    channel="web",
                    external_chat_id="browser-1",
                    session_id="web:browser-1",
                )
            )
        )
        for _ in range(100):
            active = agent.get_active_run("web:browser-1")
            if active is not None and session_ids:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("active run or background session was not registered")

        accepted = await agent.request_run_cancel(
            "web:browser-1",
            active.run_id,
            channel="web",
            external_chat_id="browser-1",
        )
        assert accepted is True

        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("process task was not cancelled")

        assert agent.background_process_manager is not None
        session = await agent.background_process_manager.get_session(session_ids[0])
        events = await storage.get_run_events("web:browser-1", active.run_id)
        return session, events

    session, events = asyncio.run(scenario())

    assert session is not None
    assert session.state == "exited"
    assert session.termination_reason == "killed"
    assert session.owner_session_id == "web:browser-1"
    assert session.owner_run_id is not None
    cancel_event = next(event for event in events if event.event_type == "run_cancel_requested")
    assert cancel_event.payload["owned_background_sessions_cancelled"] == 1
    assert len(cancel_event.payload["owned_background_session_ids"]) == 1


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

        async def fake_call_llm(session_id, current_message, channel=None, user_images=None, allow_tools=True, **kwargs):
            assert agent._queue_outbound_media("image", "img-out") is None
            assert agent._queue_outbound_media("voice", "voice-out") is None
            assert agent._queue_outbound_media("audio", "audio-out") is None
            assert agent._queue_outbound_media("video", "video-out") is None
            return ExecutionResult(content="sending media", executed_tool_calls=1, used_configure_skill=False)

        async def fake_maintenance(session_id):
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
                external_chat_id="room-1",
                session_id="telegram:room-1",
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
                external_chat_id="room-1",
                session_id="telegram:room-1",
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
                external_chat_id="room-1",
                session_id="telegram:room-1",
            )
        )
        return store.read_managed_block()

    task_block = asyncio.run(scenario())

    assert "- Status: blocked" in task_block
    assert "目前無法繼續，測試環境失敗。" in task_block


def test_background_session_exit_notifier_queues_agent_summary_request(tmp_path):
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

    session_token = agent._current_session_id.set("telegram:room-1")
    channel_token = agent._current_channel.set("telegram")
    transport_token = agent._current_external_chat_id.set("room-1")
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
        agent._current_external_chat_id.reset(transport_token)
        agent._current_channel.reset(channel_token)
        agent._current_session_id.reset(session_token)

    assert fake_bus.outbound == []
    assert len(fake_bus.inbound) == 1
    inbound = fake_bus.inbound[0]
    assert inbound.channel == "telegram"
    assert inbound.external_chat_id == "room-1"
    assert inbound.session_id == "telegram:room-1"
    assert inbound.sender_id == "system:background"
    assert "A managed background process has finished." in inbound.content
    assert "Session ID: bg123" in inbound.content
    assert "Command: python job.py" in inbound.content
    assert "job done" in inbound.content
    assert inbound.metadata["kind"] == "background_session_summary_request"
    assert inbound.metadata["_bypass_commands"] is True
    assert storage.saved == []


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
        tool_result_session_id=None,
        tool_registry=None,
        on_tool_before_execute=None,
        on_tool_after_execute=None,
        on_llm_status=None,
        on_response_delta=None,
        on_tool_input_delta=None,
        on_reasoning_delta=None,
        refresh_system_prompt=None,
        max_tool_iterations=None,
        should_cancel=None,
        work_state_summary="",
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
        session_id="telegram:room-1",
    )

    assert history == []
    assert base_tokens > 0
    assert history_tokens == 0
    assert final_tokens == base_tokens


def test_effective_context_budget_uses_model_window_and_manual_cap(tmp_path):
    chat_kwargs = Config.packaged_agent_llm_chat_kwargs()
    chat_kwargs["llm_output_reserve_tokens"] = 200
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
        session_id="telegram:room-1",
        tool_schema_tokens=0,
    )
    kept_with_tools, _, _, _ = agent._trim_history_to_token_budget(
        history=[{"role": "assistant", "content": "recent message"}],
        current_message="hello",
        channel="telegram",
        session_id="telegram:room-1",
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
                external_chat_id="room-1",
                session_id="telegram:room-1",
                sender_id="user-1",
                sender_name="alice",
            )
        )
    )

    assert response.text == "請先設定 LLM"
    assert [entry[1] for entry in storage.saved] == ["user", "assistant"]
    assert storage.saved[1][2] == "請先設定 LLM"
