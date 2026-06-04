"""
Integration test: main agent call_llm feeds the same system prompt as FileContextBuilder
into the LLM provider (full string), not only fragments in unit tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from opensprite.agent.agent import AgentLoop
from opensprite.config.schema import (
    AgentConfig,
    Config,
    LogConfig,
    MemoryConfig,
    SearchConfig,
    ToolsConfig,
    UserProfileConfig,
)
from opensprite.context.file_builder import FileContextBuilder
from opensprite.context.paths import sync_templates
from opensprite.documents.active_task import TASK_BOUNDARY_CONFIRMATION_EVENT, create_active_task_store
from opensprite.agent.llm_call import _effective_task_intent
from opensprite.agent.task_intent import TaskIntentService
from opensprite.agent.task_objective_resolver import TaskObjectiveDecision
from opensprite.llms.base import LLMResponse
from opensprite.search.base import SearchHit
from opensprite.storage.base import StoredMessage
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class CapturingProvider:
    """Records chat() messages so tests can inspect the full prompt sent to the model."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        if _is_task_contract_planner_call(messages):
            return _task_contract_response(messages)
        self.calls.append(list(messages))
        return LLMResponse(content="done", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


class TaskContextDecisionProvider(CapturingProvider):
    """Returns a task-context JSON decision before the main assistant response."""

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        if _is_task_contract_planner_call(messages):
            return _task_contract_response(messages)
        self.calls.append(list(messages))
        system_text = str(getattr(messages[0], "content", "") or "") if messages else ""
        if "You classify whether the latest user turn inherits task context" in system_text:
            return LLMResponse(
                content=(
                    '{"is_follow_up": false, "should_inherit_active_task": false, '
                    '"should_seed_active_task": true, "should_replace_active_task": true, '
                    '"inherited_task_type": null, "inherited_tool_group": null, '
                    '"confidence": 0.88, "reason": "new concrete task should replace the current task"}'
                ),
                model="fake-model",
            )
        if "You resolve a concise task objective for ACTIVE_TASK" in system_text:
            return LLMResponse(
                content=(
                    '{"resolved_objective": "Fix tests/test_app.py and verify the tests.", '
                    '"should_use_resolved_objective": true, "confidence": 0.87, '
                    '"reason": "task context classified the short turn as a new concrete task"}'
                ),
                model="fake-model",
            )
        return LLMResponse(content="done", model="fake-model")


class AmbiguousBoundaryDecisionProvider(CapturingProvider):
    """Returns an ambiguous boundary decision before the main assistant response."""

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        if _is_task_contract_planner_call(messages):
            return _task_contract_response(messages)
        self.calls.append(list(messages))
        system_text = str(getattr(messages[0], "content", "") or "") if messages else ""
        if "You classify whether the latest user turn inherits task context" in system_text:
            return LLMResponse(
                content=(
                    '{"continuation_type": "new_task", "is_follow_up": false, '
                    '"should_inherit_active_task": false, '
                    '"should_seed_active_task": true, "should_replace_active_task": true, '
                    '"inherited_task_type": null, "inherited_tool_group": null, '
                    '"confidence": 0.72, "reason": "might be a new README task"}'
                ),
                model="fake-model",
            )
        return LLMResponse(content="done", model="fake-model")


class BoundaryConfirmationProvider(CapturingProvider):
    """Returns task-boundary confirmation and objective JSON before the main response."""

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        if _is_task_contract_planner_call(messages):
            return _task_contract_response(messages)
        self.calls.append(list(messages))
        system_text = str(getattr(messages[0], "content", "") or "") if messages else ""
        if "You classify whether the latest user turn inherits task context" in system_text:
            return LLMResponse(
                content=(
                    '{"continuation_type": "task_switch", "is_follow_up": false, '
                    '"should_inherit_active_task": false, '
                    '"should_seed_active_task": true, "should_replace_active_task": true, '
                    '"inherited_task_type": null, "inherited_tool_group": null, '
                    '"confidence": 0.92, "reason": "user confirmed the pending task-boundary request"}'
                ),
                model="fake-model",
            )
        if "You resolve a concise task objective for ACTIVE_TASK" in system_text:
            return LLMResponse(
                content=(
                    '{"resolved_objective": "please update README", '
                    '"should_use_resolved_objective": true, "confidence": 0.9, '
                    '"reason": "active task boundary prompt contains the pending request"}'
                ),
                model="fake-model",
            )
        return LLMResponse(content="done", model="fake-model")


class TaskObjectiveDecisionProvider(CapturingProvider):
    """Returns task-context and objective JSON decisions before the main response."""

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        if _is_task_contract_planner_call(messages):
            return _task_contract_response(messages)
        self.calls.append(list(messages))
        system_text = str(getattr(messages[0], "content", "") or "") if messages else ""
        if "You classify whether the latest user turn inherits task context" in system_text:
            return LLMResponse(
                content=(
                    '{"continuation_type": "follow_up", "is_follow_up": true, '
                    '"should_inherit_active_task": false, '
                    '"should_seed_active_task": false, "should_replace_active_task": false, '
                    '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
                    '"confidence": 0.88, "reason": "The short turn refers to the prior ETF lookup."}'
                ),
                model="fake-model",
            )
        if "You resolve a concise task objective for ACTIVE_TASK" in system_text:
            return LLMResponse(
                content=(
                    '{"resolved_objective": '
                    '"Research 00981T ETF price and basic public information using web sources.", '
                    '"should_use_resolved_objective": true, "confidence": 0.88, '
                    '"reason": "The short turn refers to the prior ETF lookup."}'
                ),
                model="fake-model",
            )
        return LLMResponse(content="done", model="fake-model")


class HistoryRetrievalContextProvider(CapturingProvider):
    """Returns a task-context decision that asks for prior chat retrieval."""

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        if _is_task_contract_planner_call(messages):
            return _task_contract_response(messages)
        self.calls.append(list(messages))
        system_text = str(getattr(messages[0], "content", "") or "") if messages else ""
        if "You classify whether the latest user turn inherits task context" in system_text:
            return LLMResponse(
                content=(
                    '{"continuation_type": "continue_recent_context", "is_follow_up": true, '
                    '"should_inherit_active_task": false, "should_seed_active_task": false, '
                    '"should_replace_active_task": false, "inherited_task_type": "history_retrieval", '
                    '"inherited_tool_group": "history_retrieval", "confidence": 0.91, '
                    '"reason": "latest message asks about prior conversation context"}'
                ),
                model="fake-model",
            )
        if "You resolve a concise task objective for ACTIVE_TASK" in system_text:
            return LLMResponse(
                content=(
                    '{"resolved_objective": "Summarize the previous cleanup fix from chat history.", '
                    '"should_use_resolved_objective": true, "confidence": 0.86, '
                    '"reason": "context resolver identified a history follow-up"}'
                ),
                model="fake-model",
            )
        return LLMResponse(content="done", model="fake-model")


def _is_task_contract_planner_call(messages) -> bool:
    system_text = str(getattr(messages[0], "content", "") or "") if messages else ""
    return "OpenSprite task-contract planner" in system_text


def _task_contract_response(messages) -> LLMResponse:
    prompt_text = "\n".join(str(getattr(message, "content", "") or "") for message in messages)
    prompt_lower = prompt_text.lower()
    if "please update readme" in prompt_lower or "tests/test_app.py" in prompt_text or "refactor the agent" in prompt_lower:
        content = (
            '{"task_type":"workspace_change","required_tool_groups":["workspace_read","workspace_write"],'
            '"final_answer_required":true,"allow_no_tool_final":false,"reason":"test planner workspace change"}'
        )
    else:
        content = (
            '{"task_type":"pure_answer","required_tool_groups":[],"final_answer_required":true,'
            '"allow_no_tool_final":true,"reason":"test planner contract"}'
        )
    return LLMResponse(
        content=content,
        model="fake-model",
    )


class _MinimalTool(Tool):
    """Single dummy tool so AgentLoop does not register the full default tool set."""

    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "noop"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs):
        return "ok"


