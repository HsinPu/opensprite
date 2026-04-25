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
from opensprite.documents.active_task import create_active_task_store
from opensprite.llms.base import LLMResponse
from opensprite.storage.base import StoredMessage
from opensprite.tools.base import Tool
from opensprite.tools.registry import ToolRegistry


class CapturingProvider:
    """Records chat() messages so tests can inspect the full prompt sent to the model."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append(list(messages))
        return LLMResponse(content="done", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


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
    async def get_messages(self, chat_id, limit=None):
        return []

    async def add_message(self, chat_id, message: StoredMessage):
        return None

    async def clear_messages(self, chat_id):
        return None

    async def get_consolidated_index(self, chat_id):
        return 0

    async def set_consolidated_index(self, chat_id, index):
        return None

    async def get_all_chats(self):
        return []


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

    chat_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            chat_id,
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

    expected = context_builder.build_system_prompt(chat_id)
    assert system_text == expected

    assert "You are OpenSprite" in system_text
    assert "# Session Context" in system_text
    assert "# Retrieval Strategy" in system_text
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

    chat_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            chat_id,
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


def test_main_agent_call_llm_seeds_active_task_on_first_turn(tmp_path: Path) -> None:
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

    chat_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            chat_id,
            "Refactor the agent in small safe steps and keep it on task.",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    system_text = provider.calls[0][0].content
    assert "# Active Task" in system_text
    assert "# Active Task Execution Rules" in system_text
    assert "Goal: Refactor the agent in small safe steps and keep it on task." in system_text
    assert "Primary focus for this turn: 1. inspect the relevant context and refine the task if needed" in system_text
    assert "Current step: 1. inspect the relevant context and refine the task if needed" in system_text


def test_main_agent_call_llm_replaces_active_task_when_user_explicitly_switches(tmp_path: Path) -> None:
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

    chat_id = "telegram:room-1"

    async def _run() -> str:
        task_store = create_active_task_store(app_home, chat_id, workspace_root=context_builder.tool_workspace)
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
            chat_id,
            "改成先幫我檢查 MCP lifecycle",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    system_text = provider.calls[0][0].content
    assert "Goal: 改成先幫我檢查 MCP lifecycle" in system_text


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

    chat_id = "telegram:room-1"

    async def _run() -> str:
        return await agent.call_llm(
            chat_id,
            "你覺得這樣可以嗎？",
            channel="telegram",
            allow_tools=False,
        )

    result = asyncio.run(_run())

    assert result.content == "done"
    system_text = provider.calls[0][0].content
    assert "# Active Task" not in system_text