class _MinimalMCPTool(Tool):
    @property
    def name(self) -> str:
        return "mcp_demo_echo"

    @property
    def description(self) -> str:
        return "Echo text through demo MCP"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs):
        return "ok"


class _EmptyStorage:
    async def get_messages(self, session_id, limit=None):
        return []

    async def add_message(self, session_id, message: StoredMessage):
        return None

    async def clear_messages(self, session_id):
        return None

    async def get_consolidated_index(self, session_id):
        return 0

    async def set_consolidated_index(self, session_id, index):
        return None

    async def get_all_sessions(self):
        return []


class _HistoryStorage(_EmptyStorage):
    def __init__(self, messages):
        self.messages = list(messages)

    async def get_messages(self, session_id, limit=None):
        if limit is None:
            return list(self.messages)
        return list(self.messages[-limit:])


class _FakeSearchStore:
    async def sync_from_storage(self, storage):
        return None

    async def index_message(self, session_id, role, content, tool_name=None, created_at=None):
        return None

    async def search_history(self, session_id, query, limit=5):
        return [
            SearchHit(
                id="history-1",
                session_id=session_id,
                source_type="history",
                content="Earlier we fixed the failing cleanup path in src/cleanup.py.",
                created_at=1.0,
                role="assistant",
            )
        ]

    async def clear_session(self, session_id):
        return None


def test_main_agent_call_llm_passes_full_file_builder_system_prompt_to_provider(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = CapturingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            session_id,
            "hello from integration test",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    assert len(provider.calls) == 1

    llm_messages = provider.calls[0]
    assert llm_messages[0].role == "system"
    system_text = llm_messages[0].content
    assert isinstance(system_text, str)

    expected = context_builder.build_system_prompt(session_id)
    assert system_text == expected

    assert "You are OpenSprite" in system_text
    assert "# Session Context" in system_text
    assert "# Retrieval Strategy" in system_text
    assert "Do not end a turn with a promise of future action" in system_text
    assert "When the user says things like \"earlier\", \"before\", \"again\"" in system_text
    assert "When the conversation has been compacted, treat the compacted state as a handoff" in system_text
    assert "For command or program version questions, run the direct version command" in system_text
    assert "# MCP Configuration" in system_text
    assert "prefer using `configure_mcp` instead of telling the user to edit config files manually" in system_text
    assert "# Available Subagents" in system_text
    assert "Use `delegate` when a focused subproblem would benefit from a dedicated prompt." in system_text
    assert "\n\n---\n\n" in system_text


def test_main_agent_system_prompt_lists_connected_mcp_tools(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())
    registry.register(_MinimalMCPTool())

    provider = CapturingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            session_id,
            "show me available mcp tools",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    system_text = provider.calls[0][0].content
    assert "# MCP Configuration" in system_text
    assert "Use `configure_mcp` first for MCP setup or changes." in system_text
    assert "# Available MCP Tools" in system_text
    assert "These MCP tools are already connected and available through normal tool calling." in system_text
    assert "`mcp_demo_echo`: Echo text through demo MCP" in system_text


def test_main_agent_call_llm_does_not_seed_active_task_from_task_intent_only(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = CapturingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"
    message = "Refactor the agent in small safe steps and keep it on task."
    task_intent = TaskIntentService().classify(message)

    async def _run() -> str:
        return await agent.call_llm(
            session_id,
            message,
            channel="telegram",
            allow_tools=False,
            task_intent=task_intent,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    system_text = provider.calls[0][0].content
    assert "# Active Task" not in system_text
    task_store = create_active_task_store(app_home, session_id, workspace_root=context_builder.tool_workspace)
    assert task_store.read_status() == "inactive"


def test_main_agent_call_llm_does_not_replace_active_task_without_context_decision(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = CapturingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"

    async def _run() -> str:
        task_store = create_active_task_store(app_home, session_id, workspace_root=context_builder.tool_workspace)
        task_store.write_managed_block(
            "- Status: active\n"
            "- Goal: Refactor the agent in small safe steps.\n"
            "- Deliverable: a safe refactor and verification\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - minimal changes\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: 1. inspect\n"
            "- Next step: 1. inspect\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        )
        return await agent.call_llm(
            session_id,
            "改成先幫我檢查 MCP lifecycle",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    system_text = provider.calls[0][0].content
    assert "Goal: Refactor the agent in small safe steps." in system_text
    assert "Goal: 改成先幫我檢查 MCP lifecycle" not in system_text


def test_main_agent_call_llm_uses_task_context_decision_to_replace_active_task(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = TaskContextDecisionProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"
    message = "好，現在請直接修掉 tests/test_app.py 的問題"

    async def _run() -> str:
        task_store = create_active_task_store(app_home, session_id, workspace_root=context_builder.tool_workspace)
        task_store.write_managed_block(
            "- Status: active\n"
            "- Goal: Refactor the agent in small safe steps.\n"
            "- Deliverable: a safe refactor and verification\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - minimal changes\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: 1. inspect\n"
            "- Next step: not set\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        )
        return await agent.call_llm(
            session_id,
            message,
            channel="telegram",
            allow_tools=False,
            task_intent=agent.task_intents.classify(message),
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    assert len(provider.calls) == 3
    assert "You resolve a concise task objective for ACTIVE_TASK" in provider.calls[1][0].content
    system_text = provider.calls[-1][0].content
    assert "Goal: Fix tests/test_app.py and verify the tests." in system_text


def test_main_agent_call_llm_marks_ambiguous_boundary_waiting_user(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = AmbiguousBoundaryDecisionProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"
    message = "please update README"

    async def _run() -> str:
        task_store = create_active_task_store(app_home, session_id, workspace_root=context_builder.tool_workspace)
        task_store.write_managed_block(
            "- Status: active\n"
            "- Goal: Refactor the agent in small safe steps.\n"
            "- Deliverable: a safe refactor and verification\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - minimal changes\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: 1. inspect\n"
            "- Next step: not set\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        )
        return await agent.call_llm(
            session_id,
            message,
            channel="telegram",
            allow_tools=False,
            task_intent=agent.task_intents.classify(message),
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    assert len(provider.calls) == 2
    system_text = provider.calls[-1][0].content
    assert "Status: waiting_user" in system_text
    assert "Goal: Refactor the agent in small safe steps." in system_text
    assert "Reply `switch` to replace the active task" in system_text
    assert "`continue` to keep the active task" in system_text
    assert message in system_text
    assert f"Goal: {message}" not in system_text


def test_main_agent_call_llm_switches_to_confirmed_boundary_request(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = BoundaryConfirmationProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"

    async def _run() -> str:
        task_store = create_active_task_store(app_home, session_id, workspace_root=context_builder.tool_workspace)
        task_store.write_managed_block(
            "- Status: waiting_user\n"
            "- Goal: Refactor the agent in small safe steps.\n"
            "- Deliverable: a safe refactor and verification\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - minimal changes\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: 1. inspect\n"
            "- Next step: not set\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - Reply `switch` to replace the active task (Refactor the agent in small safe steps.) "
            "with the new request (please update README), or `continue` to keep the active task."
        )
        task_store.append_event(
            TASK_BOUNDARY_CONFIRMATION_EVENT,
            "immediate",
            details={"pending_request": "please update README", "confidence": 0.72},
        )
        return await agent.call_llm(
            session_id,
            "switch",
            channel="telegram",
            allow_tools=False,
            task_intent=agent.task_intents.classify("switch"),
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    assert result.task_contract is not None
    assert result.task_contract.task_type == "code_change"
    assert any(requirement.kind == "file_change" for requirement in result.task_contract.requirements)
    assert len(provider.calls) == 3
    final_messages = provider.calls[-1]
    system_text = final_messages[0].content
    prompt_text = "\n".join(str(getattr(message, "content", "") or "") for message in final_messages)
    assert "Status: active" in system_text
    assert "Goal: please update README" in system_text
    assert "Original user message: switch" in system_text
    assert "Reply `switch` to replace the active task" not in system_text
    assert "Resolved task objective: please update README" in prompt_text
    assert "Record at least one workspace file change" in prompt_text


def test_main_agent_call_llm_uses_enriched_objective_for_short_follow_up(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = TaskObjectiveDecisionProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_HistoryStorage(
            [
                {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
                {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
            ]
        ),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"
    message = "那00981t呢"

    async def _run() -> str:
        return await agent.call_llm(
            session_id,
            message,
            channel="telegram",
            allow_tools=False,
            task_intent=agent.task_intents.classify(message),
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    assert any("You resolve a concise task objective for ACTIVE_TASK" in call[0].content for call in provider.calls)
    system_text = provider.calls[-1][0].content
    assert "Goal: Research 00981T ETF price and basic public information using web sources." in system_text
    assert "Original user message: 那00981t呢" in system_text


def test_main_agent_call_llm_does_not_seed_active_task_for_plain_question(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = CapturingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            session_id,
            "你覺得這樣可以嗎？",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    system_text = provider.calls[0][0].content
    assert "# Active Task" not in system_text


def test_main_agent_call_llm_does_not_inject_retrieval_context_without_task_context_decision(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = CapturingProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_EmptyStorage(),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_store=_FakeSearchStore(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    session_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            session_id,
            "Use the earlier fix again and compare it to what you found before.",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    llm_messages = provider.calls[0]
    assert not any(
        getattr(message, "role", None) == "system"
        and "# Proactive Retrieval Context" in str(message.content or "")
        for message in llm_messages
    )


def test_main_agent_call_llm_uses_task_context_decision_for_proactive_retrieval(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    context_builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )

    registry = ToolRegistry()
    registry.register(_MinimalTool())

    provider = HistoryRetrievalContextProvider()
    agent = AgentLoop(
        config=Config.load_agent_template_config(),
        provider=provider,
        storage=_HistoryStorage(
            [
                {"role": "user", "content": "We fixed the cleanup flow."},
                {"role": "assistant", "content": "The cleanup fix touched src/cleanup.py."},
            ]
        ),
        context_builder=context_builder,
        tools=registry,
        memory_config=MemoryConfig(**Config.load_template_data()["memory"]),
        tools_config=ToolsConfig(),
        log_config=LogConfig(log_system_prompt=False),
        search_store=_FakeSearchStore(),
        search_config=SearchConfig(),
        user_profile_config=UserProfileConfig(**{**Config.load_template_data()["user_profile"], "enabled": False}),
        **Config.packaged_agent_llm_chat_kwargs(),
    )

    async def _run() -> str:
        return await agent.call_llm(
            "telegram:room-1",
            "and this one?",
            channel="telegram",
            allow_tools=False,
            task_intent=agent.task_intents.classify("and this one?"),
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    llm_messages = provider.calls[-1]
    proactive_context = next(
        message.content
        for message in llm_messages
        if getattr(message, "role", None) == "system" and "# Proactive Retrieval Context" in str(message.content or "")
    )
    assert "## Retrieved History" in proactive_context
    assert "src/cleanup.py" in proactive_context


def test_effective_task_intent_keeps_existing_structure_for_resolved_objective() -> None:
    intent = TaskIntentService().classify("那這個呢")
    decision = TaskObjectiveDecision(
        original_message="那這個呢",
        resolved_objective="Research 00981T ETF price and cite current sources.",
        should_use_resolved_objective=True,
        confidence=0.89,
        method="llm",
        reason="follow-up objective was resolved from task context",
    )

    effective = _effective_task_intent(intent, decision)

    assert effective is not None
    assert effective.objective == "Research 00981T ETF price and cite current sources."
    assert effective.kind == intent.kind
